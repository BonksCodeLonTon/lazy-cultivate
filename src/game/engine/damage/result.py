"""DamageResult dataclass."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DamageResult:
    raw: int
    final: int
    is_crit: bool
    is_evaded: bool
    element: str | None
