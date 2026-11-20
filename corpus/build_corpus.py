"""
Build section-level chunks from the official Act PDFs in corpus/raw/pdf/.

    python -m corpus.build_corpus          # writes corpus/processed/*.jsonl + MANIFEST.md

Text is kept as extracted from the source PDF. The only normalisations are:
page headers/footers and India Code amendment footnotes are removed (they are
editorial, not statute text), footnote markers like "2[" become "[", and runs
of whitespace collapse to one space. Every chunk records its source URL and the
source PDF's sha256 so it can be checked against the original.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).parent
PDF_DIR = ROOT / "raw" / "pdf"
OUT_DIR = ROOT / "processed"
SOURCES = ROOT / "raw" / "sources.yaml"


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pages(path: pathlib.Path) -> list[str]:
    from pypdf import PdfReader
    return [p.extract_text() or "" for p in PdfReader(str(path)).pages]


# ── Page cleaning per source layout ──────────────────────────────
_FOOTNOTE = re.compile(r"^\s*\d+\.\s+(Subs\.|Ins\.|Omitted|Added|Rep\.|The words|Cl\.|Clause|Sub-s|Section|Proviso|Explanation|Renumbered|\d{1,2}(st|nd|rd|th)\s+\w+,?\s+\d{4})")
_GAZETTE_HDR = re.compile(r"(THE GAZETTE OF INDIA EXTRAORDINARY|^SEC\. \d\]|^\s*\d+\s*$)")


def _clean_indiacode(page: str) -> tuple[list[str], list[str]]:
    lines = page.splitlines()
    # India Code puts amendment footnotes after a blank separator line at the page bottom.
    for i, ln in enumerate(lines):
        if _FOOTNOTE.match(ln) and all(_FOOTNOTE.match(x) or not re.match(r"^\s*\d+\.\s", x) for x in lines[i:]):
            lines = lines[:i]
            break
    body = [ln for ln in lines if not re.fullmatch(r"\s*\d{1,3}\s*", ln)]
    return body, []


def _clean_gazette(page: str) -> tuple[list[str], list[str]]:
    """Gazette layout: marginal section headings are printed as short lines at the page end."""
    lines = [ln for ln in page.splitlines() if ln.strip() and not _GAZETTE_HDR.search(ln)]
    margin: list[str] = []
    while lines and len(lines[-1].strip()) <= 20:
        margin.insert(0, lines.pop().strip())
    # A short last body line ("extend to one year.") can be swept up too; marginal
    # notes always start with a capital letter, so drop lowercase-led lines at the front.
    while margin and margin[0][:1].islower():
        lines.append(margin.pop(0))
    notes, cur = [], []
    for ln in margin:
        cur.append(ln)
        if ln.endswith("."):
            notes.append(" ".join(cur))
            cur = []
    # Notes like "68 of 1986." cite Acts, they are not headings.
    notes = [n for n in notes if not re.fullmatch(r"\d+ of \d{4}\.", n)]
    return lines, notes


def _clean_plain(page: str) -> tuple[list[str], list[str]]:
    return [ln for ln in page.splitlines() if not re.fullmatch(r"\s*\d{1,3}\s*", ln)], []


CLEANERS = {"indiacode": _clean_indiacode, "gazette": _clean_gazette, "plain": _clean_plain}

_SEC = re.compile(r"^\s*(?:\d{1,2}\[)?(\d{1,3})([A-Z]{0,3})\s?\.\s*(.*)$")


def _norm(text: str) -> str:
    text = re.sub(r"(?<![\w\]])\d{1,2}\[", "[", text)
    return re.sub(r"[ \t ]+", " ", text).strip()


def _key(num: int, suffix: str) -> tuple[int, str]:
    return (num, suffix)


def split_sections(lines_by_page: list[tuple[list[str], list[str]]], src: dict) -> list[dict]:
    """Sequential splitter: a line starts section N only if N follows the previous section."""
    start_re = re.compile(src["body_start"])
    end_re = re.compile(src["body_end"]) if src.get("body_end") else None
    last_sec = int(src["last_section"])
    started, done = False, False
    sections: list[dict] = []
    prev = (0, "")
    for page_no, (lines, notes) in enumerate(lines_by_page, 1):
        starts_on_page = []
        for li, ln in enumerate(lines):
            if done:
                break
            if not started:
                if start_re.search(ln):
                    started = True
                else:
                    continue
            if end_re and end_re.search(ln) and sections:
                done = True
                break
            m = _SEC.match(ln)
            if m:
                num, suf, rest = int(m.group(1)), m.group(2), m.group(3)
                gap_ok = any(prev[0] + 1 == lo and num == hi + 1 for lo, hi in src.get("repealed_ranges", []))
                plausible = (num == prev[0] and suf > prev[1]) or ((prev[0] < num <= prev[0] + 4 or gap_ok) and num <= last_sec)
                # India Code headings end with ".—", sometimes on the following line.
                nxt = lines[li + 1] if li + 1 < len(lines) else ""
                looks_like_heading = src["layout"] != "indiacode" or "—" in rest or "—" in nxt[:120]
                if plausible and looks_like_heading and rest.strip():
                    sec = {"section": f"{num}{suf}", "page": page_no, "lines": [ln]}
                    sections.append(sec)
                    starts_on_page.append(sec)
                    prev = (num, suf)
                    continue
            if sections:
                sections[-1]["lines"].append(ln)
        # Gazette marginal notes are in section order for the sections starting on that page.
        if notes and len(notes) == len(starts_on_page):
            for sec, note in zip(starts_on_page, notes):
                sec["heading"] = note.rstrip(".")
        elif src["layout"] == "gazette" and starts_on_page:
            for sec in starts_on_page:
                sec.setdefault("heading", "")
    for sec in sections:
        body = "\n".join(sec.pop("lines"))
        sec["text"] = _norm(body)
        if "heading" not in sec:
            if src["layout"] == "indiacode":
                m = re.match(r"^\s*(?:\d{1,2}\[)?\d+[A-Z]*\s?\.\s*(.*?)\s*\.?\s*—", body, re.S)
                sec["heading"] = _norm(m.group(1)) if m else ""
            else:  # plain: heading is the rest of the first line
                sec["heading"] = _norm(_SEC.match(body.splitlines()[0]).group(3)).rstrip(" -.:")
    return sections


def split_schedule(lines_by_page, src: dict) -> list[dict]:
    """Limitation Act Schedule: one chunk per Article, heading = division/part."""
    lines = [ln for page, _ in lines_by_page for ln in page]
    text = "\n".join(lines)
    start = text.find(src["schedule_start"])
    if start < 0:
        return []
    lines = text[start:].splitlines()
    arts, prev, part = [], 0, ""
    for ln in lines:
        if re.match(r"^\s*(PART [IVX]+\.?—|FIRST DIVISION|SECOND DIVISION|THIRD DIVISION)", ln):
            part = _norm(ln)
            continue
        m = re.match(r"^\s*(\d{1,3})\.\s+(\S.*)$", ln)
        if m and int(m.group(1)) == prev + 1:
            prev = int(m.group(1))
            arts.append({"section": f"schedule_art{prev}", "heading": part, "lines": [ln]})
            continue
        if re.match(r"^\s*Description of (suit|appeal|application)", ln):
            continue  # repeated table header on each page
        if arts:
            arts[-1]["lines"].append(ln)
    for a in arts:
        a["text"] = _norm("\n".join(a.pop("lines")))
    return arts


_DEF_CLAUSE = re.compile(r"^\s*\[?\((\d+[A-Z]?|[a-z]{1,3})\)\s*[“\"]([^”\"]+)[”\"]", re.M)


def split_definitions(sec: dict) -> list[dict]:
    """A definitions section is too long to embed as one unit (the embedder truncates
    at ~512 tokens), so each defined term becomes its own chunk, e.g. CPA2019:s2(7)."""
    marks = list(_DEF_CLAUSE.finditer(sec["text"]))
    if len(marks) < 5:
        return [sec]
    out = [{**sec, "text": sec["text"][: marks[0].start()].strip() or sec["text"][:200], "heading": sec["heading"] + " (opening words)"}]
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(sec["text"])
        out.append({"section": f"{sec['section']}({m.group(1)})", "heading": f"{sec['heading']}: {m.group(2).strip()}",
                    "text": sec["text"][m.start():end].strip()})
    return out


def build() -> dict:
    sources = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(exist_ok=True)
    summary = {}
    for code, src in sources["acts"].items():
        pdf = PDF_DIR / f"{code}.pdf"
        if not pdf.exists():
            print(f"  missing {pdf.name}: skipped")
            continue
        sha = _sha(pdf)
        if src.get("sha256") and src["sha256"] != sha:
            sys.exit(f"{pdf.name}: sha256 differs from sources.yaml. Update sources.yaml if you replaced the PDF.")
        pages = [CLEANERS[src["layout"]](p) for p in _pages(pdf)]
        secs = []
        for sec in split_sections(pages, src):
            is_def = sec["heading"].lower().startswith("definition") or (sec["section"] == "2" and len(sec["text"]) > 4000)
            secs += split_definitions(sec) if is_def and len(sec["text"]) > 4000 else [sec]
        if src.get("schedule_start"):
            secs += split_schedule(pages, src)
        recs = []
        for s in secs:
            recs.append({
                "chunk_id": f"{code}:s{s['section']}" if not s["section"].startswith("schedule") else f"{code}:{s['section']}",
                "act": src["name"], "act_code": code, "section": s["section"],
                "heading": s["heading"], "text": s["text"], "as_of": src["as_of"],
                "source_url": src["url"], "source_sha256": sha, "tag": "law",
                "pdf_page": s.get("page"),
            })
        with open(OUT_DIR / f"{code}.jsonl", "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        nums = sorted({int(re.match(r"\d+", s["section"]).group()) for s in secs if not s["section"].startswith("schedule")})
        missing = sorted(set(range(1, int(src["last_section"]) + 1)) - set(nums))
        no_head = [s["section"] for s in secs if not s["heading"]]
        summary[code] = {"chunks": len(recs), "missing_sections": missing, "no_heading": no_head, "sha256": sha}
        print(f"{code}: {len(recs)} chunks; missing sections {missing or 'none'}; no heading: {len(no_head)}")
    write_manifest(sources, summary)
    return summary


def write_manifest(sources: dict, summary: dict) -> None:
    """MANIFEST.md and a 20-chunk spot-check sheet, both generated (never hand-edited)."""
    import random
    rows = ["# Corpus Manifest", "", "Generated by `python -m corpus.build_corpus`. Do not edit by hand.", "",
            "| Code | Instrument | Source | As of | Chunks | Gaps | sha256 (PDF) |", "|---|---|---|---|---|---|---|"]
    for code, info in summary.items():
        src = sources["acts"][code]
        gaps = info["missing_sections"]
        gap_txt = "none" if not gaps else (f"{gaps[0]}–{gaps[-1]} (repealed in source)" if src.get("repealed_ranges") else ", ".join(map(str, gaps)))
        rows.append(f"| `{code}` | {src['name']} | [{src['publisher']}]({src['url']}) | {src['as_of']} | {info['chunks']} | {gap_txt} | `{info['sha256'][:12]}…` |")
    rows += ["", "Chunk IDs: `ACT:s<section>`, definition clauses `ACT:s2(<clause>)`, Limitation Act schedule `LA1963:schedule_art<n>`,",
             "procedure notes `PROC:<id>` (see `corpus/raw/procedure_notes.yaml`).", "",
             "Known source quality issues: ICA1872 is a state-government transcription with typos (e.g. \"form the party\" in s73);",
             "pypdf inserts stray spaces inside some words (\"tra fficking\"). The verifier compares quotes whitespace-insensitively.",
             "CPA2019 is the Gazette text as enacted (2019); later amendments are not reflected."]
    (ROOT / "MANIFEST.md").write_text("\n".join(rows) + "\n", encoding="utf-8")

    all_recs = [json.loads(l) for f in sorted(OUT_DIR.glob("*.jsonl")) for l in f.read_text(encoding="utf-8").splitlines()]
    sample = random.Random(20261120).sample(all_recs, 20)
    out = ["# Spot-check sheet (20 random chunks, seed 20261120)", "",
           "Open each source PDF at the page shown and confirm the chunk text matches. Mark ✅/❌.", ""]
    for r in sample:
        out += [f"## `{r['chunk_id']}` — {r['heading']}  ", f"Source: {r['source_url']} (PDF page {r.get('pdf_page')})  ",
                "Match: ☐", "", "```", r["text"][:600] + (" …" if len(r["text"]) > 600 else ""), "```", ""]
    (ROOT / "SPOTCHECK.md").write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    build()
