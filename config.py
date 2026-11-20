"""
All runtime settings. Swapping the LLM provider is one change: LLM_PROVIDER=groq|gemini|openrouter.
No legal facts live here; they come from the corpus (corpus/processed) or rules/*.yaml.
"""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).parent
load_dotenv(ROOT / ".env")
# chromadb's bundled telemetry errors against its pinned posthog; disable it.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

AS_OF: str = os.getenv("AS_OF_DATE", "2026-11-20")      # corpus snapshot date shown to users
ALLOW_UNVERIFIED: bool = os.getenv("ALLOW_UNVERIFIED", "0") == "1"


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key_env: str
    models: dict[str, str]          # role -> model id
    json_schema: bool               # supports response_format json_schema (strict)
    data_note: str                  # what the provider does with inputs (shown in UI)


# Model ids checked against each provider's live /models endpoint and docs on 2026-11-20
# (see docs/build_log.md). The verifier is a different model family from the generator.
PROVIDERS: dict[str, ProviderConfig] = {
    "groq": ProviderConfig(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
        models={
            "generator": os.getenv("GROQ_GENERATOR_MODEL", "openai/gpt-oss-120b"),
            "verifier": os.getenv("GROQ_VERIFIER_MODEL", "qwen/qwen3.8-27b"),
            "fast": os.getenv("GROQ_FAST_MODEL", "openai/gpt-oss-20b"),
        },
        json_schema=True,
        data_note="Groq: 'By default, Groq does not retain customer data for inference requests' (console.groq.com/docs/your-data).",
    ),
    "gemini": ProviderConfig(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key_env="GEMINI_API_KEY",
        models={
            "generator": os.getenv("GEMINI_GENERATOR_MODEL", "gemini-3.5-flash"),
            "verifier": os.getenv("GEMINI_VERIFIER_MODEL", "gemma-4-31b-it"),
            "fast": os.getenv("GEMINI_FAST_MODEL", "gemini-3.5-flash-lite"),
        },
        json_schema=False,
        data_note="Gemini free tier: content is 'used to improve our products' (ai.google.dev pricing). Avoid real personal details.",
    ),
    "openrouter": ProviderConfig(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        models={
            "generator": os.getenv("OPENROUTER_GENERATOR_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free"),
            "verifier": os.getenv("OPENROUTER_VERIFIER_MODEL", "google/gemma-4-31b-it:free"),
            "fast": os.getenv("OPENROUTER_FAST_MODEL", "nvidia/nemotron-3.5-lightning:free"),
        },
        json_schema=False,
        data_note="OpenRouter free models: routed to third-party hosts whose data policies vary.",
    ),
}

ACTIVE_PROVIDER: str = os.getenv("LLM_PROVIDER", "groq")
MAX_OUTPUT_TOKENS: int = int(os.getenv("MAX_OUTPUT_TOKENS", "2500"))


def get_provider(name: str | None = None) -> ProviderConfig:
    return PROVIDERS[name or ACTIVE_PROVIDER]


# ── Retrieval ────────────────────────────────────────────────────
RETRIEVAL_K: int = int(os.getenv("RETRIEVAL_K", "6"))
BM25_WEIGHT: float = float(os.getenv("BM25_WEIGHT", "0.4"))
VECTOR_WEIGHT: float = float(os.getenv("VECTOR_WEIGHT", "0.6"))
CHUNK_PROMPT_CHARS: int = int(os.getenv("CHUNK_PROMPT_CHARS", "1400"))  # keeps prompts inside Groq's 8K TPM
# Retrieval gate: minimum cosine similarity of the best chunk for a transformed query.
# Measured 2026-11-20 (python -m eval.retrieval_test, 30 in-scope + 6 out-of-scope queries):
# in-scope min 0.704 / median 0.807; out-of-scope median 0.652, max 0.764 (cheque bounce,
# which the escalation rules catch first). Raw untransformed Hinglish scores lower, so the
# gate runs on transformed queries.
RETRIEVAL_GATE_THRESHOLD: float = float(os.getenv("RETRIEVAL_GATE_THRESHOLD", "0.70"))
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

# ── Escalation ───────────────────────────────────────────────────
# A product choice, not law: above this amount the stakes justify an advocate. Set by the deployer.
CLAIM_ESCALATION_THRESHOLD: float = float(os.getenv("CLAIM_THRESHOLD", "1000000"))
MAX_CLARIFY_ROUNDS: int = 1   # one round of at most 2 questions, then proceed with cannot_assess

# ── Act codes (must match corpus chunk-ID prefixes) ──────────────
ACT_CODES: dict[str, str] = {
    "Consumer Protection Act, 2019": "CPA2019",
    "Indian Contract Act, 1872": "ICA1872",
    "Limitation Act, 1963": "LA1963",
    "Code on Wages, 2019": "COW2019",
    "Legal Services Authorities Act, 1987": "LSAA1987",
    "Consumer Protection (Jurisdiction) Rules, 2021": "CPJR2021",
}

# ── Voice (Groq Whisper) ─────────────────────────────────────────
WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "whisper-large-v3")
WHISPER_BASE_URL: str = "https://api.groq.com/openai/v1"
MAX_AUDIO_SECONDS: int = 120

# ── Logging / tracing ────────────────────────────────────────────
RUN_LOG_PATH: str = os.getenv("RUN_LOG_PATH", "run_log.jsonl")
# LangSmith is opt-in (LANGSMITH_TRACING=true + LANGSMITH_API_KEY). Off by default because
# traces would send users' case text to a third party; use it for eval and dev only.
LANGSMITH_ENABLED: bool = os.getenv("LANGSMITH_TRACING", "").lower() == "true" and bool(os.getenv("LANGSMITH_API_KEY"))
