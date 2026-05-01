"""Drop engine — resolves drop tables into concrete loot.

Supports two drop modes per entry:

  Independent  (no pool_id)    — each entry rolls on its own; weight is in
                                  units of ``POOL_RANGE`` (1,000,000 = 100%).
                                  Multiple items can drop at once.

  Exclusive pool (pool_id="X") — all entries sharing the same pool_id compete.
                                  The pool fires once with a chance equal to the
                                  highest individual weight in the group, then one
                                  winner is selected proportionally by weight.
                                  Exactly zero or one item drops per pool per kill.

Optional per-entry fields:
  min_realm   (int, default 0)   — skip if player_realm_total < min_realm
  luck_scale  (float, default 1) — per-entry multiplier on top of global luck_pct

Entry format (JSON):
  {
    "item_key":  "MatHoaThanDan",
    "weight":    50000,       // out of 1,000,000 (5%) for independent; relative for pool
    "qty_min":   1,
    "qty_max":   2,
    "pool_id":   null,        // null = independent; string = exclusive pool key
    "min_realm": 0,
    "luck_scale": 1.0
  }
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

# Base weight reference — a weight of POOL_RANGE is a guaranteed drop (100%).
# Scaled from the original 100.0 to 1,000,000.0 to allow finer probability
# tuning (minimum step = 0.0001% when weight==1).
POOL_RANGE = 1_000_000.0


@dataclass
class DropResult:
    items: list[dict] = field(default_factory=list)  # [{"item_key": str, "quantity": int}]

    def merge(self) -> list[dict]:
        """Collapse duplicate item_keys by summing quantities."""
        totals: dict[str, int] = {}
        for entry in self.items:
            totals[entry["item_key"]] = totals.get(entry["item_key"], 0) + entry["quantity"]
        return [{"item_key": k, "quantity": v} for k, v in totals.items()]


def roll_drops(
    drop_table: list[dict],
    rng: random.Random,
    *,
    luck_pct: float = 0.0,
    player_realm_total: int = 0,
) -> DropResult:
    """Resolve a drop_table into concrete loot.

    Args:
        drop_table:          List of drop entry dicts from JSON.
        rng:                 Seeded random source (deterministic in tests).
        luck_pct:            Additive drop-rate bonus (0.10 → weight × 1.10).
        player_realm_total:  Used for min_realm gating.

    Returns:
        DropResult whose .items list may contain duplicates; call .merge() for
        a collapsed list before displaying or writing to inventory.
    """
    result = DropResult()
    pools: dict[str, list[dict]] = {}

    for entry in drop_table:
        if player_realm_total < entry.get("min_realm", 0):
            continue

        pool_id = entry.get("pool_id")
        if pool_id is None:
            _roll_independent(entry, rng, luck_pct, result)
        else:
            pools.setdefault(pool_id, []).append(entry)

    for pool_entries in pools.values():
        _roll_pool(pool_entries, rng, luck_pct, result)

    return result


# ── Internal helpers ──────────────────────────────────────────────────────────

def _effective_weight(entry: dict, luck_pct: float) -> float:
    """Scale a drop entry's weight by ``luck_pct``.

    ``luck_pct`` is signed: positive values boost the weight (good luck /
    grade bonus), negative values shrink it (used by Linh Căn dungeons to
    decay drop rate per realm). The result is clamped to
    ``[0, POOL_RANGE]`` so a sufficiently negative luck can't produce a
    negative weight that breaks pool selection arithmetic.
    """
    scale = entry.get("luck_scale", 1.0)
    raw = entry.get("weight", 10) * (1.0 + luck_pct * scale)
    return max(0.0, min(POOL_RANGE, raw))


def _pick_qty(entry: dict, rng: random.Random) -> int:
    return rng.randint(entry.get("qty_min", 1), entry.get("qty_max", 1))


def _roll_independent(entry: dict, rng: random.Random, luck_pct: float, result: DropResult) -> None:
    if rng.uniform(0, POOL_RANGE) < _effective_weight(entry, luck_pct):
        result.items.append({"item_key": entry["item_key"], "quantity": _pick_qty(entry, rng)})


def _roll_pool(entries: list[dict], rng: random.Random, luck_pct: float, result: DropResult) -> None:
    """Weighted exclusive selection within a pool.

    Pool activation chance = max individual weight in the group (clamped to
    ``POOL_RANGE`` = 1,000,000). If the pool fires, one entry is chosen
    proportionally by its effective weight.
    """
    weighted = [(e, _effective_weight(e, luck_pct)) for e in entries]
    pool_chance = max(w for _, w in weighted)

    if rng.uniform(0, POOL_RANGE) >= pool_chance:
        return

    total = sum(w for _, w in weighted)
    pick = rng.uniform(0, total)
    cumulative = 0.0
    for entry, w in weighted:
        cumulative += w
        if pick <= cumulative:
            result.items.append({"item_key": entry["item_key"], "quantity": _pick_qty(entry, rng)})
            return
