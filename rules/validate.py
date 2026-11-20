"""
Rules validator: the "no unverified law" mechanism.

Walks every rules/*.yaml file and fails if
  - a `ref` / `procedure_ref` / inline [CHUNK:ID] points to a chunk that does not exist,
  - a `quote` (or an inline "quoted text" [REF]) is not an exact substring of that chunk
    (compared ignoring whitespace and quote-mark style),
  - a limitation period's number of years is not stated in its cited chunk,
  - a value is still TODO_USER_VERIFY in a verified entry.

    python -m rules.validate
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any, Iterator

import yaml

import config
from rules import RULES_DIR

REF_RE = re.compile(r"\[((?:[A-Z]{2,5}\d{4}|PROC):[^\]\s]+)\]")
INLINE_QUOTE_RE = re.compile(r"[\"“]([^\"”]{6,}?)[\"”][^\"“\[]{0,80}?\[((?:[A-Z]{2,5}\d{4}|PROC):[^\]\s]+)\]")
YEARS = {2: "two years", 3: "three years"}


def squash(text: str) -> str:
    text = text.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)  # models emit non-breaking/en dashes for "-"
    return re.sub(r"\s+", "", text).lower()


def load_chunk_texts() -> dict[str, str]:
    texts: dict[str, str] = {}
    for path in (config.ROOT / "corpus" / "processed").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            texts[r["chunk_id"]] = r["text"]
    notes = yaml.safe_load((config.ROOT / "corpus" / "raw" / "procedure_notes.yaml").read_text(encoding="utf-8"))
    for n in notes["notes"]:
        texts[f"PROC:{n['id']}"] = n["text"]
    return texts


def quote_in(chunks: dict[str, str], ref: str, quote: str) -> bool:
    return ref in chunks and squash(quote) in squash(chunks[ref])


def _walk(node: Any, path: str) -> Iterator[tuple[str, Any]]:
    yield path, node
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def validate() -> list[str]:
    chunks = load_chunk_texts()
    errors: list[str] = []
    for f in sorted(RULES_DIR.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        for path, node in _walk(data, f.stem):
            if isinstance(node, dict):
                for key in ("ref", "procedure_ref"):
                    if key in node and node[key] not in chunks:
                        errors.append(f"{path}: {key} {node[key]!r} not in corpus")
                if "ref" in node and "quote" in node and not quote_in(chunks, node["ref"], node["quote"]):
                    errors.append(f"{path}: quote not found in {node['ref']}: {node['quote'][:70]!r}")
                if "years" in node and "ref" in node:
                    word = YEARS.get(node["years"], "")
                    if not word or squash(word) not in squash(chunks.get(node["ref"], "")):
                        errors.append(f"{path}: {node['years']} years not stated in {node['ref']}")
                if node.get("verified", True) and any(isinstance(v, str) and "TODO_USER_VERIFY" in v for v in node.values()):
                    errors.append(f"{path}: verified entry still contains TODO_USER_VERIFY")
            elif isinstance(node, str):
                for ref in REF_RE.findall(node):
                    if ref not in chunks:
                        errors.append(f"{path}: inline ref [{ref}] not in corpus")
                for quote, ref in INLINE_QUOTE_RE.findall(node):
                    if ref in chunks and not quote_in(chunks, ref, quote):
                        errors.append(f"{path}: inline quote not in {ref}: {quote[:70]!r}")
    return errors


def main() -> None:
    errors = validate()
    if errors:
        print(f"❌ {len(errors)} rules problems:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("✅ All rule references and quotes match the corpus.")


if __name__ == "__main__":
    main()
