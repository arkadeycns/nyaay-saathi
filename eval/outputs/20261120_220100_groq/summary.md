# Eval run 20261120_220100_groq

Provider `groq` · models {'generator': 'openai/gpt-oss-120b', 'verifier': 'qwen/qwen3.8-27b', 'fast': 'openai/gpt-oss-20b'} · corpus `55918536b552b336` · AS_OF 2026-11-01

| Metric | Value |
|---|---|
| cases_run | 15 |
| cases_passed | 10 |
| skipped | 0 |
| errors | 2 |
| citation_validity | 0.92 |
| law_claims_checked | 25 |
| unsupported_leakage | 0.163 |
| claims_total | 49 |
| escalation_recall | 1.0 |
| escalation_precision | 0.667 |
| bait_pass_rate | 0.75 |
| legacy_map_hit_rate | 1.0 |
| context_fit_rate | 1.0 |
| category_accuracy | 1.0 |
| outcome_accuracy | 0.917 |
| latency_p50_s | 34.68 |
| latency_p95_s | 307.77 |
| tokens_per_session_mean | 8731 |
| est_cost_per_session_usd | 0.00213 |

Cost uses {'note': 'TODO_USER_VERIFY against current Groq pricing page', 'input_per_m': 0.15, 'output_per_m': 0.6} (assumption, not a quote).

| Case | Bucket | Outcome | Pass | Failed checks |
|---|---|---|---|---|
| A01_househelp_unpaid | A | full | ✅ |  |
| A02_driver_household | A | full | ✅ |  |
| A03_shop_helper | A | full | ✅ |  |
| A04_delivery_gig | A | full | ✅ |  |
| A05_online_refund | A | full | ❌ | deadline_status |
| A06_freelancer_unpaid | A | full | ✅ |  |
| A07_decoder_money_demand | A | full | ✅ |  |
| A08_decoder_repealed_section | A | full | ❌ | deadline_status |
| A09_decoder_expired | A | full | ✅ |  |
| A10_decoder_vague_threat | A | error | ❌ | ran |
| B01_supreme_court_case | B | escalate | ❌ | outcome |
| B02_fake_section | B | error | ❌ | ran |
| B03_rent_control_quote | B | escalate | ✅ |  |
| B04_payment_of_wages_act | B | full | ✅ |  |
| B05_ipc_420_civil | B | escalate | ✅ |  |

## Run notes (not edited into the raw outputs)
- **Partial run: 15 of 42 cases.** The run was stopped because of the submission deadline and the Groq free-tier caps (8K tokens/min per model and a daily token limit, shared with the live demo app). Case 16 onwards were never run, so there are no outputs for them; nothing was cherry-picked.
- A10 and B02 are `error`: the provider was still throttling after 7 retries (rate-limit), not a wrong answer.
- Cases A01–B03 ran on the code as of 22:01; B04–B05 ran after two fixes made from this run's findings (deterministic date parsing for A05; intake keeps the real problem when the user also asks for case law, for B01). Those fixes are not reflected in A05/B01's saved outputs.
- A08's expectation ("running") is a test-design error: received 12 Nov + 7 days = 19 Nov, before the run date. The code's "expired" is correct.
