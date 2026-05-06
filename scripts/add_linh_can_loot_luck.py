"""Add per-tier ``loot_luck_bonus`` to linh_can enemies.

All four tiers of a linh_can dungeon (T1 R2 → Apex R8) currently share one
loot table per element, so a low-tier kill drops at the same rate as the
boss. This adds an additive ``loot_luck_bonus`` field per enemy so the
combat session's ``_roll_loot`` can multiply weights by ``(1 + luck_pct)``
proportional to the kill's tier.

Tuning chosen so the apex pool (~1.8% baseline) climbs to ~5.4% on the
boss while T1 mobs stay at the documented baseline:

    T1 (R2) : +0.0   — no change
    T2 (R4) : +0.5   — 1.5× weight
    T3 (R6) : +1.2   — 2.2× weight
    Apex(R8): +2.0   — 3.0× weight (POOL_RANGE caps any 1M-wt entry)

Run from repo root:
    python scripts/add_linh_can_loot_luck.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ENEMY_DIR = Path("src/data/enemies/linh_can")

REALM_LUCK: dict[int, float] = {
    2: 0.0,
    4: 0.5,
    6: 1.2,
    8: 2.0,
}


def _migrate(enemy: dict) -> bool:
    realm = int(enemy.get("realm_level", 0))
    bonus = REALM_LUCK.get(realm)
    if bonus is None:
        return False
    if enemy.get("loot_luck_bonus") == bonus:
        return False
    enemy["loot_luck_bonus"] = bonus
    return True


def main() -> None:
    total = 0
    for path in sorted(ENEMY_DIR.glob("*.json")):
        enemies = json.loads(path.read_text(encoding="utf-8"))
        changed = sum(_migrate(e) for e in enemies)
        if changed:
            path.write_text(
                json.dumps(enemies, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        total += changed
        print(f"  {path.name}: {changed} entries updated")
    print(f"Done. {total} enemies updated.")


if __name__ == "__main__":
    main()
