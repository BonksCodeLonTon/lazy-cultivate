"""Balance-band guardrail — every Thể Chất must fit the tunable envelope.

The envelope lives in ``docs/constitution_balance_bands.json`` (THE file to
tune when a new body deliberately needs more room). For each process body this
suite sums the CUMULATIVE L9 kit — flat ``stat_bonuses`` + every milestone's
``stat_bonuses`` + ``per_level_growth`` × (max_level − 1) — into the band
categories and asserts each stays ≤ its rarity's max. A failure means a new or
edited body power-creeps past the existing roster: either trim the body or
consciously raise the band (with the anchor comment updated).

Also enforces the no-total-immunity rule: any authored stat key containing
``immune``/``resist``/``negate`` must stay ≤ ``resist_immunity_chance_max``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.data.registry import registry

BANDS_PATH = Path(__file__).parent.parent / "docs" / "constitution_balance_bands.json"


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


@pytest.fixture(scope="session")
def bands() -> dict:
    return json.loads(BANDS_PATH.read_text(encoding="utf-8"))


def _process_bodies() -> list[dict]:
    return [c for c in registry.constitutions.values() if c.get("process")]


def _kit_layers(const_data: dict):
    """Yield (stat_bonuses, multiplier) for the cumulative-L9 sum."""
    yield const_data.get("stat_bonuses") or {}, 1
    process = const_data.get("process") or {}
    for block in (process.get("levels") or {}).values():
        yield block.get("stat_bonuses") or {}, 1
    growth = process.get("per_level_growth") or {}
    if growth:
        yield growth, int(process.get("max_level", 9)) - 1


def _category_total(const_data: dict, spec: dict) -> float:
    total = 0.0
    flat_keys = spec.get("includes", [])
    nested_keys = spec.get("includes_nested", [])
    for sb, mult in _kit_layers(const_data):
        for key in flat_keys:
            val = sb.get(key, 0)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                total += float(val) * mult
        for key in nested_keys:
            nested = sb.get(key) or {}
            if isinstance(nested, dict):
                total += sum(
                    float(v) for v in nested.values()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)
                ) * mult
    return total


def _rarity_cap(spec: dict, rarity: str) -> float:
    caps = spec["max"]
    # Unknown future rarities fall back to the mythic (widest) band so a new
    # tier surfaces as a conscious band edit, not a KeyError.
    return float(caps.get(rarity, caps["mythic"]))


def test_bands_file_wellformed(bands):
    assert bands["categories"], "bands file must define categories"
    for name, spec in bands["categories"].items():
        assert spec.get("includes") or spec.get("includes_nested"), name
        assert "legendary" in spec["max"] and "mythic" in spec["max"], name


@pytest.mark.parametrize(
    "const_key", [c["key"] for c in _process_bodies()] or ["<no bodies>"],
)
def test_body_within_bands(const_key, bands):
    const_data = registry.get_constitution(const_key)
    assert const_data is not None
    rarity = const_data.get("rarity", "legendary")
    violations = []
    for name, spec in bands["categories"].items():
        total = _category_total(const_data, spec)
        cap = _rarity_cap(spec, rarity)
        if total > cap + 1e-9:
            violations.append(f"{name}: {total:.3f} > {cap} ({rarity})")
    assert not violations, (
        f"{const_key} exceeds balance bands — trim the body or deliberately "
        f"raise docs/constitution_balance_bands.json:\n  " + "\n  ".join(violations)
    )


@pytest.mark.parametrize(
    "const_key", [c["key"] for c in _process_bodies()] or ["<no bodies>"],
)
def test_no_total_immunity_chances(const_key, bands):
    """Any immune/resist/negate chance authored on a body stays ≤ the cap —
    total (1.0) immunity is banned by design (the #24 Tiêu Dao ruling)."""
    const_data = registry.get_constitution(const_key)
    cap = float(bands["resist_immunity_chance_max"])
    offenders = []
    for sb, _mult in _kit_layers(const_data):
        for key, val in sb.items():
            if not any(w in key for w in ("immune", "resist", "negate")):
                continue
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                # Bool flags (pill_toxin_immune, poison_immunity) are hard
                # immunities to a NARROW, named lane — allowed by review, the
                # chance rule targets broad-negation percentages.
                continue
            if 0 < float(val) <= 1.0 and float(val) > cap:
                offenders.append(f"{key}={val}")
    assert not offenders, f"{const_key}: immunity chances above {cap}: {offenders}"
