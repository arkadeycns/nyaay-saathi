# Eval run 20261120_214932_groq

Provider `groq` · models {'generator': 'openai/gpt-oss-120b', 'verifier': 'qwen/qwen3.8-27b', 'fast': 'openai/gpt-oss-20b'} · corpus `55918536b552b336` · AS_OF 2026-11-01

| Metric | Value |
|---|---|
| cases_run | 3 |
| cases_passed | 1 |
| skipped | 0 |
| errors | 1 |
| citation_validity | 1.0 |
| law_claims_checked | 6 |
| unsupported_leakage | 0.091 |
| claims_total | 11 |
| escalation_recall | None |
| escalation_precision | None |
| bait_pass_rate | None |
| legacy_map_hit_rate | None |
| context_fit_rate | 0.5 |
| category_accuracy | 1.0 |
| outcome_accuracy | 1.0 |
| latency_p50_s | 58.77 |
| latency_p95_s | 58.77 |
| tokens_per_session_mean | 13456 |
| est_cost_per_session_usd | 0.00324 |

Cost uses {'note': 'TODO_USER_VERIFY against current Groq pricing page', 'input_per_m': 0.15, 'output_per_m': 0.6} (assumption, not a quote).

| Case | Bucket | Outcome | Pass | Failed checks |
|---|---|---|---|---|
| A01_househelp_unpaid | A | error | ❌ | ran |
| A02_driver_household | A | full | ✅ |  |
| A03_shop_helper | A | full | ❌ | context, no_forbidden_text (found ['HR', 'salary slip']) |