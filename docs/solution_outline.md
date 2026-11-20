# Nyaay Saathi: an honest AI legal helper for everyday problems in India

**Code:** github.com/arkadeycns/nyaay-saathi · **App:** https://nyaay-saathi-ckvuzcn6rdrrsmwsqdkazm.streamlit.app · **Raw outputs:** `eval/outputs/` in the repo · **Video:** VIDEO_URL

## 1. The problem I picked, and why
**Problem 2, the AI lawyer.** Most people never get legal help for small, common problems: wages not paid, a refund that never came, a client or friend who won't pay, a notice they don't understand. The hard part isn't generating fluent text. It's being **trustworthy** for someone who can't tell a real section from an invented one. I picked this problem because that gap is real, and the engineering question it poses is: how do you let a model help without letting it make up law?

**Who it is for:** a scared, first-time user, often an informal or household worker, typing Hinglish on a phone. Success means they understand where they stand, see realistic options with their money, time and emotional cost, know their deadline, get a usable first letter, and know when they need a real lawyer.

## 2. Assumptions and scope
- **Law covered:** central law only, as a fixed snapshot. Consumer Protection Act 2019, Indian Contract Act 1872, Limitation Act 1963, Code on Wages 2019, Legal Services Authorities Act 1987, and the Consumer Protection Jurisdiction Rules 2021. All come from official government-hosted PDFs, with each file's source and sha256 hash recorded (`corpus/MANIFEST.md`).
- **Out of scope, recognised and referred with safe steps:** state law (rent and security deposits, state wage rules), criminal law, family and property law, cheque bounce, any case law.
- **Languages:** English, Hindi and Hinglish text, plus voice input via Whisper. No OCR, no voice output.
- **Cost:** free tiers only (Groq + Streamlit Community Cloud). Gemini and OpenRouter work with one config change.
- **A product choice, not law:** claims above ₹10 lakh are escalated to an advocate.

## 3. How it works
Three tabs. **Advisor** is a chat that builds a case file and gives verified advice. **Notice Decoder** explains a notice and checks the law it cites. **Opponent Simulator** lets you rehearse against bluffs the other side typically uses.

**The core rule: code decides, the LLM drafts.**

| Step | Done by | Why |
|---|---|---|
| Crisis words, escalation triggers, blocked tactics (e.g. "FIR to scare him") | Code (YAML rules) | Must always fire; the LLM can add flags, never remove them |
| Turn the story into a case file | LLM (fast model) | Messy Hinglish needs a model |
| Amounts and dates kept only if the user's exact words contain them | Code | No invented facts |
| Situation (household worker, shop worker, online buyer…) → evidence list, option order, banned words | Code (`routing.yaml`) | Fixes generic advice such as telling a house-help to "ask HR" |
| Rewrite the question into search terms | LLM | Raw Hinglish found the right section 17/30 times; rewritten, 30/30 |
| Hybrid search (vector + BM25) and a refusal gate | Code | Gate threshold measured on 36 queries |
| Explanation as tagged claims (Law / Official procedure / Practical) | LLM (generator) | Plain language |
| **Verifier A:** cited chunk exists; quote is verbatim; every number or time limit appears in the cited text; no case law, repealed Acts or wrong-situation words | Code | Exact and auditable |
| **Verifier B:** each claim judged against only its cited text | LLM (different model family) | Catches a real section used for the wrong point |
| Deadlines, strength band, cost table, drafts | Code | The LLM never computes a deadline |

A claim that fails verification triggers one regeneration with the problems fed back. Anything still failing is removed, and the user is told how many statements were dropped. **Human in the loop:** the user confirms every fact before a draft is made, edits the voice transcript, and gives the date they received a notice. Rule entries drafted by AI stay hidden until reviewed (`verified: false`).

## 4. How it would scale
- **More law:** adding an Act means adding a PDF, a layout rule and a line in `sources.yaml`. The verbatim test and the rules validator stop paraphrased law from entering. State rent law would be the next pack, one state at a time.
- **More users:** retrieval is local (ONNX embeddings, committed index), so the only per-user cost is LLM tokens. Measured: ~8,700 tokens per advisor session (4–7 calls), about $0.002 (≈₹0.20) at an assumed $0.15/$0.60 per million input/output tokens (TODO_USER_VERIFY against Groq's price page). The free tier allows about 1–2 sessions a minute; the paid tier removes the waits.
- **Quality at scale:** each answer stores its verifier log, so failures can be sampled. The eval runs on any provider with one flag, and LangSmith traces dev and eval runs (opt-in only, because traces contain case text).
- **Next steps with budget:** a paralegal review queue for drafts, DLSA partnerships for hand-offs, saved case files with consent, more languages.

## 5. How I know it's good, and where it fails
- **Corpus:** `tests/test_corpus.py` proves all 654 chunks are word-for-word from the source PDFs. `rules.validate` proves every legal line in the rules quotes the corpus exactly. **61 tests** cover the deterministic code.
- **Retrieval:** right section in the top 5 for 30/30 rewritten queries; the gate blocks 5 of 6 out-of-scope queries (the 6th, cheque bounce, is caught earlier by escalation rules).
- **End-to-end eval:** 42 of my own cases written (realistic, hallucination bait, escalation, messy input, simulator, Hindi), with raw unedited outputs in `eval/outputs/`. Results from the **15 of 42 cases that ran** (the run was stopped by time constraints and the Groq free tier's per-minute and daily token caps, which the eval shares with the live app; the rest of the suite is ready to run with `python -m eval.run_eval --resume <run>`):

| Metric | Value |
|---|---|
| Cases passing all checks | 10 / 15 (2 of the 5 failures are rate-limit errors, not wrong answers) |
| Citation validity (law claims with an existing cite + verbatim quote) | 0.92 (25 law claims) |
| Claims verifier B marked partial/unsupported that still shipped (flagged in UI) | 0.16 |
| Escalation recall / precision | 1.00 / 0.67 |
| Hallucination-bait cases handled safely | 3 / 4 that completed |
| Context fit (no "HR"/"salary slip" for informal workers) | 1.00 |
| Repealed-Act detection | 1.00 |
| Latency p50 / p95 | 35 s / 308 s (p95 is free-tier rate-limit waiting) |

- **Where it fails:** escalation precision 0.67: B01 (asks for a Supreme Court case *and* describes a refund) was escalated instead of helped; fixed in the intake prompt after the run. A05 (online refund): the model copied "9 October 2026" but left the date field empty, so no deadline was shown. Fixed with a deterministic date parser (+6 tests), not re-run yet. A08: my own test expected "running" for a deadline that had actually passed, a test-design error; the code was right. About 19% of claims ship flagged "partially supported"; they are shown with a warning rather than hidden. Known limits: the Contract Act copy is a state transcription with typos; CPA 2019 is as enacted; whether a household counts as an "establishment" under the Code on Wages isn't settled by the text, so the tool says so and sends the person to the DLSA; free models rate-limit, so answers take 20–60 s; the verifier sometimes drops true statements (safer, but shorter answers); voice is untested on real recordings.
- **Where AI tools failed while I built it** (`docs/build_log.md`): the first corpus was paraphrased from memory; a rank-based retrieval gate passed nonsense queries; retired Gemini model IDs; hidden reasoning tokens used up the output budget; a verifier model returned the wrong JSON key; and the advice was addressed to the employer instead of the worker.
