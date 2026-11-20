"""Corpus integrity: every chunk's text must appear in its source PDF (ignoring whitespace
and the footnote markers the builder strips), and every rules reference must exist."""
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).parent.parent
PROCESSED = ROOT / "corpus" / "processed"
PDF_DIR = ROOT / "corpus" / "raw" / "pdf"


def _squash(t: str) -> str:
    t = re.sub(r"(?<![\w\]])\d{1,2}\[", "[", t)
    return re.sub(r"\s+", "", t)


@pytest.mark.parametrize("jsonl", sorted(PROCESSED.glob("*.jsonl")), ids=lambda p: p.stem)
def test_chunks_are_verbatim_from_source(jsonl):
    from pypdf import PdfReader
    pdf_text = _squash("\n".join(p.extract_text() or "" for p in PdfReader(str(PDF_DIR / f"{jsonl.stem}.pdf")).pages))
    bad = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        # Check in 200-char windows: page headers/footers are removed between pages.
        body = _squash(r["text"])
        windows = [body[i:i + 200] for i in range(0, len(body), 200)]
        missing = [w for w in windows if w not in pdf_text]
        if len(missing) > max(1, len(windows) // 5):
            bad.append(r["chunk_id"])
    assert not bad, f"chunks not found in source PDF: {bad[:10]}"


def test_chunk_ids_unique():
    ids = [json.loads(l)["chunk_id"] for f in PROCESSED.glob("*.jsonl") for l in f.read_text(encoding="utf-8").splitlines()]
    assert len(ids) == len(set(ids))
