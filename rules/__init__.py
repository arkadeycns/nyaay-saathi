"""Rules loader. YAML files in this folder are the deterministic half of the system."""
from __future__ import annotations

import functools
import pathlib
from typing import Any

import yaml

import config

RULES_DIR = pathlib.Path(__file__).parent


@functools.lru_cache(maxsize=None)
def load(name: str) -> Any:
    with open(RULES_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def verified_only(entries: list[dict]) -> list[dict]:
    """Entries marked verified: false load only with ALLOW_UNVERIFIED=1 (and are tagged in the UI)."""
    return [e for e in entries if e.get("verified", True) or config.ALLOW_UNVERIFIED]
