"""
Retrieval test: top-5 hit rate on hand-written queries, and the score
distribution used to calibrate the retrieval gate.

    python -m eval.retrieval_test
"""
from __future__ import annotations

import pathlib
import statistics

import yaml

from retrieval import get_index

QUERIES = pathlib.Path(__file__).parent / "retrieval_queries.yaml"


def run(k: int = 5) -> dict:
    idx = get_index()
    qs = yaml.safe_load(QUERIES.read_text(encoding="utf-8"))["queries"]
    report = {"in_scope": [], "out_scope": []}
    for q in qs:
        for mode in ("raw", "kw"):
            res = idx.search(q[mode], k=k)
            top_ids = [r.chunk.chunk_id for r in res]
            row = {
                "id": q["id"], "mode": mode, "query": q[mode],
                "top": top_ids,
                "top_rrf": round(res[0].rrf_score, 5) if res else 0.0,
                "top_vec": round(res[0].vector_score, 4) if res else 0.0,
                "top_bm25_rank": res[0].bm25_rank if res else 999,
            }
            if q["gold"]:
                row["hit"] = any(g in top_ids for g in q["gold"])
                report["in_scope"].append(row)
            else:
                report["out_scope"].append(row)
    return report


def main() -> None:
    rep = run()
    for mode in ("raw", "kw"):
        rows = [r for r in rep["in_scope"] if r["mode"] == mode]
        hits = sum(r["hit"] for r in rows)
        print(f"\n[{mode}] top-5 hit rate: {hits}/{len(rows)}")
        for r in rows:
            if not r["hit"]:
                print(f"  MISS {r['id']}: {r['query'][:60]!r} -> {r['top'][:5]}")
        ins = [r["top_vec"] for r in rows]
        outs = [r["top_vec"] for r in rep["out_scope"] if r["mode"] == mode]
        print(f"  top vector sim  in-scope: min {min(ins):.3f} median {statistics.median(ins):.3f}"
              f" | out-of-scope: max {max(outs):.3f} median {statistics.median(outs):.3f}")
        for r in rep["out_scope"]:
            if r["mode"] == mode:
                print(f"  OOS {r['id']}: vec {r['top_vec']:.3f} -> {r['top'][:3]}")


if __name__ == "__main__":
    main()
