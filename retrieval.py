"""
Retrieval system — ingest, chunk, hybrid search (vector + BM25 with RRF).

Runtime: only the query is embedded. Corpus embeddings are precomputed
offline and stored in a persistent Chroma index committed to the repo.
"""
from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import re
from dataclasses import dataclass, field
from typing import Optional

import yaml
from rank_bm25 import BM25Okapi

import config

logger = logging.getLogger(__name__)
# chromadb's bundled posthog telemetry raises on every call; silence it.
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

# ── Paths ────────────────────────────────────────────────────────
PROJECT_ROOT = pathlib.Path(__file__).parent
CORPUS_RAW = PROJECT_ROOT / "corpus" / "raw"
CORPUS_PROCESSED = PROJECT_ROOT / "corpus" / "processed"
CORPUS_INDEX = PROJECT_ROOT / "corpus" / "index"


# ── Data structures ─────────────────────────────────────────────
@dataclass
class Chunk:
    chunk_id: str               # e.g. CPA2019:s35
    act: str
    act_code: str
    section: str
    heading: str
    text: str
    as_of: str
    source_url: str
    content_hash: str
    tag: str = "law"            # "law" or "procedure"


@dataclass
class SearchResult:
    chunk: Chunk
    vector_score: float
    bm25_rank: int
    rrf_score: float
    source_tag: str             # "law" or "procedure"


# ══════════════════════════════════════════════════════════════════
# INGESTION (offline, run once)
# ══════════════════════════════════════════════════════════════════

def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# Chunking lives in corpus/build_corpus.py (it needs per-source layout rules);
# this module only loads its output.
def load_processed_chunks() -> list[Chunk]:
    chunks = []
    for path in sorted(CORPUS_PROCESSED.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            chunks.append(Chunk(
                chunk_id=r["chunk_id"], act=r["act"], act_code=r["act_code"],
                section=r["section"], heading=r["heading"], text=r["text"],
                as_of=r["as_of"], source_url=r["source_url"],
                content_hash=_hash_text(r["text"]), tag=r.get("tag", "law"),
            ))
    return chunks


_STOP = set("the a an of to in and or for on by with is are be any such this that as at from which who shall may under".split())


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP]


