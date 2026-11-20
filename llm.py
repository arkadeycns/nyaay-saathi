"""
Swappable LLM adapter over the OpenAI-compatible chat API (Groq, Gemini, OpenRouter).

complete(system, messages, role="generator"|"verifier"|"fast", schema=None)
  - roles map to models in config.PROVIDERS; nothing provider-specific lives outside config.
  - schema: a pydantic model -> JSON output, validated, with one repair retry.
  - 429s back off using the provider's retry-after hint when present.
  - every call is logged (role, model, tokens, latency) to the run log.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from typing import Any, Type, TypeVar

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

import config

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_run_log: list[dict] = []
_log_lock = threading.Lock()


def get_run_log() -> list[dict]:
    return list(_run_log)


def clear_run_log() -> None:
    """Reset between eval cases so each saved output holds only its own calls."""
    _run_log.clear()


def _log_call(role: str, model: str, usage: Any, latency_ms: float, ok: bool = True, note: str = "") -> None:
    entry = {
        "ts": round(time.time(), 3), "role": role, "model": model, "ok": ok,
        "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        "latency_ms": round(latency_ms, 1), "note": note,
    }
    with _log_lock:
        _run_log.append(entry)
        try:
            with open(config.RUN_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # read-only filesystems (Streamlit Cloud) still keep the in-memory log


class LLMError(RuntimeError):
    """Raised when the provider cannot produce a usable answer; the UI shows a friendly message."""


class _SchemaRejected(Exception):
    """Provider rejected json_schema output (e.g. Groq 'json_validate_failed'); retry in plain JSON mode."""


_THINK = re.compile(r"<(think|thought)>.*?</\1>", re.S | re.I)
_MAX_RETRIES = 7


class LLMClient:
    def __init__(self, provider_name: str | None = None) -> None:
        self.cfg = config.get_provider(provider_name)
        key = os.getenv(self.cfg.api_key_env, "")
        if not key:
            # Never fall back to canned output: it would look like a real answer.
            raise EnvironmentError(f"Provider '{self.cfg.name}' needs {self.cfg.api_key_env} in the environment.")
        client = OpenAI(base_url=self.cfg.base_url, api_key=key, timeout=90, max_retries=0)
        if config.LANGSMITH_ENABLED:
            from langsmith.wrappers import wrap_openai
            client = wrap_openai(client)
        self._client = client

    def model_for(self, role: str) -> str:
        return self.cfg.models[role]

    def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        role: str = "generator",
        schema: Type[T] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> str | T:
        model = self.cfg.models[role]
        msgs = [{"role": "system", "content": system}] + messages
        kwargs: dict[str, Any] = {"temperature": temperature, "max_tokens": max_tokens or config.MAX_OUTPUT_TOKENS}
        kwargs.update(_reasoning_kwargs(self.cfg.name, model))
        if schema is not None:
            if self.cfg.json_schema:
                kwargs["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": schema.__name__, "strict": False, "schema": schema.model_json_schema()}}
            else:
                kwargs["response_format"] = {"type": "json_object"}
                msgs[0]["content"] += "\n\nReply with ONLY a JSON object matching this JSON schema:\n" + json.dumps(schema.model_json_schema())
        try:
            raw = self._call(role, model, msgs, **kwargs)
        except _SchemaRejected as exc:
            logger.warning("json_schema rejected on %s (%s); falling back to JSON mode", model, str(exc)[:200])
            kwargs["response_format"] = {"type": "json_object"}
            msgs[0]["content"] += "\n\nReply with ONLY a JSON object matching this JSON schema:\n" + json.dumps(schema.model_json_schema())
            raw = self._call(role, model, msgs, **kwargs)
        if schema is None:
            return raw
        try:
            return schema.model_validate(_parse_json(raw))
        except (ValueError, ValidationError) as exc:
            logger.warning("structured output invalid (%s); one repair attempt", str(exc)[:200])
            repair = msgs + [
                {"role": "assistant", "content": raw[:4000]},
                {"role": "user", "content": f"That was not valid JSON for the schema ({str(exc)[:300]}). Reply with ONLY the corrected JSON object."},
            ]
            raw = self._call(role, model, repair, **kwargs)
            try:
                return schema.model_validate(_parse_json(raw))
            except (ValueError, ValidationError) as exc2:
                raise LLMError(f"{model} did not return valid structured output: {str(exc2)[:200]}") from exc2

    def _call(self, role: str, model: str, messages: list[dict], **kwargs: Any) -> str:
        for attempt in range(_MAX_RETRIES):
            t0 = time.time()
            try:
                resp = self._client.chat.completions.create(model=model, messages=messages, **kwargs)
                _log_call(role, model, resp.usage, (time.time() - t0) * 1000)
                return _THINK.sub("", resp.choices[0].message.content or "").strip()
            except RateLimitError as exc:
                # Groq's retry-after hint is often ~2 s while the per-minute token window needs longer;
                # take the larger of the hint and exponential backoff so retries aren't burnt in seconds.
                wait = max(_retry_after(exc) or 0.0, min(60.0, 3.0 * 2 ** attempt))
                _log_call(role, model, None, (time.time() - t0) * 1000, ok=False, note=f"429, waiting {wait:.1f}s")
                logger.warning("429 on %s; retry %d in %.1fs", model, attempt + 1, wait)
                time.sleep(wait)
            except (APITimeoutError, APIConnectionError) as exc:
                _log_call(role, model, None, (time.time() - t0) * 1000, ok=False, note=type(exc).__name__)
                if attempt >= 1:
                    raise LLMError(f"{self.cfg.name} did not respond ({type(exc).__name__}). Try again shortly.") from exc
            except APIStatusError as exc:
                _log_call(role, model, None, (time.time() - t0) * 1000, ok=False, note=f"HTTP {exc.status_code}")
                if exc.status_code == 400 and kwargs.get("response_format", {}).get("type") == "json_schema":
                    raise _SchemaRejected(str(exc)) from exc
                if exc.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                    time.sleep(2.0 * 2 ** attempt)
                    continue
                raise LLMError(f"{self.cfg.name} returned HTTP {exc.status_code}: {str(exc)[:200]}") from exc
        raise LLMError(f"{self.cfg.name} rate limit: still throttled after {_MAX_RETRIES} retries. Try again in a minute.")

    def transcribe(self, audio_bytes: bytes, language: str | None = None, suffix: str = ".wav") -> str:
        """Groq Whisper speech-to-text. language: 'hi', 'en' or None for auto-detect."""
        key = os.getenv("GROQ_API_KEY", "")
        if not key:
            raise EnvironmentError("GROQ_API_KEY is required for voice transcription.")
        client = OpenAI(base_url=config.WHISPER_BASE_URL, api_key=key, timeout=120)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            path = f.name
        t0 = time.time()
        try:
            with open(path, "rb") as fh:
                kw = {"language": language} if language else {}
                resp = client.audio.transcriptions.create(model=config.WHISPER_MODEL, file=fh, **kw)
            _log_call("whisper", config.WHISPER_MODEL, None, (time.time() - t0) * 1000)
            return resp.text
        finally:
            os.unlink(path)


def _reasoning_kwargs(provider: str, model: str) -> dict:
    """Reasoning models spend hidden tokens before answering; without a cap they can exhaust
    max_tokens and return no JSON (seen on Groq gpt-oss: 'max completion tokens reached')."""
    if provider == "groq" and "gpt-oss" in model:
        return {"reasoning_effort": "low"}
    if provider == "groq" and "qwen3" in model:
        return {"reasoning_effort": "none"}
    if provider == "gemini" and model.startswith("gemini"):
        return {"reasoning_effort": "low"}   # default thinking timed out (>60 s) on free tier, 2026-11-20
    return {}


def _retry_after(exc: RateLimitError) -> float | None:
    try:
        hdr = exc.response.headers.get("retry-after")
        return min(60.0, float(hdr) + 0.5) if hdr else None
    except Exception:  # header absent or malformed
        return None


def _parse_json(raw: str) -> Any:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in output")
    return _lower_keys(json.loads(text[start:end + 1]))


def _lower_keys(obj: Any) -> Any:
    """Some models capitalise keys ("Verdict"); our schemas are all lower-case."""
    if isinstance(obj, dict):
        return {(k.lower() if isinstance(k, str) else k): _lower_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_lower_keys(v) for v in obj]
    return obj


_default: dict[str, LLMClient] = {}


def get_llm() -> LLMClient:
    """Process-wide client for the active provider (config.ACTIVE_PROVIDER may change in evals)."""
    name = config.ACTIVE_PROVIDER
    if name not in _default:
        _default[name] = LLMClient(name)
    return _default[name]
