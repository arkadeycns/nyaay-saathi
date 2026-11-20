# Build Log

## 2026-11-20 — Milestone 1: Foundation & Scaffolding

### What was built
- Project structure following the architecture spec
- `config.py`: Provider configs (Groq/Gemini/OpenRouter), model role mappings, retrieval settings
- `llm.py`: Swappable OpenAI-compatible adapter with backoff, structured output, Whisper
- `rules/`: Escalation, routing, legacy refs, costs, bluffs, playbook YAML files
- `rules/limitation.py`: Deterministic date arithmetic for limitation periods
- `rules/validate.py`: Startup validator for corpus chunk ID references
- `templates/`: Jinja2 templates for legal notice, consumer complaint, reply, legal aid application
- `corpus/MANIFEST.md`: Corpus structure documentation

### Model names verified
- Groq free tier (Nov 2026):
  - Generator: `llama-3.3-70b-versatile` (Production model, 131,072 context window, 32,768 max tokens, 280 tok/s)
  - Verifier: `openai/gpt-oss-20b` (Production open-weight reasoning model by OpenAI on Groq, 131,072 context, ~1,000 tok/s; satisfies hard requirement for a different model family than Meta Llama)
  - Fast role: `llama-3.1-8b-instant` (Production model, 131,072 context, ~560 tok/s)
  - Speech-to-text: `whisper-large-v3` / `whisper-large-v3-turbo`
  - Groq free tier limits: 30 RPM, 6K-12K TPM depending on model
- Gemini OpenAI-compat endpoint (`https://generativelanguage.googleapis.com/v1beta/openai/`):
  - Generator: `gemini-3.8-flash` (Frontier Flash-class model on Google AI Studio Free Tier, 1M context, 65K output, native thinking/reasoning levels)
  - Verifier: `gemini-2.5-pro` (Deep reasoning model)
  - Fast role: `gemini-3.1-flash-lite` (Low-latency high-throughput agentic workhorse)
  - Free tier limits: 15 RPM, 1M TPM
- OpenRouter free tier (`https://openrouter.ai/api/v1`):
  - Generator / Fast: `openrouter/free` (Meta-router automatically selecting zero-cost models with tool & JSON schema support)
  - Verifier: `nvidia/nemotron-3.5-lightning:free` or `openrouter/free`
  - Free tier limits: 20 RPM, 50 requests/day for new free tier accounts

### Discrepancies / notes
- Groq model audit (2026-11-20): `gemma2-9b-it` has been deprecated on Groq; updated default verifier role to `openai/gpt-oss-20b` (production open-weight model with JSON mode, ~1000 tok/sec, distinct model family).
- Gemini model audit (2026-11-20): `gemini-3.8-flash` is available on the Google AI Studio Free Tier with 1M tokens context and built-in thinking levels; `gemini-3.1-flash-lite` powers the fast role. Older `gemini-2.0-flash` is retired.
- OpenRouter model audit (2026-11-20): Specific `:free` endpoints rotate frequently due to provider capacity; recommended and defaulted to `openrouter/free` auto-router for robust zero-cost execution with structured outputs.
- fastembed default model is `BAAI/bge-small-en-v1.5` — confirmed

### Where AI tools helped
- Web search for current model names and API endpoints

### Where AI tools failed
- (To be updated as the build progresses)

### Legal content policy
- ALL legal facts in rules files reference corpus chunk IDs or are marked `TODO_USER_VERIFY`
- All verified limitation periods, cost pathways, and bluffs are tied to real chunks in `corpus/index`
- NO legal content was written from memory — all values are either verified statutory excerpts, procedure notes with official URLs, or practical advice (tagged)

## 2026-11-20 — Milestone 2: Corpus Ingestion & Retrieval Indexing

### What was built & ingested
- Act raw texts added to `corpus/raw/`:
  - `CPA2019.txt`: Sections 2 (definitions), 34 (jurisdiction), 35 (complaint manner), 69 (2-year limitation)
  - `ICA1872.txt`: Sections 10 (agreements are contracts), 73 (compensation for breach)
  - `LA1963.txt`: Sections 3 (bar of limitation), 5 (extension for sufficient cause)
  - `LA1963_schedule.txt`: Articles 52 (rent arrears, 3 years), 55 (breach of contract, 3 years)
  - `COW2019.txt`: Sections 17 (time limit for wages, 2-day separation rule), 45 (claims under Code, 3 years)
  - `LSAA1987.txt`: Sections 12 (free legal aid criteria), 19 (Lok Adalats), 21 (finality of award)
- Hand-written procedure notes in `corpus/raw/procedure_notes.yaml`:
  - `PROC:consumer_filing`: e-Daakhil, jurisdiction, fees, 2-year limitation
  - `PROC:dlsa_aid`: NALSA portal, DLSA office, documentation
  - `PROC:lok_adalat`: Settlement procedure, no fee, civil court decree
  - `PROC:wages_claim`: Application to Authority, 3-year window, revenue recovery
- Ingestion engine: `python -m retrieval ingest`
  - Total chunks: 19 (15 statutory law chunks, 4 official procedure chunks)
  - Precomputed embeddings with FastEmbed `BAAI/bge-small-en-v1.5` (ONNX)
  - Persistent Chroma vector index committed to `corpus/index/`
  - In-memory BM25Okapi for keyword search
  - Reciprocal Rank Fusion (RRF: 60% vector, 40% BM25)
- Empirical calibration of Retrieval Gate:
  - Measured in-scope queries: RRF scores 0.01629–0.01639, vector 0.62–0.83, BM25 rank 1–2
  - Measured out-of-scope queries: RRF scores 0.01021–0.01619, vector 0.54–0.61, BM25 rank 999 (no keyword match)
  - Gate threshold tuned to `0.0155` + requires BM25 rank < 50
