#!/usr/bin/env python3
"""Prompt caching integration for RH memory-driven workflows.

This module detects when RH memory files are included in prompts and
automatically marks them for caching using Anthropic's cache_control API.

Features:
- Detect memory file patterns in prompts and system context
- Generate cache keys from file paths + modification times
- Structure prompts with cache_control annotations
- Support ephemeral (multi-turn) and last_message (single) cache types
- Integration hooks for session start, prompt processing, and response handling

Usage:
    from memory_cache_integration import MemoryCacheIntegrator

    integrator = MemoryCacheIntegrator()

    # Session start: auto-detect RH memory files
    memory_files = integrator.detect_session_memory_files()

    # Prompt processing: add cache_control to memory content
    structured_prompt = integrator.structure_prompt_with_caching(
        user_message="...",
        system_context="...",
        cache_type="ephemeral"  # or "last_message"
    )

    # Response handling: extract cache hit/miss from response metadata
    cache_info = integrator.extract_cache_usage_from_response(response)
"""

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Cache TTL can be overridden via environment variable (in seconds)
# Default: 5 minutes (300 seconds) - conservative, safe default
# Example: export ENSEMBLE_CACHE_TTL_SECONDS=21600  # 6 hours (for cost savings)
DEFAULT_CACHE_TTL_SECONDS = int(os.environ.get('ENSEMBLE_CACHE_TTL_SECONDS', '300'))
logger.setLevel(logging.INFO)

# Add console handler if not already present
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


@dataclass
class CacheKey:
    """Represents a cache key for a memory file.

    Uses nanosecond-precision modification time tracking to detect
    edits within the same second, preventing cache invalidation false negatives.
    """

    file_path: str
    modification_time: float  # seconds since epoch (high precision)
    content_hash: str
    file_size: int
    stat_info_ns: Optional[int] = None  # Nanoseconds for precise change detection (Python 3.3+)

    def to_hash(self) -> str:
        """Convert cache key to a reproducible hash.

        Uses nanosecond precision to catch rapid file changes that occur
        within the same second, preventing cache invalidation false negatives.
        """
        key_data = json.dumps({
            'path': self.file_path,
            'mtime': self.modification_time,
            'mtime_ns': self.stat_info_ns,  # Include nanosecond precision
            'hash': self.content_hash,
            'size': self.file_size
        }, sort_keys=True)
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]


@dataclass
class CacheableBlock:
    """Represents a block of content marked for caching.

    Cache blocks are automatically invalidated after the configured TTL
    (default: 5 minutes / 300 seconds) as per Anthropic's caching policy.

    For cost savings with static memory files, override with:
      export ENSEMBLE_CACHE_TTL_SECONDS=21600  (6 hours, saves ~$500/month)
    """

    content: str
    cache_type: str  # "ephemeral" or "last_message"
    source_path: Optional[str] = None
    cache_key: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    cache_ttl_seconds: int = field(default_factory=lambda: DEFAULT_CACHE_TTL_SECONDS)

    def __post_init__(self):
        """Validate cache type."""
        if self.cache_type not in ("ephemeral", "last_message"):
            raise ValueError(
                f"Invalid cache_type '{self.cache_type}'. "
                "Must be 'ephemeral' or 'last_message'."
            )

    def is_expired(self) -> bool:
        """Check if cache block has exceeded TTL."""
        return (time.time() - self.created_at) > self.cache_ttl_seconds

    def time_until_expiration(self) -> float:
        """Return seconds until cache expires. Returns 0 if expired."""
        remaining = self.cache_ttl_seconds - (time.time() - self.created_at)
        return max(0, remaining)


