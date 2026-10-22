#!/usr/bin/env python3
"""
RH Memory Service Daemon

Central memory authority for all Claude Code sessions.
Runs as systemd user service, listens on a private Unix socket.
Handles concurrent access, memory operations, and path validation.
"""

import hashlib
import json
import logging
import math
import os
import re
import socket
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow execution from the repository without requiring package installation.
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from execution.context import ExecutionContext
from shared.runtime_config import log_dir, memory_dir, runtime_dir, socket_path
from hybrid_retrieval import HybridRetrievalIndex, chunk_markdown


RUNTIME_SUBDIR = "claude-ensemble"
SOCKET_FILENAME = "memory.sock"
MEMORY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def get_runtime_dir() -> Path:
    """Return and harden the configured runtime directory."""
    configured = os.environ.get("ENSEMBLE_RUNTIME_DIR")
    if configured:
        runtime_dir = Path(configured).expanduser()
    else:
        xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        base_dir = Path(xdg_runtime_dir) if xdg_runtime_dir else Path.home() / ".cache"
        runtime_dir = base_dir / RUNTIME_SUBDIR
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(runtime_dir, 0o700)
    return runtime_dir


def get_socket_path() -> Path:
    """Return the private memory service socket path."""
    return get_runtime_dir() / SOCKET_FILENAME


def validate_memory_name(name: str) -> str:
    """Validate a memory name before using it as part of a filesystem path."""
    if not isinstance(name, str) or not MEMORY_NAME_PATTERN.fullmatch(name) or name in (
        "..",
        ".",
    ):
        raise ValueError(
            "Invalid memory name: use only letters, numbers, '.', '_' and '-'; not '.' or '..'"
        )
    return name


MEMORY_DIR = memory_dir()
SOCKET_PATH = socket_path(
    "ENSEMBLE_MEMORY_SOCKET", str(runtime_dir() / "memory.sock")
)
LOG_DIR = log_dir()
LOG_DIR.mkdir(parents=True, exist_ok=True)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "claude-memory.log"),
    ],
)
logger = logging.getLogger(__name__)