def ingest_procedure_notes(notes_path: pathlib.Path) -> list[Chunk]:
    """
    Load hand-written procedure notes from a YAML file.
    Each note has: id, topic, text, official_url, retrieved_on.
    """
    if not notes_path.exists():
        return []
    with open(notes_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    notes = data if isinstance(data, list) else data.get("notes", [])
    chunks = []
    for note in notes:
        chunk_id = f"PROC:{note['id']}"
        chunks.append(Chunk(
            chunk_id=chunk_id,
            act="Procedure Notes",
            act_code="PROC",
            section=note["id"],
            heading=note.get("topic", ""),
            text=note["text"],
            as_of=note.get("retrieved_on", config.AS_OF),
            source_url=note.get("official_url", ""),
            content_hash=_hash_text(note["text"]),
            tag="procedure",
        ))
    return chunks


# ══════════════════════════════════════════════════════════════════
# INDEX (Chroma + BM25)
# ══════════════════════════════════════════════════════════════════

class CorpusIndex:
    """Persistent index: ChromaDB for vectors, in-memory BM25 for keywords."""

    def __init__(self, persist_dir: str | None = None):
        import os
        os.environ["ANONYMIZED_TELEMETRY"] = "False"
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        self._persist_dir = persist_dir or str(CORPUS_INDEX)
        self._chroma = chromadb.PersistentClient(
            path=self._persist_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._chroma.get_or_create_collection(
            name="legal_corpus",
            metadata={"hnsw:space": "cosine"},
        )
        self._chunks: dict[str, Chunk] = {}
        self._bm25: Optional[BM25Okapi] = None
        self._bm25_ids: list[str] = []
        self._embedder = None

    def _get_embedder(self):
        if self._embedder is None:
            from fastembed import TextEmbedding
            self._embedder = TextEmbedding(model_name=config.EMBEDDING_MODEL)
        return self._embedder

    def add_chunks(self, chunks: list[Chunk]) -> None:
        """Add chunks to both Chroma and BM25 index."""
        if not chunks:
            return

        embedder = self._get_embedder()
        texts = [c.text for c in chunks]
        ids = [c.chunk_id for c in chunks]

        # Embed heading + text: headings carry most of the meaning for short queries.
        embeddings = list(embedder.embed([f"{c.act}. {c.heading}. {c.text}" for c in chunks], batch_size=32))

        # Add to Chroma
        metadatas = [
            {
                "act": c.act,
                "act_code": c.act_code,
                "section": c.section,
                "heading": c.heading,
                "as_of": c.as_of,
                "source_url": c.source_url,
                "tag": c.tag,
                "content_hash": c.content_hash,
            }
            for c in chunks
        ]
        for i in range(0, len(ids), 200):  # Chroma caps batch size
            self._collection.upsert(
                ids=ids[i:i + 200],
                documents=texts[i:i + 200],
                embeddings=[e.tolist() for e in embeddings[i:i + 200]],
                metadatas=metadatas[i:i + 200],
            )

        # Update chunk lookup
        for c in chunks:
            self._chunks[c.chunk_id] = c

        # Rebuild BM25
        self._rebuild_bm25()

        logger.info("Indexed %d chunks", len(chunks))

    def _rebuild_bm25(self) -> None:
        """Rebuild BM25 index from all stored chunks."""
        all_data = self._collection.get(include=["documents", "metadatas"])
        if not all_data["ids"]:
            return
        self._bm25_ids = all_data["ids"]
        tokenized = [_tokens(f"{m.get('heading', '')} {doc}") for doc, m in zip(all_data["documents"], all_data["metadatas"])]
        self._bm25 = BM25Okapi(tokenized)

    def load_chunks_from_collection(self) -> None:
        """Load chunk objects from Chroma metadata (on startup)."""
        all_data = self._collection.get(
            include=["documents", "metadatas"]
        )
        for cid, doc, meta in zip(
            all_data["ids"], all_data["documents"], all_data["metadatas"]
        ):
            self._chunks[cid] = Chunk(
                chunk_id=cid,
                act=meta.get("act", ""),
                act_code=meta.get("act_code", ""),
                section=meta.get("section", ""),
                heading=meta.get("heading", ""),
                text=doc,
                as_of=meta.get("as_of", ""),
                source_url=meta.get("source_url", ""),
                content_hash=meta.get("content_hash", ""),
                tag=meta.get("tag", "law"),
            )
        self._rebuild_bm25()

    def has_chunks(self) -> bool:
        return self._collection.count() > 0

    def get_chunk(self, chunk_id: str) -> Optional[Chunk]:
        return self._chunks.get(chunk_id)

    def all_chunk_ids(self) -> set[str]:
        return set(self._chunks.keys())

    # ── Hybrid search ────────────────────────────────────────────
    def search(
        self,
        query: str,
        k: int | None = None,
        tag_filter: str | None = None,
    ) -> list[SearchResult]:
        """
        Hybrid search: vector + BM25, fused with Reciprocal Rank Fusion.
        """
        k = k or config.RETRIEVAL_K
        if not self.has_chunks():
            return []

        # ── Vector search ──────────────────────────────────────
        embedder = self._get_embedder()
        query_emb = list(embedder.embed([query]))[0].tolist()

        where_filter = {"tag": tag_filter} if tag_filter else None
        vec_results = self._collection.query(
            query_embeddings=[query_emb],
            n_results=min(k * 2, self._collection.count()),
            where=where_filter,
            include=["distances", "documents"],
        )

        vec_scores: dict[str, float] = {}
        if vec_results["ids"] and vec_results["ids"][0]:
            for cid, dist in zip(
                vec_results["ids"][0], vec_results["distances"][0]
            ):
                # Chroma cosine returns distance; convert to similarity
                vec_scores[cid] = 1.0 - dist

        # ── BM25 search ───────────────────────────────────────
        bm25_ranks: dict[str, int] = {}
        if self._bm25 is not None:
            tokenized_query = _tokens(query)
            scores = self._bm25.get_scores(tokenized_query)
            ranked = sorted(
                enumerate(scores), key=lambda x: x[1], reverse=True
            )
            for rank, (idx, score) in enumerate(ranked[: k * 2]):
                if score > 0:
                    cid = self._bm25_ids[idx]
                    bm25_ranks[cid] = rank + 1

        # ── RRF fusion ────────────────────────────────────────
        all_ids = set(vec_scores.keys()) | set(bm25_ranks.keys())
        rrf_const = 60  # standard RRF constant
        rrf_scores: list[tuple[str, float, float, int]] = []

        v_order = {cid: i + 1 for i, cid in enumerate(sorted(vec_scores, key=vec_scores.get, reverse=True))}
        for cid in all_ids:
            v_rank = v_order.get(cid, len(vec_scores) + 1)
            b_rank = bm25_ranks.get(cid, len(bm25_ranks) + 1)

            rrf = (
                config.VECTOR_WEIGHT * (1.0 / (rrf_const + v_rank))
                + config.BM25_WEIGHT * (1.0 / (rrf_const + b_rank))
            )
            rrf_scores.append((
                cid,
                rrf,
                vec_scores.get(cid, 0.0),
                bm25_ranks.get(cid, 999),
            ))

        rrf_scores.sort(key=lambda x: x[1], reverse=True)

        # ── Build results ─────────────────────────────────────
        results: list[SearchResult] = []
        for cid, rrf, vscore, brank in rrf_scores[:k]:
            chunk = self._chunks.get(cid)
            if chunk is None:
                continue
            if tag_filter and chunk.tag != tag_filter:
                continue
            results.append(SearchResult(
                chunk=chunk,
                vector_score=vscore,
                bm25_rank=brank,
                rrf_score=rrf,
                source_tag=chunk.tag,
            ))

        return results


# ══════════════════════════════════════════════════════════════════
# RETRIEVAL GATE
# ══════════════════════════════════════════════════════════════════

def passes_retrieval_gate(results: list[SearchResult]) -> bool:
    """True if the best hit is semantically close enough to ground an answer.

    Uses cosine similarity of the top result, not the RRF score: RRF depends only on
    ranks, so it is high even for nonsense queries. Threshold measured with
    `python -m eval.retrieval_test` on keyword-transformed queries (see config).
    """
    if not results:
        return False
    return max(r.vector_score for r in results) >= config.RETRIEVAL_GATE_THRESHOLD


# ══════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════

_index: Optional[CorpusIndex] = None


def get_index() -> CorpusIndex:
    global _index
    if _index is None:
        _index = CorpusIndex()
        if _index.has_chunks():
            _index.load_chunks_from_collection()
    return _index


# ══════════════════════════════════════════════════════════════════
# CLI: python -m retrieval ingest
# ══════════════════════════════════════════════════════════════════

def _cli_ingest():
    """Rebuild the index from corpus/processed/*.jsonl and procedure notes."""
    import shutil
    for child in CORPUS_INDEX.iterdir():
        if child.name != ".gitkeep":
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    idx = CorpusIndex()
    chunks = load_processed_chunks()
    if not chunks:
        raise SystemExit("No processed chunks. Run: python -m corpus.build_corpus")
    chunks += ingest_procedure_notes(CORPUS_RAW / "procedure_notes.yaml")
    idx.add_chunks(chunks)
    print(f"Indexed {len(chunks)} chunks into {CORPUS_INDEX}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "ingest":
        _cli_ingest()
    else:
        print("Usage: python -m retrieval ingest")
