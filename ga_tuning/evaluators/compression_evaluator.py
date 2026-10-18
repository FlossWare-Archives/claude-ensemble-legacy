#!/usr/bin/env python3
"""
Compression GA Evaluator

Evaluates compression parameters:
- compression_level (0-5): How aggressively to compress
- target_reduction (0.2-0.7): Target token reduction percentage

Fitness = token_savings % * semantic_preservation_score
Constraint: semantic_similarity > 0.85 (preserve meaning)

Uses actual RH memory files for testing.
"""

import os
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import json

try:
    import tiktoken
except ImportError:
    tiktoken = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CompressionEvaluator:
    """Evaluate compression parameters on RH memory files"""

    def __init__(self, rh_memory_dir: Path):
        self.rh_memory_dir = Path(rh_memory_dir)
        self.synthetic_corpus = False
        self.test_files = self._load_test_files()
        self.tokenizer = tiktoken.get_encoding("cl100k_base") if tiktoken else None

        if not self.test_files:
            logger.warning("No test files found in RH memory directory")

        logger.info(f"Loaded {len(self.test_files)} test files for compression evaluation")

    def _load_test_files(self) -> List[Tuple[str, str]]:
        """Load RH memory files for testing"""
        test_files = []

        if not self.rh_memory_dir.exists():
            logger.warning("Memory directory not found: %s; using a synthetic fixture corpus", self.rh_memory_dir)
            self.synthetic_corpus = True
            return self._synthetic_test_files()

        # Load markdown files
        for md_file in self.rh_memory_dir.glob('*.md'):
            try:
                with open(md_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if len(content) > 100:  # Skip very small files
                        test_files.append((md_file.name, content))
            except Exception as e:
                logger.error(f"Error loading {md_file}: {e}")

        if not test_files:
            logger.warning("No usable Markdown files found; using a synthetic fixture corpus")
            self.synthetic_corpus = True
            return self._synthetic_test_files()
        return test_files

    @staticmethod
    def _synthetic_test_files() -> List[Tuple[str, str]]:
        """Small deterministic corpus used only when no operational corpus is available."""
        common = (
            "Service memory request state configuration validation recovery reliability "
            "service memory request state configuration validation recovery reliability"
        )
        return [
            ("architecture.md", common + ". " + common + ". " + common + ". " + common + "."),
            ("testing.md", common + ". " + common + ". " + common + ". " + common + "."),
            ("operations.md", common + ". " + common + ". " + common + ". " + common + "."),
            ("memory.md", common + ". " + common + ". " + common + ". " + common + "."),
        ]

    def _count_tokens(self, text: str) -> int:
        """Count tokens using tiktoken"""
        if self.tokenizer:
            return len(self.tokenizer.encode(text))
        else:
            # Fallback: approximate with word count
            return len(text.split())

    def _compress_text(self, text: str, compression_level: float) -> str:
        """
        Compress text based on compression level.
        0 = no compression
        5 = maximum compression
        """
        # Split into sentences
        sentences = text.replace('\n', ' ').split('. ')
        sentences = [s.strip() + '.' if s.strip() else s for s in sentences]

        if len(sentences) < 2:
            return text

        # Compression strategy: remove lower-importance sentences
        # Approximate importance by sentence length and position
        n_keep = max(1, int(len(sentences) * (1.0 - (compression_level / 10.0))))

        # Keep first sentence, last sentence, and longest sentences
        if n_keep >= len(sentences):
            return text

        importance_scores = []
        for i, sent in enumerate(sentences):
            # Favor beginning and end, longer sentences
            position_score = 1.0 - abs(i - len(sentences) / 2) / len(sentences)
            length_score = min(1.0, len(sent) / 100.0)
            importance = (0.4 * position_score + 0.6 * length_score)
            importance_scores.append((i, importance))

        # Keep top-scoring sentences
        kept_indices = sorted([i for i, _ in sorted(importance_scores, key=lambda x: x[1], reverse=True)[:n_keep]])

        compressed = ' '.join([sentences[i] for i in kept_indices])
        return compressed

    def _semantic_similarity(self, original: str, compressed: str) -> float:
        """Calculate cosine similarity between original and compressed text"""
        if not original or not compressed:
            return 0.0

        try:
            # Use TF-IDF vectorizer
            vectorizer = TfidfVectorizer(max_features=100, lowercase=True, stop_words='english')

            # Need at least 2 documents
            texts = [original, compressed]
            tfidf_matrix = vectorizer.fit_transform(texts)

            # Calculate cosine similarity
            similarity = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
            return float(similarity)

        except Exception as e:
            logger.debug(f"Error calculating similarity: {e}")
            # Fallback: basic overlap
            orig_words = set(original.lower().split())
            comp_words = set(compressed.lower().split())
            overlap = len(orig_words & comp_words) / max(len(orig_words), len(comp_words))
            return overlap

    def evaluate(self, parameters: Dict[str, float]) -> float:
        """
        Evaluate compression parameters on test files.
        Returns fitness score: token_savings % * semantic_preservation
        """
        compression_level = parameters['compression_level']
        target_reduction = parameters['target_reduction']

        if not self.test_files:
            return 0.0

        total_original_tokens = 0
        total_compressed_tokens = 0
        similarity_scores = []

        for filename, content in self.test_files:
            # Compress text
            compressed = self._compress_text(content, compression_level)

            # Count tokens
            original_tokens = self._count_tokens(content)
            compressed_tokens = self._count_tokens(compressed)

            total_original_tokens += original_tokens
            total_compressed_tokens += compressed_tokens

            # Calculate semantic similarity
            similarity = self._semantic_similarity(content, compressed)
            similarity_scores.append(similarity)

        # Calculate metrics
        if total_original_tokens == 0:
            return 0.0

        actual_reduction = (total_original_tokens - total_compressed_tokens) / total_original_tokens
        avg_similarity = np.mean(similarity_scores)

        # Penalize if semantic similarity too low
        if avg_similarity < 0.85:
            penalty = (0.85 - avg_similarity) * 2.0
            avg_similarity = max(0.0, avg_similarity - penalty)

        # Fitness = token_savings * semantic_preservation
        # Reward if we hit target reduction
        reduction_bonus = 1.0
        if abs(actual_reduction - target_reduction) < 0.1:
            reduction_bonus = 1.2  # 20% bonus for hitting target

        fitness = actual_reduction * avg_similarity * reduction_bonus

        logger.debug(
            f"Compression: level={compression_level:.2f}, target={target_reduction:.2f} -> "
            f"actual_reduction={actual_reduction:.2%}, similarity={avg_similarity:.4f}, fitness={fitness:.6f}"
        )

        return max(0.0, min(1.0, fitness))  # Clamp to [0, 1]


if __name__ == '__main__':
    # Test evaluator
    rh_memory_dir = Path(os.environ.get('CE_MEMORY_DIR', Path.home() / 'Development/redhat/scm/gitlab/cee/sfloess/claude-global-skills/memory')).expanduser()
    evaluator = CompressionEvaluator(rh_memory_dir)

    # Test parameters
    test_params = {
        'compression_level': 2.0,
        'target_reduction': 0.3,
    }

    fitness = evaluator.evaluate(test_params)
    print(f"Test fitness: {fitness:.6f}")