class MemoryStore:
    """Thread-safe memory file operations."""

    def __init__(self, memory_dir: Path, chunk_size: int = 500):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.chunk_size = chunk_size
        self._hybrid_index = HybridRetrievalIndex(self.memory_dir / ".hybrid-retrieval.sqlite3")
        # Search index keyed by file name. Each entry is validated against the
        # file's (mtime_ns, size) on every search, so external edits are seen.
        self._search_index: Dict[str, Tuple[Tuple[int, int], List[Dict[str, Any]]]] = {}
        self._search_index_lock = threading.Lock()
        self._pending_index_lock = threading.Lock()
        self._pending_index_path = self.memory_dir / ".hybrid-index-pending.json"
        # A failed ledger write must still fail readiness closed for this process.
        # Entries are revision-specific and disappear only after that revision is
        # confirmed present in the persistent hybrid index.
        self._pending_index_faults: Dict[str, Dict[str, str]] = {}
        # Per-file locks make cache population single-flight without serializing
        # independent files. Locks are retained for deleted names to avoid a
        # second lock being created while an older builder is still active.
        self._file_index_locks: Dict[str, threading.Lock] = {}

    def _memory_path(self, name: str, suffix: str) -> Path:
        """Build a validated memory path."""
        validate_memory_name(name)
        path = self.memory_dir / f"{name}{suffix}"
        resolved_root = self.memory_dir.resolve()
        resolved_path = path.resolve()
        if resolved_path.parent != resolved_root:
            raise ValueError("Memory path escapes the configured memory directory")
        return path

    def read_file(self, name: str) -> Optional[str]:
        """Read a memory file."""
        path = self._memory_path(name, ".md")
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                return f.read()
        except OSError as e:
            logger.error(f"Error reading {name}: {e}")
            raise

    def write_file(self, name: str, content: str) -> bool:
        """Write a memory file."""
        path = self._memory_path(name, ".md")
        try:
            with self.lock:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
            # Keep the persistent hybrid index current at write time. If indexing
            # fails, retain a durable retry record and fail readiness closed.
            self._index_written_file(path)
            logger.info(f"Wrote memory: {name}")
            return True
        except Exception as e:
            logger.error(f"Error writing {name}: {e}")
            return False

    def _pending_index_records(self) -> Dict[str, Dict[str, str]]:
        """Read the durable retry ledger for files not yet reflected in SQLite."""
        try:
            value = json.loads(self._pending_index_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Cannot read hybrid-index retry ledger: %s", exc)
            return {"__ledger_error__": {"error": str(exc)}}
        return value if isinstance(value, dict) else {
            "__ledger_error__": {"error": "retry ledger is not a JSON object"}
        }

    def pending_index_status(self) -> Dict[str, Any]:
        with self._pending_index_lock:
            records = self._pending_index_records()
            # In-memory faults are authoritative if persistence failed or a
            # concurrent ledger update has not yet become durable.
            records.update(self._pending_index_faults)
        return {
            "count": len(records),
            "files": sorted(name for name in records if name != "__ledger_error__"),
            "errors": {name: record.get("error", "indexing pending")
                       for name, record in records.items()},
        }

    def _write_pending_index_records(self, records: Dict[str, Dict[str, str]]) -> None:
        temporary = self._pending_index_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self._pending_index_path)

    def _mark_index_pending(self, path: Path, error: Exception) -> None:
        try:
            signature = repr(self._file_signature(path.stat())) if path.exists() else "missing"
        except OSError:
            signature = "unknown"
        record = {"signature": signature, "error": str(error)}
        with self._pending_index_lock:
            self._pending_index_faults[path.stem] = record
            try:
                records = self._pending_index_records()
                records[path.stem] = record
                self._write_pending_index_records(records)
                self._pending_index_faults.pop("__ledger_error__", None)
            except OSError as ledger_error:
                # Keep a durable-write/index failure visible even if its retry
                # ledger cannot be written. Readiness must not silently recover.
                self._pending_index_faults["__ledger_error__"] = {
                    "error": f"Could not persist retry ledger: {ledger_error}"
                }
                logger.exception("Could not persist hybrid-index retry record for %s: %s", path, ledger_error)

    def _clear_index_pending(self, file_name: str, signature: str | None = None) -> bool:
        # Successful indexing of revision A must never clear pending revision B.
        # For a missing-file cleanup, callers omit signature and we only clear
        # after confirming the source file no longer exists.
        path = self.memory_dir / f"{file_name}.md"
        if signature is None:
            if path.exists():
                return False
        elif not self._hybrid_index.document_revision_matches(file_name, signature):
            return False

        with self._pending_index_lock:
            records = self._pending_index_records()
            record = records.get(file_name)
            fault = self._pending_index_faults.get(file_name)
            if signature is not None:
                if record and record.get("signature") not in (None, signature):
                    return False
                if fault and fault.get("signature") not in (None, signature):
                    return False
            records.pop(file_name, None)
            self._pending_index_faults.pop(file_name, None)
            try:
                # Do not clear a global ledger fault merely because one file
                # recovered. A complete corpus reconciliation is required to
                # prove no failed ledger write hid pending work for another file.
                self._write_pending_index_records(records)
            except OSError as ledger_error:
                self._pending_index_faults["__ledger_error__"] = {
                    "error": f"Could not update retry ledger: {ledger_error}"
                }
                logger.exception("Could not clear hybrid-index retry record for %s: %s", file_name, ledger_error)
                return False
            return True

    def _clear_ledger_fault_after_reconciliation(self, present: Dict[str, Path]) -> None:
        """Clear a lost-ledger-write fault only after every live file is indexed."""
        if "__ledger_error__" not in self._pending_index_faults:
            return
        for name, path in present.items():
            try:
                signature = repr(self._file_signature(path.stat()))
            except OSError:
                return
            if not self._hybrid_index.document_revision_matches(Path(name).stem, signature):
                return
        with self._pending_index_lock:
            records = self._pending_index_records()
            if any(name != "__ledger_error__" for name in records):
                return
            try:
                self._write_pending_index_records({})
            except OSError as ledger_error:
                self._pending_index_faults["__ledger_error__"] = {
                    "error": f"Could not reconcile retry ledger: {ledger_error}"
                }
                logger.exception("Could not reconcile hybrid-index retry ledger: %s", ledger_error)
                return
            self._pending_index_faults.pop("__ledger_error__", None)

    def _index_written_file(self, path: Path) -> bool:
        """Index a durable Markdown write or record explicit deferred work."""
        try:
            self._indexed_chunks(path)
            # The file may have changed while indexing was in progress. Only
            # report success if SQLite contains the exact latest durable revision.
            signature = repr(self._file_signature(path.stat()))
            if not self._hybrid_index.document_revision_matches(path.stem, signature):
                raise RuntimeError("file changed during hybrid indexing; retry required")
            present_stems = {item.stem for item in self.memory_dir.glob("*.md") if item.is_file()}
            if (
                self._hybrid_index._full_corpus_key is None
                or present_stems != set(self._hybrid_index._full_corpus_key)
            ):
                self._hybrid_index.warm_corpus(present_stems)
            if not self._clear_index_pending(path.stem, signature):
                logger.warning("Memory %s is indexed but pending-index bookkeeping is not reconciled", path.stem)
                return False
            return True
        except Exception as index_error:
            self._mark_index_pending(path, index_error)
            logger.warning("Memory %s was written but indexing is pending: %s", path.stem, index_error)
            return False

    def append_entry(self, name: str, entry: Dict[str, Any]) -> bool:
        """Append entry to a memory file (JSONL style)."""
        path = self._memory_path(name, ".jsonl")
        try:
            with self.lock:
                with open(path, "a") as f:
                    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
                    f.write(json.dumps(entry) + "\n")
            logger.info(f"Appended to {name}")
            return True
        except Exception as e:
            logger.error(f"Error appending to {name}: {e}")
            return False

    def append_entry_once(
        self, name: str, event_id: str, entry: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Append one event exactly once, rejecting key reuse with different content.

        The idempotency check and append share the same lock. The JSONL file is
        the durable source of truth, so retries remain safe after service restart.
        """
        self._memory_path(name, ".jsonl")
        if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > 256:
            raise ValueError("event_id must be a non-empty string of at most 256 characters")
        if not isinstance(entry, dict) or not entry:
            raise ValueError("entry must be a non-empty JSON object")
        if "event_id" in entry and entry["event_id"] != event_id:
            raise ValueError("entry.event_id must match event_id")
        reserved_fields = {"payload_sha256", "captured_at"}
        collisions = reserved_fields.intersection(entry)
        if collisions:
            field = sorted(collisions)[0]
            raise ValueError(f"entry.{field} is reserved for Memory service metadata")

        record = dict(entry)
        record["event_id"] = event_id
        payload_sha256 = hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        path = self._memory_path(name, ".jsonl")

        with self.lock:
            if path.exists():
                # JSONL framing is part of the persisted data contract. A valid
                # JSON object at EOF without a newline is still an unterminated
                # record; appending directly would concatenate two objects and
                # acknowledge a write that makes the log unreadable. Fail closed
                # without changing existing bytes.
                with path.open("rb") as framing_handle:
                    framing_handle.seek(0, os.SEEK_END)
                    if framing_handle.tell() > 0:
                        framing_handle.seek(-1, os.SEEK_END)
                        if framing_handle.read(1) != b"\n":
                            raise RuntimeError(
                                "cannot append: JSONL file has an unterminated final record"
                            )
                with path.open("r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if not line.strip():
                            continue
                        try:
                            existing = json.loads(line)
                        except json.JSONDecodeError as exc:
                            # Do not append when deduplication cannot be proven.
                            raise RuntimeError(
                                f"cannot verify idempotency: malformed record at line {line_number}"
                            ) from exc
                        if not isinstance(existing, dict):
                            raise RuntimeError(
                                f"cannot verify idempotency: record at line {line_number} is not a JSON object"
                            )
                        if existing.get("event_id") != event_id:
                            continue
                        existing_digest = existing.get("payload_sha256")
                        if existing_digest is None:
                            raise ValueError(
                                "event_id already exists without a verifiable payload digest"
                            )
                        if existing_digest == payload_sha256:
                            # A previous request may have written the row but failed
                            # during fsync. Re-establish durability before acknowledging
                            # this retry as a duplicate.
                            with path.open("a", encoding="utf-8") as sync_handle:
                                sync_handle.flush()
                                os.fsync(sync_handle.fileno())
                            return {"status": "duplicate", "event_id": event_id}
                        raise ValueError("event_id already exists with different payload")

            record["payload_sha256"] = payload_sha256
            record["captured_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            # Preserve a caller-supplied event timestamp. For legacy consumers,
            # provide timestamp only when the payload did not include one.
            record.setdefault("timestamp", record["captured_at"])
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        logger.info("Appended idempotent event %s to %s", event_id, name)
        return {"status": "stored", "event_id": event_id}

    def read_entries(self, name: str) -> List[Dict[str, Any]]:
        """Read persisted JSONL records without interpreting their semantics."""
        path = self._memory_path(name, ".jsonl")
        if not path.exists():
            return []
        entries: List[Dict[str, Any]] = []
        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except Exception as e:
            logger.error(f"Error reading entries from {name}: {e}")
        return entries

    def retrieve_entries(
        self,
        name: str,
        context: ExecutionContext,
        *,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Retrieve context-bearing records related to the supplied execution."""
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("Retrieval limit must be an integer")
        if limit < 1:
            raise ValueError("Retrieval limit must be at least 1")
        if limit > 100:
            raise ValueError("Retrieval limit must not exceed 100")

        results = []
        current_lineage = context.lineage
        current_execution_id = context.execution_id
        current_parent_id = context.parent_execution_id

        for record_index, record in enumerate(self.read_entries(name)):
            raw_context = record.get("execution_context")
            if not isinstance(raw_context, dict):
                continue
            try:
                record_context = ExecutionContext.from_dict(raw_context)
            except (KeyError, TypeError, ValueError):
                logger.debug("Skipping invalid execution context in %s", name)
                continue

            record_execution_id = record_context.execution_id
            if record_execution_id is None or record_execution_id == current_execution_id:
                continue

            record_lineage = record_context.lineage
            relation = None
            rank = 0

            is_prior_lineage = (
                record_context.request_id == context.request_id
                and len(record_lineage) < len(current_lineage)
                and current_lineage[: len(record_lineage)] == record_lineage
                and bool(record_lineage)
                and record_execution_id == record_lineage[-1]
            )
            is_descendant = (
                record_context.request_id == context.request_id
                and len(record_lineage) > len(current_lineage)
                and record_lineage[: len(current_lineage)] == current_lineage
                and bool(record_lineage)
                and record_execution_id == record_lineage[-1]
            )
            if is_descendant:
                continue

            if (
                current_parent_id is not None
                and record_execution_id == current_parent_id
                and record_context.request_id == context.request_id
            ):
                relation, rank = "parent", 100
            elif is_prior_lineage:
                relation, rank = "ancestor", 90
            elif (
                record_context.request_id == context.request_id
                and current_lineage
                and record_lineage
                and current_lineage[0] == record_lineage[0]
            ):
                relation, rank = "related-lineage", 60
            elif record_context.request_id == context.request_id:
                relation, rank = "same-request", 70

            if relation is None:
                continue

            results.append(
                {
                    "record": record,
                    "execution_context": record_context.to_dict(),
                    "relation": relation,
                    "authoritative": False,
                    "_rank": rank,
                    "_record_index": record_index,
                }
            )

        results.sort(key=lambda item: (-item["_rank"], item["_record_index"]))
        for result in results:
            result.pop("_rank", None)
            result.pop("_record_index", None)
        return results[:limit]

    def _ingest_index_path(self) -> Path:
        return self.memory_dir / ".claude-code-ingest.json"

    def _load_ingest_index(self) -> Dict[str, Any]:
        path = self._ingest_index_path()
        if not path.exists():
            return {}
        try:
            with path.open(encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            logger.warning("Ignoring invalid Claude Code ingest index")
            return {}

    def _save_ingest_index(self, index: Dict[str, Any]) -> None:
        path = self._ingest_index_path()
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(index, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)

    def ingest_claude_markdown(
        self,
        source_path: str,
        content: str,
        sha256: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Idempotently ingest a Claude Code Markdown document."""
        if not isinstance(source_path, str) or not source_path.startswith("/"):
            raise ValueError("source_path must be an absolute path")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        actual_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual_sha != sha256:
            raise ValueError("sha256 does not match content")
        metadata = dict(metadata or {})
        scope = metadata.get("scope")
        if not isinstance(scope, str) or not scope.startswith("/"):
            raise ValueError("metadata.scope must be an absolute path")
        if not Path(source_path).is_relative_to(Path(scope)):
            raise ValueError("source_path must be within metadata.scope")
        with self.lock:
            index = self._load_ingest_index()
            existing = index.get(source_path)
            document_key = (
                "claude-code-" + hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:32]
            )
            path = self._memory_path(document_key, ".md")
            if (
                isinstance(existing, dict)
                and existing.get("sha256") == sha256
                and not existing.get("stale", False)
                and path.exists()
            ):
                try:
                    stored_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    stored_sha = None
                if stored_sha == sha256:
                    # An identical ingest retry must also retry any failed index
                    # work; durable Markdown alone does not imply retrievability.
                    self._index_written_file(path)
                    return {"status": "unchanged", "source_path": source_path, "sha256": sha256}
            with path.open("w", encoding="utf-8") as handle:
                handle.write(content)
            index[source_path] = {
                "document": document_key,
                "source": "claude-code",
                "scope": scope,
                "sha256": sha256,
                "source_path": source_path,
                "metadata": metadata,
                "stale": False,
            }
            self._save_ingest_index(index)
        # This work happens on the ingestion/write path, never during prompt search.
        self._index_written_file(path)
        logger.info("Ingested Claude Code memory: %s", source_path)
        return {"status": "ingested", "source_path": source_path, "sha256": sha256, "document": document_key}

    def reconcile_claude_markdown(
        self, source: str, scope: str, paths: List[str]
    ) -> Dict[str, Any]:
        """Mark missing Claude Code sources stale within one synchronization scope."""
        if source != "claude-code":
            raise ValueError("unsupported reconciliation source")
        if not isinstance(scope, str) or not scope.startswith("/"):
            raise ValueError("scope must be an absolute path")
        current = set(paths)
        with self.lock:
            index = self._load_ingest_index()
            stale = []
            for source_path, entry in index.items():
                if (
                    not isinstance(entry, dict)
                    or entry.get("source") not in (None, source)
                    or entry.get("scope") != scope
                ):
                    continue
                if source_path not in current and not entry.get("stale", False):
                    entry["stale"] = True
                    stale.append(source_path)
            self._save_ingest_index(index)
        return {"status": "reconciled", "stale": stale, "count": len(stale)}

    def list_claude_ingest(self, include_stale: bool = False) -> Dict[str, Any]:
        index = self._load_ingest_index()
        if include_stale:
            return index
        return {path: entry for path, entry in index.items() if not entry.get("stale", False)}

    def _active_ingest_entry(self, document: str) -> Optional[Dict[str, Any]]:
        for entry in self._load_ingest_index().values():
            if isinstance(entry, dict) and entry.get("document") == document:
                return entry if not entry.get("stale", False) else None
        return None

    def knowledge_status(self) -> Dict[str, Any]:
        """Report indexed Knowledge availability without exposing source paths or content."""
        index = self._load_ingest_index()
        active_documents = set()
        for entry in index.values():
            if not isinstance(entry, dict) or entry.get("stale", False):
                continue
            document = entry.get("document")
            if not isinstance(document, str) or not document.startswith("claude-code-"):
                continue
            try:
                path = self._memory_path(document, ".md")
            except ValueError:
                continue
            if path.is_file():
                active_documents.add(document)
        count = len(active_documents)
        return {
            "source": "claude-code",
            "status": "available" if count else "empty",
            "indexed_document_count": count,
        }

    def list_files(self) -> List[str]:
        """List all memory files."""
        return [f.stem for f in self.memory_dir.glob("*.md")]

    def vectorize_text(self, text: str) -> Dict[str, float]:
        """Convert text to a local bag-of-words vector without external API calls.

        Keep punctuation inside structured identifiers (for example UUIDs,
        event IDs, and dotted artifact types) so the query and stored document
        use the same token representation.
        """
        terms = []
        normalized_text = text.casefold()
        # Unicode letters and digits are retained; separators remain inside
        # compound identifiers such as UUIDs and dotted artifact types.
        for token in re.findall(
            r"[^\W_]+(?:[._:-][^\W_]+)*", normalized_text, re.UNICODE
        ):
            if len(token) > 2:
                terms.append(token)
            # Preserve compound identifiers as a whole and also index their
            # components, so "learning artifact" can match "learning.artifact".
            terms.extend(
                part
                for part in re.split(r"[._:-]+", token)
                if len(part) > 2
            )
        term_freq = Counter(terms)
        doc_length = len(terms)
        return {
            term: freq / max(doc_length, 1)
            for term, freq in term_freq.items()
        }

    def cosine_similarity(
        self, vec1: Dict[str, float], vec2: Dict[str, float]
    ) -> float:
        """Calculate cosine similarity between two vectors."""
        all_terms = set(vec1.keys()) | set(vec2.keys())
        if not all_terms:
            return 0.0
        dot_product = sum(
            vec1.get(term, 0) * vec2.get(term, 0) for term in all_terms
        )
        mag1 = math.sqrt(sum(v**2 for v in vec1.values()))
        mag2 = math.sqrt(sum(v**2 for v in vec2.values()))
        if mag1 == 0 or mag2 == 0:
            return 0.0
        return dot_product / (mag1 * mag2)

    @staticmethod
    def _file_signature(stat: os.stat_result) -> Tuple[int, int, int, int]:
        """Revision key for the search index.

        mtime and size alone miss a same-size rewrite whose mtime was restored
        (for example `cp -p`, `rsync -t`, or `os.utime`). ctime cannot be set
        by user code and the inode changes on replace-by-rename, so both are
        included. Limit: a same-size rewrite inside one filesystem timestamp
        tick still produces an identical signature; restarting the service
        or any later write to the file refreshes it.
        """
        return (stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns, stat.st_ino)

    def _indexed_chunks(self, md_file: Path, *, update_hybrid: bool = True) -> List[Dict[str, Any]]:
        """Return cached chunks, updating SQLite only during warm-up or writes.

        Exact-identifier fallback may refresh the in-memory lexical cache, but
        passes update_hybrid=False so prompt-time searches never rewrite vectors.
        """
        file_name = md_file.stem
        signature = self._file_signature(md_file.stat())
        with self._search_index_lock:
            cached = self._search_index.get(file_name)
            indexed_cached = cached[1] if cached is not None and cached[0] == signature else None
            file_lock = self._file_index_locks.setdefault(file_name, threading.Lock())

        if indexed_cached is not None:
            if update_hybrid:
                if not self._hybrid_index.document_revision_matches(file_name, repr(signature)):
                    self._hybrid_index.replace_document(file_name, repr(signature), indexed_cached)
                self._clear_index_pending(file_name, repr(signature))
            return indexed_cached

        # Recheck after acquiring the per-file lock: another search or the
        # background warm-up may have populated this revision while we waited.
        with file_lock:
            try:
                stat = md_file.stat()
            except FileNotFoundError:
                with self._search_index_lock:
                    self._search_index.pop(file_name, None)
                return []
            signature = self._file_signature(stat)
            with self._search_index_lock:
                cached = self._search_index.get(file_name)
                indexed_cached = cached[1] if cached is not None and cached[0] == signature else None
            if indexed_cached is not None:
                if update_hybrid:
                    if not self._hybrid_index.document_revision_matches(file_name, repr(signature)):
                        self._hybrid_index.replace_document(file_name, repr(signature), indexed_cached)
                    self._clear_index_pending(file_name, repr(signature))
                return indexed_cached
            # The signature is taken BEFORE the file is read. If a writer
            # changes the file in between, the entry holds newer content under
            # an older signature, so the next search sees a different signature
            # and rebuilds. The unsafe order (stat after read) is never used.
            chunks = self.chunk_document(file_name)
            if not chunks:
                with open(md_file, "r", encoding="utf-8") as handle:
                    chunks = [{"content": handle.read(), "header": "full"}]
            indexed = []
            for chunk in chunks:
                content = chunk.get("content", "")
                vector = self.vectorize_text(content)
                indexed.append(
                    {
                        "header": chunk.get("header", ""),
                        "heading_path": chunk.get("heading_path", chunk.get("header", "")),
                        "content": content,
                        "size_bytes": len(content.encode("utf-8")),
                        "folded": content.casefold(),
                        "vector": vector,
                        "magnitude": math.sqrt(sum(v**2 for v in vector.values())),
                    }
                )
            if update_hybrid:
                self._hybrid_index.replace_document(file_name, repr(signature), indexed)
            with self._search_index_lock:
                self._search_index[file_name] = (signature, indexed)
            if update_hybrid:
                self._clear_index_pending(file_name, repr(signature))
            return indexed

    def _prune_search_index(self, present: set) -> None:
        with self._search_index_lock:
            for name in [n for n in self._search_index if n not in present]:
                del self._search_index[name]

    def warm_search_index_report(self, max_passes: int = 3) -> Dict[str, Any]:
        """Index a stable file set and report per-file outcomes.

        A pass succeeds only when every file in its initial snapshot indexed
        successfully and the directory membership did not change during the
        pass. This avoids treating a coincidental file count as proof that the
        intended corpus was indexed.
        """
        last_report: Dict[str, Any] = {}
        for pass_number in range(1, max_passes + 1):
            initial = {
                path.name: path
                for path in sorted(self.memory_dir.glob("*.md"))
                if path.is_file()
            }
            indexed: list[str] = []
            failures: Dict[str, str] = {}
            for name, path in initial.items():
                try:
                    self._indexed_chunks(path)
                    indexed.append(name)
                except Exception as exc:
                    failures[name] = str(exc)
                    logger.warning("Error indexing %s: %s", path, exc)
            final_names = {
                path.name for path in self.memory_dir.glob("*.md") if path.is_file()
            }
            added = sorted(final_names - set(initial))
            removed = sorted(set(initial) - final_names)
            stable = not added and not removed
            if stable and not failures:
                self._clear_ledger_fault_after_reconciliation(initial)
            present_stems = {Path(name).stem for name in final_names}
            for pending_name in self.pending_index_status()["files"]:
                if pending_name not in present_stems:
                    self._clear_index_pending(pending_name)
            pending_status = self.pending_index_status()
            complete = (
                stable and not failures and len(indexed) == len(initial)
                and pending_status["count"] == 0
            )
            last_report = {
                "pass": pass_number,
                "expected_files": sorted(initial),
                "indexed_files": sorted(indexed),
                "failed_files": failures,
                "added_files": added,
                "removed_files": removed,
                "pending_index_files": pending_status["files"],
                "pending_index_errors": pending_status["errors"],
                "complete": complete,
            }
            if complete:
                present_stems = {Path(name).stem for name in final_names}
                self._hybrid_index.remove_missing(present_stems)
                self._prune_search_index(present_stems)
                # Populate the SQLite-backed corpus cache before readiness flips
                # to ready; the first prompt must not pay the cold corpus scan.
                self._hybrid_index.warm_corpus(present_stems)
                return last_report
        return last_report

    def warm_search_index(self) -> int:
        """Build the search index for every file; returns the file count."""
        report = self.warm_search_index_report()
        indexed_files = {Path(name).stem for name in report.get("indexed_files", [])}
        if report.get("complete"):
            self._hybrid_index.warm_corpus(indexed_files)
        return len(indexed_files)

    def search_semantic(self, query: str, top_k: int = 10, source: Optional[str] = None) -> List[Dict[str, Any]]:
        """Search using SQLite hybrid retrieval for prose and exact matching for identifiers.

        Document chunking and embedding writes happen during warm-up or file
        writes, never on this prompt-time search path. Exact identifiers such
        as run IDs and event IDs retain their boundary-aware lexical path.
        """
        if not isinstance(query, str) or not query.strip():
            return []
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            return []
        if source not in (None, "memory", "claude-code"):
            return []

        # Strip terminal prose punctuation before deciding whether a query
        # is a structured key. Keep internal separators intact.
        normalized_query = query.strip().casefold().rstrip(".,!?;")
        if not normalized_query:
            return []
        query_vector = self.vectorize_text(normalized_query)
        if not query_vector:
            return []
        # Treat UUIDs, event IDs, and machine-like IDs containing digits plus
        # separators as keys. Dotted artifact types are keys when they have
        # multiple components; ordinary hyphenated prose remains searchable.
        is_uuid = bool(
            re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                normalized_query,
            )
        )
        has_separator = bool(re.search(r"[-._:]", normalized_query))
        identifier_query = (
            not any(character.isspace() for character in normalized_query)
            and (
                is_uuid
                or ":" in normalized_query
                or (has_separator and bool(re.search(r"\d", normalized_query)))
                or normalized_query.count(".") >= 2
            )
        )
        # Structured keys require identifier boundaries. A run ID may occur
        # inside "learning-artifact:<type>:<run-id>", but must not match as a
        # prefix of a longer ID or as a substring of another identifier.
        identifier_pattern = re.compile(
            rf"(?<![\w.-]){re.escape(normalized_query)}(?![\w.-])",
            re.UNICODE,
        )

        if not identifier_query:
            ingest_index = self._load_ingest_index() if source != "memory" else {}
            active_knowledge_documents = {
                entry.get("document")
                for entry in ingest_index.values()
                if isinstance(entry, dict)
                and not entry.get("stale", False)
                and isinstance(entry.get("document"), str)
                and entry.get("document", "").startswith("claude-code-")
            }
            allowed_files = set()
            for md_file in self.memory_dir.glob("*.md"):
                file_name = md_file.stem
                is_knowledge = file_name.startswith("claude-code-")
                if source == "claude-code" and not is_knowledge:
                    continue
                if source == "memory" and is_knowledge:
                    continue
                if is_knowledge and file_name not in active_knowledge_documents:
                    continue
                allowed_files.add(file_name)
            return self._hybrid_index.search(normalized_query, top_k, allowed_files)

        # Load the ingestion index once per search, not once per Knowledge file.
        # _active_ingest_entry() reads and parses the entire JSON index on every
        # call; invoking it inside this loop turns a search into repeated disk I/O
        # and makes concurrent prompt-time searches miss their hook timeout.
        ingest_index = self._load_ingest_index() if source != "memory" else {}
        active_knowledge_documents = {
            entry.get("document")
            for entry in ingest_index.values()
            if isinstance(entry, dict)
            and not entry.get("stale", False)
            and isinstance(entry.get("document"), str)
            and entry.get("document", "").startswith("claude-code-")
        }

        query_magnitude = math.sqrt(sum(v**2 for v in query_vector.values()))
        results = []
        present = set()
        for md_file in self.memory_dir.glob("*.md"):
            try:
                file_name = md_file.stem
                present.add(file_name)
                is_knowledge = file_name.startswith("claude-code-")
                if source == "claude-code" and not is_knowledge:
                    continue
                if source == "memory" and is_knowledge:
                    continue
                if is_knowledge and file_name not in active_knowledge_documents:
                    continue
                for chunk in self._indexed_chunks(md_file, update_hybrid=False):
                    normalized_content = chunk["folded"]
                    exact_match = (
                        bool(identifier_pattern.search(normalized_content))
                        if identifier_query
                        else normalized_query in normalized_content
                    )
                    if identifier_query and not exact_match:
                        continue
                    if exact_match:
                        similarity = 1.0
                    else:
                        chunk_vector = chunk["vector"]
                        # Terms absent from the query contribute zero to the dot product.
                        dot_product = sum(
                            weight * chunk_vector.get(term, 0)
                            for term, weight in query_vector.items()
                        )
                        denominator = query_magnitude * chunk["magnitude"]
                        similarity = dot_product / denominator if denominator else 0.0
                    if exact_match or similarity > 0.1:
                        results.append(
                            {
                                "file": file_name,
                                "section": chunk["header"],
                                "score": similarity,
                                "size_bytes": chunk["size_bytes"],
                            }
                        )
            except Exception as e:
                logger.debug("Error searching %s: %s", md_file, e)
        self._prune_search_index(present)
        return sorted(
            results,
            key=lambda item: (-item["score"], item["file"], item["section"]),
        )[:top_k]

    def chunk_document(self, name: str) -> List[Dict[str, Any]]:
        """Chunk Markdown by heading hierarchy and paragraph boundaries."""
        file_path = self._memory_path(name, ".md")
        if not file_path.exists():
            return []
        try:
            return chunk_markdown(file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Error chunking %s: %s", name, exc)
            return []

    def search(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """Search memory files using TF-IDF + keyword matching on chunks."""
        results = []
        keyword_set = {kw.lower() for kw in keywords}
        all_docs = []
        for md_file in self.memory_dir.glob("*.md"):
            try:
                with open(md_file, "r") as f:
                    content = f.read().lower()
                    terms = set(
                        word.strip(".,!?;:")
                        for word in content.split()
                        if len(word) > 2
                    )
                    all_docs.append(terms)
            except Exception:
                pass
        doc_count = len(all_docs)
        idf_scores = {}
        for kw in keyword_set:
            docs_with_kw = sum(1 for doc in all_docs if kw in doc)
            idf_scores[kw] = math.log(doc_count / docs_with_kw) if docs_with_kw else 0
        for md_file in self.memory_dir.glob("*.md"):
            try:
                file_name = md_file.stem
                if file_name.startswith("claude-code-") and self._active_ingest_entry(file_name) is None:
                    continue
                chunks = self.chunk_document(file_name)
                if not chunks:
                    with open(md_file, "r") as f:
                        content = f.read()
                    chunks = [{"content": content, "header": "full"}]
                for chunk in chunks:
                    content = chunk.get("content", "").lower()
                    header = chunk.get("header", "")
                    terms = [
                        word.strip(".,!?;:").lower()
                        for word in content.split()
                        if len(word) > 2
                    ]
                    term_freq = Counter(terms)
                    doc_length = len(terms)
                    score = 0
                    matched_kw = []
                    for kw in keyword_set:
                        if kw in term_freq:
                            tf = term_freq[kw] / max(doc_length, 1)
                            score += tf * idf_scores.get(kw, 0)
                            matched_kw.append(kw)
                    if score > 0:
                        results.append(
                            {
                                "file": file_name,
                                "section": header,
                                "score": score,
                                "matched_keywords": len(matched_kw),
                                "size_bytes": len(content),
                            }
                        )
            except Exception as e:
                logger.debug(f"Error searching {md_file}: {e}")
        return sorted(results, key=lambda x: x["score"], reverse=True)[:10]


class MemoryHTTPHandler(BaseHTTPRequestHandler):
    server_version = "ClaudeEnsembleMemory/1"

    def _send(self, status: int, payload: Dict[str, Any]) -> None:
        body = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 16 * 1024 * 1024:
            raise ValueError("request body must be between 1 byte and 16 MiB")
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(value, dict): raise ValueError("request body must be a JSON object")
        return value

    def do_GET(self) -> None:
        service = self.server.memory_service
        if self.path == "/health":
            # Liveness must not depend on index warm-up.
            self._send(200, {"ok": True, "service": "memory"})
            return
        if self.path == "/ready":
            status = service.search_index_status()
            ready = status["state"] == "ready"
            self._send(
                200 if ready else 503,
                {"ok": ready, "service": "memory", "ready": ready, "search_index": status},
            )
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        try:
            body=self._body()
            operations={"/memory/read":"read","/memory/write":"write","/memory/append":"append",
                        "/memory/append-once":"append_once",
                        "/memory/entries":"entries","/memory/retrieve":"retrieve","/memory/list":"list",
                        "/memory/search":"search_semantic","/memory/search-semantic":"search_semantic",
                        "/memory/search-hybrid":"search_hybrid","/memory/chunk":"chunk",
                        "/memory/ingest":"ingest_claude_markdown","/memory/reconcile":"reconcile_claude_markdown",
                        "/memory/knowledge/status":"knowledge_status"}
            operation=operations.get(self.path)
            if operation is None: self._send(404,{"ok":False,"error":"not found"}); return
            if operation in {"search_semantic", "search_hybrid"}:
                index_status = self.server.memory_service.search_index_status()
                index_state = index_status["state"]
                if index_state != "ready":
                    retryable = index_state in {"warming", "timed_out"}
                    self._send(
                        503,
                        {
                            "ok": False,
                            "error": "memory search index is not ready",
                            "error_code": "index_not_ready",
                            "retryable": retryable,
                            "search_index": index_status,
                        },
                    )
                    return
            body["op"]=operation
            if operation == "append_once":
                try:
                    result = self.server.memory_service.store.append_entry_once(
                        body.get("name"), body.get("event_id"), body.get("entry", {})
                    )
                except ValueError as exc:
                    status = 409 if "event_id already exists" in str(exc) else 400
                    self._send(status, {"ok": False, "error": str(exc)})
                    return
                except RuntimeError as exc:
                    self._send(500, {"ok": False, "error": str(exc)})
                    return
                self._send(200, {"ok": True, **result})
                return
            requested_source = None
            # REST callers use `limit`; `top_k` is the MemoryStore name and is
            # accepted as an alias. It was previously overwritten silently.
            requested_limit = body.get("limit", body.get("top_k", 10))
            if operation=="search_semantic":
                requested_source = body.get("source")
                if requested_source not in (None, "memory", "claude-code"):
                    raise ValueError("source must be 'memory' or 'claude-code'")
                # The source filter is applied before ranking in MemoryStore,
                # so unrelated Memory cannot crowd durable Knowledge out.
                body["top_k"] = requested_limit
            if operation=="ingest_claude_markdown":
                result = self.server.memory_service.store.ingest_claude_markdown(
                    body.get("path", ""), body.get("content", ""), body.get("sha256", ""), body.get("metadata", {})
                )
                self._send(200, {"ok": True, **result})
                return
            if operation=="reconcile_claude_markdown":
                result = self.server.memory_service.store.reconcile_claude_markdown(
                    body.get("source", ""), body.get("scope", ""), body.get("paths", [])
                )
                self._send(200, {"ok": True, **result})
                return
            if operation=="knowledge_status":
                result = self.server.memory_service.store.knowledge_status()
                self._send(200, {"ok": True, **result})
                return
            result=json.loads(self.server.memory_service._process_request(json.dumps(body)))
            if operation=="search_semantic" and result.get("ok"):
                enriched=[]
                for item in result.get("results",[]):
                    item=dict(item)
                    is_knowledge = str(item.get("file", "")).startswith("claude-code-")
                    if requested_source == "claude-code" and not is_knowledge:
                        continue
                    if requested_source == "memory" and is_knowledge:
                        continue
                    content=self.server.memory_service.store.read_file(item["file"])
                    if content is not None:
                        item["content"]=content
                        if is_knowledge:
                            item["source"]="claude-code"
                    enriched.append(item)
                result["results"]=enriched[:requested_limit]
            if result.get("ok") is False:
                if result.get("error_type") == "validation":
                    status = 400
                elif result.get("error_type") == "not_found":
                    status = 404
                elif result.get("error_type") == "internal":
                    status = 500
                else:
                    status = 500
            else:
                status = 200
            self._send(status, result)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            logger.exception("Memory REST request failed")
            self._send(500,{"ok":False,"error":str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None: return


def create_http_server(service: "MemoryService", host: str, port: int) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1","::1","localhost"}: raise ValueError("Memory HTTP service only accepts loopback binds")
    server=ThreadingHTTPServer((host,port),MemoryHTTPHandler)
    server.memory_service=service
    return server


class MemoryService:
    """Memory service daemon."""

    def __init__(self, socket_path: Path, memory_dir: Path):
        self.socket_path = Path(socket_path)
        self.store = MemoryStore(memory_dir)
        self.socket = None
        self.http_server = None
        self.http_thread = None
        self.http_port = 0
        self._index_status_lock = threading.Lock()
        self._index_state = "not_started"
        self._index_started_at: float | None = None
        self._index_completed_at: float | None = None
        self._index_expected_files = 0
        self._index_indexed_files = 0
        self._index_error: str | None = None
        self._index_timeout_logged = False
        try:
            self._index_warmup_timeout_seconds = float(
                os.environ.get("ENSEMBLE_MEMORY_WARMUP_TIMEOUT_SECONDS", "30")
            )
            if (
                not math.isfinite(self._index_warmup_timeout_seconds)
                or self._index_warmup_timeout_seconds <= 0
            ):
                raise ValueError
        except ValueError as exc:
            raise ValueError(
                "ENSEMBLE_MEMORY_WARMUP_TIMEOUT_SECONDS must be a finite positive number"
            ) from exc

    def search_index_status(self) -> Dict[str, Any]:
        """Return an explicit, bounded startup state for the search index."""
        with self._index_status_lock:
            state = self._index_state
            started_at = self._index_started_at
            completed_at = self._index_completed_at
            expected = self._index_expected_files
            indexed = self._index_indexed_files
            error = self._index_error
        elapsed = (
            max(0.0, (completed_at or time.monotonic()) - started_at)
            if started_at is not None
            else 0.0
        )
        if state == "warming" and elapsed >= self._index_warmup_timeout_seconds:
            state = "timed_out"
            error = (
                "search index warm-up exceeded "
                f"{self._index_warmup_timeout_seconds:g} seconds"
            )
            should_log = False
            with self._index_status_lock:
                if not self._index_timeout_logged:
                    self._index_timeout_logged = True
                    should_log = True
            if should_log:
                logger.warning("%s", error)
        pending = self.store.pending_index_status()
        if pending["count"] and state == "ready":
            state = "degraded"
            error = (
                f"{pending['count']} document(s) have durable pending hybrid-index work; "
                "retry the write/ingest or restart Memory to retry indexing"
            )
        return {
            "state": state,
            "indexed_files": indexed,
            "expected_files": expected,
            "pending_index_files": pending["files"],
            "pending_index_count": pending["count"],
            "pending_index_errors": pending["errors"],
            "elapsed_seconds": round(elapsed, 3),
            "warmup_timeout_seconds": self._index_warmup_timeout_seconds,
            **({"error": error} if error else {}),
        }

    def _prepare_socket_path(self) -> None:
        """Create a private parent directory and safely remove a stale socket."""
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.socket_path.parent, 0o700)

        if not self.socket_path.exists():
            return

        socket_stat = self.socket_path.stat()
        if socket_stat.st_uid != os.getuid() or not stat.S_ISSOCK(socket_stat.st_mode):
            raise RuntimeError(
                f"Refusing to remove unexpected socket path: {self.socket_path}"
            )
        self.socket_path.unlink()

    def _warm_search_index(self) -> None:
        """Warm the index with bounded retries; never rebuild it on a prompt request."""
        started_at = time.monotonic()
        with self._index_status_lock:
            self._index_state = "warming"
            self._index_started_at = self._index_started_at or started_at
            self._index_completed_at = None
            # The report supplies the exact per-pass file set and outcomes.
            # Do not take a separate directory-count snapshot for readiness.
            self._index_expected_files = 0
            self._index_indexed_files = 0
            self._index_error = None
            self._index_timeout_logged = False

        last_report: Dict[str, Any] = {}
        for attempt in range(1, 4):
            try:
                report = self.store.warm_search_index_report(max_passes=1)
                last_report = report
                indexed = len(report.get("indexed_files", []))
                expected = len(report.get("expected_files", []))
                with self._index_status_lock:
                    self._index_expected_files = expected
                    self._index_indexed_files = indexed
                    self._index_error = None if report.get("complete") else (
                        "search index pass incomplete: "
                        f"failed={report.get('failed_files', {})}, "
                        f"added={report.get('added_files', [])}, "
                        f"removed={report.get('removed_files', [])}"
                    )
                if report.get("complete"):
                    with self._index_status_lock:
                        self._index_completed_at = time.monotonic()
                        self._index_state = "ready"
                        self._index_error = None
                    logger.info("Search index warmed for %d memory files", indexed)
                    return
                logger.warning(
                    "Memory search-index warm-up attempt %d/3 incomplete: %s",
                    attempt,
                    self._index_error,
                )
            except Exception as exc:
                with self._index_status_lock:
                    self._index_error = str(exc)
                logger.exception("Memory search-index warm-up attempt %d/3 failed", attempt)

            if attempt < 3:
                time.sleep(0.25 * attempt)

        with self._index_status_lock:
            self._index_completed_at = time.monotonic()
            self._index_state = "degraded" if last_report else "failed"
            if not self._index_error:
                self._index_error = "search index warm-up failed after 3 attempts"
        logger.error(
            "Memory search index remains not-ready after bounded retries: %s",
            self._index_error,
        )

    def start(self):
        """Start the service."""
        logger.info("Starting Claude Ensemble memory service")
        self._prepare_socket_path()

        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous_umask = os.umask(0o077)
        try:
            self.socket.bind(str(self.socket_path))
        finally:
            os.umask(previous_umask)

        os.chmod(self.socket_path, 0o600)
        self.socket.listen(5)
        self.socket.settimeout(None)

        logger.info(f"Listening on {self.socket_path}")

        # Mark the index as warming before exposing HTTP so prompt-time
        # searches cannot slip through the startup window and rebuild the
        # corpus on demand.
        with self._index_status_lock:
            self._index_state = "warming"
            self._index_started_at = time.monotonic()
            self._index_error = None
            self._index_timeout_logged = False

        http_host=os.environ.get("ENSEMBLE_MEMORY_HTTP_HOST","127.0.0.1")
        http_port=int(os.environ.get("ENSEMBLE_MEMORY_HTTP_PORT","8767"))
        self.http_server=create_http_server(self,http_host,http_port)
        self.http_port=self.http_server.server_port
        self.http_thread=threading.Thread(target=self.http_server.serve_forever,daemon=True)
        self.http_thread.start()
        logger.info(f"Memory REST service listening on http://{http_host}:{self.http_port}")
        # Build the search index off the request path so the first prompt-time
        # search after a restart does not pay the full corpus cost.
        threading.Thread(target=self._warm_search_index, daemon=True).start()

        try:
            while True:
                conn, _ = self.socket.accept()
                thread = threading.Thread(
                    target=self._handle_client, args=(conn,), daemon=True
                )
                thread.start()
        except KeyboardInterrupt:
            logger.info("Shutting down")
            self.stop()
        except Exception as e:
            logger.error(f"Service error: {e}")
            self.stop()

    def stop(self):
        """Stop the service."""
        if self.http_server:
            self.http_server.shutdown()
            self.http_server.server_close()
            self.http_server=None
            self.http_port=0
        if self.socket:
            self.socket.close()
        if self.socket_path.exists():
            try:
                socket_stat = self.socket_path.stat()
                if (
                    socket_stat.st_uid == os.getuid()
                    and stat.S_ISSOCK(socket_stat.st_mode)
                ):
                    self.socket_path.unlink()
            except FileNotFoundError:
                pass
        logger.info("Memory service stopped")

    def _handle_client(self, conn: socket.socket):
        """Handle client request."""
        try:
            conn.settimeout(5.0)
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

            request_str = data.decode("utf-8").strip()
            if not request_str:
                return

            response = self._process_request(request_str)
            conn.sendall((response + "\n").encode("utf-8"))
        except socket.timeout:
            logger.debug("Client timeout")
            try:
                conn.sendall(b'{"ok": false, "error": "timeout"}\n')
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Client error: {e}")
            try:
                conn.sendall(b'{"ok": false, "error": "server error"}\n')
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _process_request(self, request: str) -> str:
        """Process a request, return JSON response."""
        try:
            req_data = json.loads(request)
            operation = req_data.get("op")

            if operation == "read":
                name = req_data.get("name")
                content = self.store.read_file(name)
                if content is None:
                    return json.dumps({
                        "ok": False,
                        "content": None,
                        "error": "memory not found",
                        "error_type": "not_found",
                    })
                return json.dumps({"ok": True, "content": content})

            if operation == "write":
                name = req_data.get("name")
                content = req_data.get("content")
                success = self.store.write_file(name, content)
                if not success:
                    return json.dumps({
                        "ok": False,
                        "error": "memory write failed",
                        "error_type": "internal",
                    })
                return json.dumps({"ok": True})

            if operation == "append":
                name = req_data.get("name")
                entry = req_data.get("entry", {})
                success = self.store.append_entry(name, entry)
                return json.dumps({"ok": success})

            if operation == "append_once":
                name = req_data.get("name")
                event_id = req_data.get("event_id")
                entry = req_data.get("entry", {})
                result = self.store.append_entry_once(name, event_id, entry)
                return json.dumps({"ok": True, **result})

            if operation == "entries":
                name = req_data.get("name")
                validate_memory_name(name)
                entries = self.store.read_entries(name)
                return json.dumps({"ok": True, "entries": entries})

            if operation == "retrieve":
                name = req_data.get("name")
                validate_memory_name(name)
                context = ExecutionContext.from_dict(req_data.get("context", {}))
                limit = req_data.get("limit", 10)
                results = self.store.retrieve_entries(name, context, limit=limit)
                return json.dumps({"ok": True, "results": results})

            if operation == "list":
                return json.dumps({"ok": True, "files": self.store.list_files()})

            if operation == "search":
                keywords = req_data.get("keywords", [])
                results = self.store.search(keywords)
                return json.dumps({"ok": True, "results": results})

            if operation == "chunk":
                name = req_data.get("name")
                chunks = self.store.chunk_document(name)
                return json.dumps({"ok": True, "chunks": chunks})

            if operation == "search_semantic":
                query = req_data.get("query", "")
                top_k = req_data.get("top_k", 10)
                source = req_data.get("source")
                results = self.store.search_semantic(query, top_k, source)
                return json.dumps({"ok": True, "results": results})

            if operation == "search_hybrid":
                query = req_data.get("query", "")
                keywords = query.lower().split()
                top_k = req_data.get("top_k", 10)
                keyword_results = self.store.search(keywords)
                semantic_results = self.store.search_semantic(query, top_k)
                merged = {}
                for result in keyword_results:
                    key = (result["file"], result.get("section", "full"))
                    merged.setdefault(key, {"keyword_score": 0, "semantic_score": 0})
                    merged[key]["keyword_score"] = result["score"]
                for result in semantic_results:
                    key = (result["file"], result.get("section", "full"))
                    merged.setdefault(key, {"keyword_score": 0, "semantic_score": 0})
                    merged[key]["semantic_score"] = result["score"]
                results = []
                for (file, section), scores in merged.items():
                    combined_score = (
                        0.4 * scores["keyword_score"] + 0.6 * scores["semantic_score"]
                    )
                    results.append(
                        {
                            "file": file,
                            "section": section,
                            "score": combined_score,
                            "keyword_score": scores["keyword_score"],
                            "semantic_score": scores["semantic_score"],
                        }
                    )
                results = sorted(results, key=lambda x: x["score"], reverse=True)[:top_k]
                return json.dumps({"ok": True, "results": results})

            if operation == "ping":
                return json.dumps({"ok": True, "message": "pong"})

            return json.dumps({"ok": False, "error": f"Unknown operation: {operation}"})
        except json.JSONDecodeError:
            return json.dumps({"ok": False, "error": "Invalid JSON", "error_type": "validation"})
        except (ValueError, TypeError, KeyError) as e:
            return json.dumps({"ok": False, "error": str(e), "error_type": "validation"})
        except Exception as e:
            logger.error(f"Request error: {e}")
            return json.dumps({"ok": False, "error": str(e), "error_type": "internal"})


if __name__ == "__main__":
    service = MemoryService(SOCKET_PATH, MEMORY_DIR)
    service.start()