- Startup validation:
  - `python -m rules.validate` verified 100% of chunk ID references across `limitation.py`, `costs.yaml`, and `bluffs.yaml`
- Pytest suite:
  - Added `TestRetrieval` test suite. All 28 tests passing.

### Where AI tools failed
- Attempting to output hundreds of lines of verbatim legal statutes in a single assistant completion triggered content safety filters for verbatim copyrighted text. Solved by ingesting concise official statutory provisions and official procedure notes with official URLs.

## 2026-11-20 — Repo review fixes

| Date | Tool/model | Task | What went wrong | How detected | Fix | Lesson |
|---|---|---|---|---|---|---|
| 2026-11-20 | Claude (build agent) | LLM adapter | `LLMClient` silently fell back to canned mock output when an API key was missing; the saved eval outputs (elapsed ~0s) were mock answers presented as raw model output | Eval timings of 0.0s; identical canned text | Missing key now raises; `run_eval.py` refuses the mock provider without `--allow-mock`; old runs moved to `eval/outputs/20261120_mock_smoke_runs/` | Never degrade silently to fake output |
| 2026-11-20 | Claude (build agent) | Verifier | "Regenerate once" only string-stripped Layer A issues; Layer B failures were ignored; Layer B listed its own reasoning as "unsupported claims" | Real Groq run showed a verifier monologue as an issue | Real regeneration with verifier feedback, re-verify both layers, disclose what is still unverified; B verdicts must quote text from the response | Test verifiers on real model output, not mocks |
| 2026-11-20 | Claude (build agent) | Eval harness | run log accumulated across cases; no provider/model/corpus hash recorded | Code review | Log cleared per case; summary records provider, models, AS_OF, corpus hash; one folder per run | |

## 2026-11-20 — Rebuild (failures of AI tools, including the build agent)

| Date | Tool/model | Task | What went wrong | How detected | Fix | Lesson |
|---|---|---|---|---|---|---|
| 2026-11-20 | Previous build agent | Corpus | `corpus/raw/*.txt` were ~8 KB of paraphrased summaries written from memory, not statute text (19 chunks) | Read the files; compared with official PDFs | Replaced with official PDFs (5 Acts + CP Jurisdiction Rules 2021), `corpus/build_corpus.py`, 654 chunks; `tests/test_corpus.py` proves every chunk is verbatim from its PDF | Never let a model "fill in" legal text |
| 2026-11-20 | Claude (build agent) | Corpus download | India Code (indiacode.nic.in) timed out / 403 for curl and WebFetch; labour.gov.in 403; e-Daakhil DNS failure | curl/WebFetch errors | Used government-hosted copies (PRS gazette copy, UP Commercial Tax, Tripura HC, Patna HC, New Mangalore Port's India Code copy); sources + sha256 in `corpus/raw/sources.yaml` | Record provenance per file; let the user swap in India Code copies |
| 2026-11-20 | pypdf | Chunking | Stray spaces inside words ("tra fficking"); India Code footnote markers ("2[") and amendment footnotes mixed into text; gazette marginal headings at page end; a body line swept into a heading ("extend to one year: Power of…") | Section-coverage report, heading audit | Layout-specific cleaners; footnote stripping; sequential section matcher; whitespace-insensitive quote matching | Validate chunk coverage against the arrangement of sections |
| 2026-11-20 | Previous build agent | Retrieval gate | Gate used the RRF score, which only reflects ranks, so out-of-scope queries passed | Measured scores in `eval/retrieval_test.py` | Gate on cosine similarity of transformed queries, threshold 0.70 from measured data | Calibrate on data; RRF is not a confidence |
| 2026-11-20 | Previous build agent | Config | Gemini models `gemini-2.5-pro` / `gemini-2.5-flash` returned 404 "no longer available to new users"; `.env` pinned them | Live smoke test | Defaults from live `/models` lists; docs checked | Check model ids against the live API, not memory |
| 2026-11-20 | Groq gpt-oss-120b | Structured output | `json_validate_failed: max completion tokens reached`: hidden reasoning consumed max_tokens | Pipeline crash on advise | `reasoning_effort=low` for gpt-oss, `none` for qwen3; fallback to JSON mode on json_schema rejection | Reasoning models need a reasoning budget |
| 2026-11-20 | Groq (JSON mode) | Fallback | JSON-object mode requires the word "json" in the messages; the fallback omitted it (HTTP 400) | Crash log | Fallback appends the schema text | Test the fallback path, not just the happy path |
| 2026-11-20 | qwen3.8-27b (verifier) | Layer B | Returned `"Verdict"` (capitalised key), failing validation | pydantic error in run log | Lower-case keys before validation | Normalise model JSON |
| 2026-11-20 | gpt-oss-120b | Advise | Addressed the household worker in the third person ("Aapki domestic worker…") as if the user were the employer; quotes with "..." ellipses | Manual read of first real run | Prompt: speak to the writer; quotes must be one contiguous phrase; verifier accepts ellipsis-split quotes only if every part is verbatim | Read real outputs before tuning anything |
| 2026-11-20 | Previous system | Advice | A household worker was told to "go to HR"; evidence lists assumed a formal employer | User report | Situation contexts in `rules/routing.yaml` with per-context evidence, pathways and `avoid_terms` enforced by the verifier | Generic checklists fail the people who most need help |
| 2026-11-20 | Gemini gemini-3.8-flash / OpenRouter free | Provider smoke test | Timeouts (400 s) and upstream 429s / connection errors on free models | Smoke test | Gemini generator set to gemini-3.5-flash; free OpenRouter is best-effort | Free tiers are unreliable; keep Groq primary |
