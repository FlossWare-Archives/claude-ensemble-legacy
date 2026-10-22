"""Persistent hybrid retrieval for the CE Memory service.

SQLite is embedded storage, not a server. Local Sentence Transformers embeddings
are enabled by default; ENSEMBLE_MEMORY_EMBEDDING_MODEL can override the default model.
"""
from __future__ import annotations

import bisect
import json
import math
from contextlib import contextmanager
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"[^\W_]+(?:[._:-][^\W_]+)*", re.UNICODE)


def tokenize(text: str) -> list[str]:
    terms: list[str] = []
    for token in TOKEN_RE.findall(text.casefold()):
        if len(token) > 2:
            terms.append(token)
        terms.extend(part for part in re.split(r"[._:-]+", token) if len(part) > 2)
    return terms


def chunk_markdown(text: str, max_chars: int = 1800, overlap_chars: int = 180) -> list[dict[str, Any]]:
    """Split Markdown near paragraph boundaries, with bounded overlap and line provenance."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    overlap_chars = max(0, min(overlap_chars, max_chars - 1))
    lines = text.splitlines(keepends=True)
    chunks: list[dict[str, Any]] = []
    heading_stack: list[tuple[int, str]] = []
    section_lines: list[tuple[int, str]] = []

    def emit_section() -> None:
        nonlocal section_lines
        if not section_lines:
            return
        heading_path = " > ".join(label for _, label in heading_stack) or "Introduction"
        header = heading_stack[-1][1] if heading_stack else "Introduction"
        section_text = "".join(line for _, line in section_lines)
        line_offsets: list[int] = []
        offset = 0
        for _, line in section_lines:
            line_offsets.append(offset)
            offset += len(line)

        def line_at(char_offset: int) -> int:
            index = max(0, bisect.bisect_right(line_offsets, char_offset) - 1)
            return section_lines[index][0]

        start = 0
        while start < len(section_text):
            limit = min(start + max_chars, len(section_text))
            end = limit
            if limit < len(section_text):
                # Prefer a paragraph boundary in the latter half of the window.
                boundary = section_text.rfind("\n\n", start + max_chars // 2, limit)
                if boundary >= 0:
                    end = boundary + 2
                else:
                    newline = section_text.rfind("\n", start + max_chars // 2, limit)
                    if newline >= 0:
                        end = newline + 1
            if end <= start:
                end = min(start + max_chars, len(section_text))
            content = section_text[start:end]
            chunks.append({
                "header": header,
                "heading_path": heading_path,
                "start_line": line_at(start),
                "end_line": line_at(max(start, end - 1)),
                "content": content,
            })
            if end >= len(section_text):
                break
            start = max(start + 1, end - overlap_chars)
        section_lines = []

    for line_no, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            emit_section()
            level = len(match.group(1))
            heading = match.group(2).strip()
            heading_stack[:] = [(depth, label) for depth, label in heading_stack if depth < level]
            heading_stack.append((level, heading))
            section_lines = [(line_no, line)]
        else:
            section_lines.append((line_no, line))
    emit_section()
    return chunks

class HybridRetrievalIndex:
    """SQLite-backed BM25 + default-on local embedding cosine similarity."""

    def __init__(self, path: Path, embedding_model: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_model_name = (
            embedding_model
            if embedding_model is not None
            else os.environ.get("ENSEMBLE_MEMORY_EMBEDDING_MODEL")
            or "sentence-transformers/all-MiniLM-L6-v2"
        )
        self._embedder = None
        self._model_lock = threading.Lock()
        self._cache_lock = threading.RLock()
        self._corpus_cache = {}
        self._full_corpus_key: frozenset[str] | None = None
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY, file TEXT NOT NULL, ordinal INTEGER NOT NULL,
                signature TEXT NOT NULL, header TEXT NOT NULL, heading_path TEXT NOT NULL,
                content TEXT NOT NULL, size_bytes INTEGER NOT NULL, tokens TEXT NOT NULL,
                embedding TEXT, embedding_model TEXT, UNIQUE(file, ordinal))""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(chunks)")}
            if "embedding_model" not in columns:
                db.execute("ALTER TABLE chunks ADD COLUMN embedding_model TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS chunks_file_idx ON chunks(file)")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=10000")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _model(self):
        if not self.embedding_model_name:
            return None
        if self._embedder is None:
            with self._model_lock:
                if self._embedder is None:
                    try:
                        from sentence_transformers import SentenceTransformer
                    except ImportError as exc:
                        raise RuntimeError(
                            "Embeddings are configured but sentence-transformers is not installed; "
                            "install the root requirements.txt"
                        ) from exc
                    self._embedder = SentenceTransformer(self.embedding_model_name, device="cpu")
        return self._embedder

    def _embed(self, texts: list[str]) -> list[list[float]] | None:
        model = self._model()
        if model is None:
            return None
        vectors = model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return [vector.astype(float).tolist() for vector in vectors]

    def _invalidate_cache(self) -> None:
        with self._cache_lock:
            self._corpus_cache.clear()
            self._full_corpus_key = None

    def _rows_to_documents(self, rows) -> list[dict[str, Any]]:
        docs = []
        for row in rows:
            tokens = json.loads(row["tokens"])
            freq = {}
            for term in tokens:
                freq[term] = freq.get(term, 0) + 1
            docs.append({
                "file": row["file"], "section": row["header"],
                "heading_path": row["heading_path"], "content": row["content"],
                "size_bytes": row["size_bytes"], "frequencies": freq,
                "length": len(tokens), "embedding": row["embedding"],
            })
        return docs

    def _refresh_cached_document(self, file: str) -> None:
        """Update existing corpus caches incrementally after a document write."""
        with self._cache_lock:
            if not self._corpus_cache:
                return
            with self._connect() as db:
                rows = db.execute("SELECT * FROM chunks WHERE file=?", (file,)).fetchall()
            new_docs = self._rows_to_documents(rows)
            old_full_key = self._full_corpus_key
            if old_full_key is not None:
                updated_files = set(old_full_key)
                if new_docs:
                    updated_files.add(file)
                else:
                    updated_files.discard(file)
                new_full_key = frozenset(updated_files)
                if new_full_key != old_full_key:
                    cached_full = self._corpus_cache.pop(old_full_key, None)
                    if cached_full is not None:
                        self._corpus_cache[new_full_key] = cached_full
                    self._full_corpus_key = new_full_key

            full_key = self._full_corpus_key
            for key in list(self._corpus_cache):
                if file not in key and key != full_key:
                    continue
                docs, _average_length, doc_freq = self._corpus_cache[key]
                total_tokens = sum(doc["length"] for doc in docs)
                retained = []
                for doc in docs:
                    if doc["file"] != file:
                        retained.append(doc)
                        continue
                    total_tokens -= doc["length"]
                    for term in doc["frequencies"]:
                        remaining = doc_freq.get(term, 0) - 1
                        if remaining > 0:
                            doc_freq[term] = remaining
                        else:
                            doc_freq.pop(term, None)
                if file in key:
                    retained.extend(new_docs)
                    for doc in new_docs:
                        total_tokens += doc["length"]
                        for term in doc["frequencies"]:
                            doc_freq[term] = doc_freq.get(term, 0) + 1
                self._corpus_cache[key] = (
                    retained, total_tokens / max(len(retained), 1), doc_freq
                )

    def warm_corpus(self, all_files: set[str]):
        """Load the local model and register the corpus cache before readiness.

        Readiness must not be reported while the first prompt could still trigger
        a model download/load. Indexing document chunks normally loads the model,
        but an empty memory directory needs the explicit preload below too.
        """
        if self.embedding_model_name:
            self._model()
        key = frozenset(all_files)
        with self._cache_lock:
            self._full_corpus_key = key
            return self._corpus(all_files)

    def _corpus(self, allowed_files: set[str]):
        key = frozenset(allowed_files)
        # Serialize cold-cache builds so concurrent prompt hooks do not all
        # scan and tokenize the same corpus at once.
        with self._cache_lock:
            cached = self._corpus_cache.get(key)
            if cached is not None:
                return cached
            # Warm-up caches the full corpus before readiness is reported. A
            # source-filtered query can derive its statistics from that in-memory
            # superset instead of reopening SQLite and rebuilding the corpus.
            for cached_key, cached_value in self._corpus_cache.items():
                if key.issubset(cached_key):
                    full_docs = cached_value[0]
                    docs = [doc for doc in full_docs if doc["file"] in allowed_files]
                    df: dict[str, int] = {}
                    total = 0
                    for doc in docs:
                        for term in doc["frequencies"]:
                            df[term] = df.get(term, 0) + 1
                        total += doc["length"]
                    result = (docs, total / max(len(docs), 1), df)
                    self._corpus_cache[key] = result
                    return result
            with self._connect() as db:
                rows = db.execute("SELECT * FROM chunks").fetchall()
            docs = self._rows_to_documents(row for row in rows if row["file"] in allowed_files)
            df = {}
            total = 0
            for doc in docs:
                total += doc["length"]
                for term in doc["frequencies"]:
                    df[term] = df.get(term, 0) + 1
            result = (docs, total / max(len(docs), 1), df)
            self._corpus_cache[key] = result
            return result

    def replace_document(self, file: str, signature: str, chunks: list[dict[str, Any]]) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT signature, embedding, embedding_model FROM chunks WHERE file=? LIMIT 1",
                (file,),
            ).fetchone()
            if row and row["signature"] == signature:
                if not self.embedding_model_name:
                    return
                if row["embedding"] and row["embedding_model"] == self.embedding_model_name:
                    return
        texts = [str(chunk.get("content", "")) for chunk in chunks]
        embeddings = self._embed(texts)
        with self._connect() as db:
            db.execute("DELETE FROM chunks WHERE file=?", (file,))
            for ordinal, chunk in enumerate(chunks):
                content = texts[ordinal]
                db.execute(
                    """INSERT INTO chunks
                    (file, ordinal, signature, header, heading_path, content, size_bytes, tokens, embedding, embedding_model)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (file, ordinal, signature, str(chunk.get("header", "")),
                     str(chunk.get("heading_path", chunk.get("header", ""))), content,
                     len(content.encode("utf-8")), json.dumps(tokenize(content)),
                     json.dumps(embeddings[ordinal]) if embeddings else None,
                     self.embedding_model_name if embeddings else None),
                )

        self._refresh_cached_document(file)

    def remove_missing(self, present_files: set[str]) -> None:
        with self._connect() as db:
            indexed = {row["file"] for row in db.execute("SELECT DISTINCT file FROM chunks")}
            for file in indexed - present_files:
                db.execute("DELETE FROM chunks WHERE file=?", (file,))

        if indexed - present_files:
            self._invalidate_cache()

    def search(self, query: str, top_k: int, allowed_files: set[str]) -> list[dict[str, Any]]:
        query_tokens = tokenize(query)
        if not query_tokens or not allowed_files:
            return []
        query_vector = None
        if self.embedding_model_name:
            encoded = self._embed([query])
            query_vector = encoded[0] if encoded else None
        rows, avg_len, doc_freq = self._corpus(allowed_files)
        n_docs = len(rows)
        scored = []
        qset = set(query_tokens)
        for row in rows:
            freq = row["frequencies"]
            length = row["length"]
            bm25 = 0.0
            for term in qset:
                tf = freq.get(term, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n_docs - doc_freq.get(term, 0) + 0.5) / (doc_freq.get(term, 0) + 0.5))
                bm25 += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * length / max(avg_len, 1)))
            folded = row["content"].casefold()
            phrase = query.casefold().strip()
            exact = bool(phrase and phrase in folded)
            semantic = 0.0
            if query_vector and row["embedding"]:
                candidate = json.loads(row["embedding"])
                semantic = max(0.0, sum(a * b for a, b in zip(query_vector, candidate)))
            scored.append((row, bm25, semantic, exact))
        max_bm25 = max((entry[1] for entry in scored), default=0.0)
        results = []
        for row, bm25, semantic, exact in scored:
            lexical = bm25 / max_bm25 if max_bm25 else 0.0
            if query_vector:
                score = 0.42 * lexical + 0.58 * semantic
            else:
                score = lexical
            if exact:
                score = max(score, 1.0)
            # Exact phrases always survive. Otherwise reject near-zero
            # semantic-only matches and tiny normalized lexical noise.
            if exact or lexical >= 0.08 or semantic >= 0.30:
                results.append({
                    "file": row["file"], "section": row["section"],
                    "heading_path": row["heading_path"], "score": score,
                    "size_bytes": row["size_bytes"], "retrieval": (
                        "exact+hybrid" if exact and query_vector else
                        "exact+bm25" if exact else
                        "hybrid" if query_vector else "bm25"
                    ),
                })
        results.sort(key=lambda item: (-item["score"], item["file"], item["section"]))
        return results[:top_k]
