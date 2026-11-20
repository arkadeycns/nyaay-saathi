# Nyaay Saathi: an honest AI legal helper for everyday problems in India

A prototype that ordinary people can talk to (English, Hindi or Hinglish, by text or voice) about an unpaid salary, a refund that never came, money a client or friend owes, or a legal notice they don't understand.

> **Information, not legal advice.** It covers five central Acts (plus the consumer-commission jurisdiction rules) as a fixed snapshot. It says "I can't ground this" and "see an advocate" readily.

## What it does
| Tab | What happens |
|---|---|
| **Advisor** | Chat. Builds a case file (facts, dates, proof), works out the person's *situation* (a household worker, a company employee, an online shopper…), explains where they stand with cited law, compares options with money/time/emotional cost, computes the time limit in code, and drafts a demand letter or legal-aid application from facts the user confirms. |
| **Notice Decoder** | Paste a notice or upload a text PDF. Every extracted detail must match the notice word for word. Rules triage (court, police, cheque bounce, eviction → escalate). Each provision the sender cites is checked: real text shown, flagged as repealed, or "can't verify". Code computes the days left from the date you received it. |
| **Opponent Simulator** | Practise against the other side. Bluffs come from a reviewed library; the LLM only voices them. A side panel shows what the law actually says, the risks either way, and a suggested reply. |

## Design rules
1. **No legal claim without a verified source.** Every "Law" statement cites a corpus chunk and carries an exact quote. The quote must be a substring of the chunk, every number must appear in the cited text, and a second model from a different family must agree. Otherwise the system regenerates once, then drops the claim and says so. There is no case law, by design.
2. **Code decides, the LLM drafts.** Escalation, scope, situation, evidence lists, pathways, time limits, strength band and templates are deterministic (`rules/`). The LLM extracts facts, rewrites search queries, explains, and voices the opponent.
3. **Context, not generic advice.** `rules/routing.yaml` maps facts (who employs you, is anything in writing, online vs shop) to a situation. Each situation has its own evidence checklist, option order and forbidden words. For example, a household worker is never told to "ask HR for a salary slip", and the verifier enforces this.
4. **Never invent facts.** Amounts and dates are kept only if the user's exact words contain them.

## Architecture
```
app.py              Streamlit UI (3 tabs)
graph.py            LangGraph wiring: advisor + decoder graphs, node labels
nodes_advisor.py    screen → intake(LLM) → triage → retrieve(LLM rewrite+search+gate) → advise(LLM) → verify → assemble
nodes_decoder.py    extract(LLM) → span_check → triage → provisions → explain(LLM) → verify → assemble
nodes_simulator.py  bluff selection (code) + voicing (LLM) + coaching
llm.py              one OpenAI-compatible client; roles: generator / verifier / fast; backoff; run log
retrieval.py        Chroma (fastembed bge-small, ONNX) + BM25, RRF fusion, similarity gate
verify.py           verifier layers A (code) and B (LLM); C = retrieval gate
rules/              *.yaml rules + engine.py, limitation.py, validate.py
corpus/             raw/pdf (official PDFs) → build_corpus.py → processed/*.jsonl → index/ (committed)
eval/               cases.yaml, run_eval.py, metrics.py, retrieval_test.py, outputs/
tests/              pytest for all deterministic code + corpus verbatim check
docs/               build_log.md, failures.md, solution_outline.md
```

## Run it
```bash
pip install -r requirements-dev.txt
```
```bash
cp .env.example .env
```
Put your `GROQ_API_KEY` in `.env` (free at console.groq.com), then:
```bash
streamlit run app.py
```

## Swap the LLM provider (one change)
Set `LLM_PROVIDER=groq` (default), `gemini` or `openrouter` in `.env` with that provider's key. The models per role are in `config.PROVIDERS`, and nothing provider-specific lives elsewhere. Current free defaults (checked 2026-11-20):

| Provider | generator | verifier (different family) | fast |
|---|---|---|---|
| Groq | openai/gpt-oss-120b | qwen/qwen3.8-27b | openai/gpt-oss-20b |
| Gemini | gemini-3.5-flash | gemma-4-31b-it | gemini-3.5-flash-lite |
| OpenRouter | nemotron-3-ultra:free | gemma-4-31b-it:free | nemotron-3.5-lightning:free |

Voice uses Groq `whisper-large-v3`.

## Rebuild / check the corpus and rules
```bash
python -m corpus.build_corpus
```
```bash
python -m retrieval ingest
```
```bash
python -m rules.validate
```
```bash
python -m pytest -q
```
```bash
python -m eval.retrieval_test
```

## Evaluation
```bash
python -m eval.run_eval --provider groq
```
Raw, unedited outputs are saved per case in `eval/outputs/<run-id>/`, and metrics in `summary.md`. LangSmith is **opt-in** (`LANGSMITH_TRACING=true` plus `LANGSMITH_API_KEY`), for eval and dev on synthetic cases only, because traces would send case text to a third party.

## Free-tier limits and paid upgrades
- Groq free tier allows about 8K tokens/min per model, so one advisor answer takes about 20–40 s, longer when throttled. The Developer tier (pay-as-you-go) removes most waiting.
- Gemini free tier content "is used to improve our products". Use a paid key for real users.
- Streamlit Community Cloud: no persistence and a shared CPU. The index is committed, so no build step is needed.

## Not covered
State law (rent and deposits, stamp duty, state wage rules), criminal law, family and property law, cheque bounce (NI Act), case law, any Act not listed in `corpus/MANIFEST.md`. Voice output, OCR and photo input. Languages beyond English, Hindi and Hinglish.