class MemoryCacheIntegrator:
    """Detects and structures prompts for Anthropic prompt caching.

    Attributes:
        memory_dir: Root directory for RH memory files (default: CE_MEMORY_DIR, legacy RH path, then ~/.claude/memory)
        cache_control_version: API version for cache_control (default: "ephemeral")
        max_cached_file_size: Max file size to cache (default: 100KB)
    """

    # Pattern to detect RH memory file paths
    RH_MEMORY_PATTERN = re.compile(
        r'/redhat/scm/gitlab/.*/memory/.*\.md',
        re.IGNORECASE
    )

    # Expanded pattern to catch variations in home directory expansion
    MEMORY_FILE_PATTERN = re.compile(
        r'(?:feedback|reference|project|session|learning)_[\w\-]+\.md',
        re.IGNORECASE
    )

    def __init__(
        self,
        memory_dir: Optional[str] = None,
        cache_control_version: str = "ephemeral",
        max_cached_file_size: int = 102400,  # 100KB
        enable_logging: bool = True
    ):
        """Initialize the cache integrator.

        Args:
            memory_dir: Root directory for RH memory files. If None, uses default.
            cache_control_version: Cache control type ("ephemeral" or "last_message").
            max_cached_file_size: Maximum file size to cache in bytes.
            enable_logging: Enable info-level logging.
        """
        self.cache_control_version = cache_control_version
        self.max_cached_file_size = max_cached_file_size
        self.enable_logging = enable_logging

        # Set default memory directory
        if memory_dir is None:
            configured_memory_dir = os.getenv("CE_MEMORY_DIR")
            if configured_memory_dir:
                self.memory_dir = str(Path(configured_memory_dir).expanduser())
            else:
                # Preserve the historical RH layout as a compatibility fallback.
                rh_path = Path.home() / "Development/redhat/scm/gitlab/cee/sfloess/claude-global-skills/memory"
                if rh_path.exists():
                    self.memory_dir = str(rh_path)
                else:
                    self.memory_dir = str(Path.home() / ".claude" / "memory")
        else:
            self.memory_dir = memory_dir

        # Cache for file metadata to avoid repeated disk I/O
        self._cache_key_cache: Dict[str, CacheKey] = {}

        logger.info(f"MemoryCacheIntegrator initialized with memory_dir={self.memory_dir}")

    def detect_memory_files_in_text(self, text: str) -> List[str]:
        """Detect memory file paths referenced in text.

        Args:
            text: Text to search for memory file references.

        Returns:
            List of absolute file paths found.
        """
        detected = set()

        # Strategy 1: Look for explicit file paths
        for match in self.RH_MEMORY_PATTERN.finditer(text):
            path = match.group(0)
            if os.path.exists(path):
                detected.add(path)

        # Strategy 2: Look for memory file names in the memory directory
        if self.memory_dir and os.path.isdir(self.memory_dir):
            for match in self.MEMORY_FILE_PATTERN.finditer(text):
                filename = match.group(0)
                full_path = os.path.join(self.memory_dir, filename)
                if os.path.exists(full_path):
                    detected.add(full_path)

        return list(detected)

    def detect_session_memory_files(self) -> List[str]:
        """Auto-detect RH memory files in the standard memory directory.

        Called at session start to identify all cacheable memory files.

        Returns:
            List of absolute file paths to memory files (.md files in memory_dir).
        """
        memory_files = []

        if not self.memory_dir or not os.path.isdir(self.memory_dir):
            logger.debug(f"Memory directory not found: {self.memory_dir}")
            return memory_files

        try:
            for filename in os.listdir(self.memory_dir):
                if filename.endswith('.md') and not filename.startswith('.'):
                    full_path = os.path.join(self.memory_dir, filename)
                    if os.path.isfile(full_path):
                        # Check file size
                        file_size = os.path.getsize(full_path)
                        if file_size > self.max_cached_file_size:
                            logger.warning(
                                f"Memory file too large for caching: {full_path} "
                                f"({file_size} bytes > {self.max_cached_file_size})"
                            )
                            continue

                        memory_files.append(full_path)
                        logger.debug(f"Detected memory file: {full_path}")
        except OSError as e:
            logger.error(f"Error scanning memory directory: {e}")

        logger.info(f"Session memory detection found {len(memory_files)} cacheable files")
        return memory_files

    def _compute_cache_key(self, file_path: str) -> Optional[CacheKey]:
        """Compute cache key for a file.

        Uses nanosecond-precision modification time to detect rapid changes
        that occur within the same second, preventing cache invalidation false negatives.

        Args:
            file_path: Absolute path to the file.

        Returns:
            CacheKey object or None if file cannot be read.
        """
        # Check cache first (but always verify mtime hasn't changed)
        if file_path in self._cache_key_cache:
            cached_key = self._cache_key_cache[file_path]
            try:
                stat_info = os.stat(file_path)
                # Check if file has been modified since we cached it
                # Use nanosecond precision to catch rapid changes
                current_mtime_ns = stat_info.st_mtime_ns
                if cached_key.stat_info_ns == current_mtime_ns:
                    return cached_key
                # File was modified, recompute
                logger.debug(f"Cache key invalidated for {file_path} (mtime changed)")
            except OSError:
                pass  # Fall through to recompute

        try:
            stat_info = os.stat(file_path)
            mtime = stat_info.st_mtime
            mtime_ns = stat_info.st_mtime_ns  # Nanosecond precision
            file_size = stat_info.st_size

            # Read file and compute content hash
            with open(file_path, 'rb') as f:
                content = f.read()
                content_hash = hashlib.sha256(content).hexdigest()

            cache_key = CacheKey(
                file_path=file_path,
                modification_time=mtime,
                content_hash=content_hash,
                file_size=file_size,
                stat_info_ns=mtime_ns  # Store nanosecond-precision mtime
            )

            # Cache the result
            self._cache_key_cache[file_path] = cache_key
            return cache_key

        except OSError as e:
            logger.error(f"Cannot compute cache key for {file_path}: {e}")
            return None

    def read_memory_file(self, file_path: str) -> Optional[str]:
        """Read a memory file's content.

        Args:
            file_path: Absolute path to the memory file.

        Returns:
            File content as string, or None on error.
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except OSError as e:
            logger.error(f"Cannot read memory file {file_path}: {e}")
            return None

    def structure_prompt_with_caching(
        self,
        user_message: str,
        system_context: Optional[str] = None,
        memory_files: Optional[List[str]] = None,
        cache_type: str = "ephemeral"
    ) -> Dict[str, Any]:
        """Structure a prompt with cache_control annotations.

        Marks memory files with cache_control for automatic caching by Anthropic.

        Returns a dict compatible with the Anthropic SDK:
        {
            "system": [
                {"type": "text", "text": "..."},
                {"type": "text", "text": "...", "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [
                {"role": "user", "content": [...]}
            ]
        }

        Args:
            user_message: The user's message content.
            system_context: Optional system prompt text.
            memory_files: Optional list of memory file paths to include.
            cache_type: Cache control type ("ephemeral" for multi-turn, "last_message" for final).

        Returns:
            Structured prompt dict with cache_control annotations.
        """
        if cache_type not in ("ephemeral", "last_message"):
            logger.warning(f"Invalid cache_type '{cache_type}', using 'ephemeral'")
            cache_type = "ephemeral"

        # Build system blocks
        system_blocks = []

        # Add base system context if provided
        if system_context:
            system_blocks.append({
                "type": "text",
                "text": system_context
            })

        # Add memory files with caching
        if memory_files is None:
            memory_files = self.detect_session_memory_files()

        cacheable_blocks: List[CacheableBlock] = []

        for file_path in memory_files:
            content = self.read_memory_file(file_path)
            if content:
                cache_key = self._compute_cache_key(file_path)
                cache_key_hash = cache_key.to_hash() if cache_key else None

                # Create cacheable block
                block = CacheableBlock(
                    content=content,
                    cache_type=cache_type,
                    source_path=file_path,
                    cache_key=cache_key_hash
                )
                cacheable_blocks.append(block)

                # Add to system blocks with cache_control
                system_blocks.append({
                    "type": "text",
                    "text": f"# Memory: {os.path.basename(file_path)}\n\n{content}",
                    "cache_control": {"type": cache_type}
                })

                logger.debug(
                    f"Added cacheable memory block: {file_path} "
                    f"(cache_key={cache_key_hash}, type={cache_type})"
                )

        # Build user message content
        user_content = [{"type": "text", "text": user_message}]

        # Build final structure
        structured = {
            "system": system_blocks,
            "messages": [
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            "_cache_metadata": {
                "cacheable_blocks": [asdict(b) for b in cacheable_blocks],
                "cache_type": cache_type,
                "created_at": time.time()
            }
        }

        logger.info(
            f"Structured prompt with {len(cacheable_blocks)} cacheable memory blocks "
            f"(type={cache_type})"
        )

        return structured

    def extract_cache_usage_from_response(
        self,
        response: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract cache hit/miss information from API response.

        The Anthropic API includes usage information in the response:
        {
            "usage": {
                "input_tokens": 1000,
                "cache_creation_input_tokens": 500,
                "cache_read_input_tokens": 200,
                "output_tokens": 50
            }
        }

        Args:
            response: The API response dict (or response.model_dump() for SDK objects).

        Returns:
            Dict with cache usage analysis:
            {
                "input_tokens": ...,
                "cache_creation_tokens": ...,
                "cache_read_tokens": ...,
                "output_tokens": ...,
                "cache_hit": bool,
                "savings_percentage": float,
                "is_cache_creation": bool
            }
        """
        usage = response.get("usage", {})

        input_tokens = usage.get("input_tokens", 0)
        cache_creation_tokens = usage.get("cache_creation_input_tokens", 0)
        cache_read_tokens = usage.get("cache_read_input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)

        # Determine cache status
        cache_hit = cache_read_tokens > 0
        is_cache_creation = cache_creation_tokens > 0

        # Calculate savings
        savings_percentage = 0.0
        if cache_read_tokens > 0:
            # Cache reads cost 10% of normal input tokens (Anthropic pricing)
            cached_cost = cache_read_tokens * 0.1
            normal_cost = cache_read_tokens  # if not cached
            savings_percentage = ((normal_cost - cached_cost) / normal_cost) * 100

        cache_usage = {
            "input_tokens": input_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cache_read_tokens": cache_read_tokens,
            "output_tokens": output_tokens,
            "cache_hit": cache_hit,
            "is_cache_creation": is_cache_creation,
            "savings_percentage": savings_percentage
        }

        # Log cache usage
        if cache_hit:
            logger.info(
                f"Cache HIT: read {cache_read_tokens} cached tokens "
                f"({savings_percentage:.1f}% savings)"
            )
        elif is_cache_creation:
            logger.info(
                f"Cache CREATION: created cache with {cache_creation_tokens} tokens"
            )
        else:
            logger.debug("No cache usage in this request")

        return cache_usage

    def get_cache_statistics(self) -> Dict[str, Any]:
        """Get statistics about the cache key cache.

        Returns:
            Dict with cache statistics:
            {
                "cached_files": int,
                "total_cached_bytes": int,
                "memory_dir": str
            }
        """
        total_bytes = sum(
            ck.file_size for ck in self._cache_key_cache.values()
        )

        stats = {
            "cached_files": len(self._cache_key_cache),
            "total_cached_bytes": total_bytes,
            "memory_dir": self.memory_dir
        }

        return stats

    def clear_cache_key_cache(self):
        """Clear the in-memory cache key cache.

        Useful when memory files change and you want to force recomputation.
        """
        self._cache_key_cache.clear()
        logger.info("Cleared cache key cache")

    def invalidate_cache_key(self, file_path: str):
        """Invalidate cache key for a specific file.

        Args:
            file_path: Path to the file whose cache should be invalidated.
        """
        if file_path in self._cache_key_cache:
            del self._cache_key_cache[file_path]
            logger.debug(f"Invalidated cache key for {file_path}")


def create_session_start_hook() -> Dict[str, Any]:
    """Create a session start hook that auto-detects memory files.

    Returns:
        Hook configuration dict for workflow harness integration.
    """
    return {
        "name": "memory_cache_session_start",
        "trigger": "on_session_start",
        "handler": "memory_cache_integration.handle_session_start",
        "config": {
            "auto_detect_memory": True,
            "cache_type": "ephemeral"
        }
    }


def create_prompt_processing_hook() -> Dict[str, Any]:
    """Create a prompt processing hook that adds cache_control.

    Returns:
        Hook configuration dict for workflow harness integration.
    """
    return {
        "name": "memory_cache_prompt_processing",
        "trigger": "on_prompt_submit",
        "handler": "memory_cache_integration.handle_prompt_processing",
        "config": {
            "enable_caching": True,
            "cache_type": "ephemeral"
        }
    }


def create_response_handler_hook() -> Dict[str, Any]:
    """Create a response handler hook that logs cache metrics.

    Returns:
        Hook configuration dict for workflow harness integration.
    """
    return {
        "name": "memory_cache_response_handler",
        "trigger": "on_response_received",
        "handler": "memory_cache_integration.handle_response_received",
        "config": {
            "track_cache_hits": True,
            "log_level": "INFO"
        }
    }
