"""Set per-realm ``base_res`` for normal-dungeon enemies (R1-R10).

Combined with the auto-scaling ``ENEMY_BASE_ELEM_RES`` (10 % own-element,
multiplied by ``realm_scale`` and capped at 0.35 in ``builders.py``), this
gives a smooth resistance curve that brings R10 enemies to the 0.75 player
cap (``MAX_ELEMENTAL_RES``).

Two layers per enemy:

* ``OWN_BONUS[realm]`` — added to the enemy's own element on top of the
  auto-scaling res. Sized so R10 own = 0.35 (auto) + 0.42 = 0.75 cap.
* ``OFF_BONUS[realm]`` — applied to **every other element** at high realms,
  so R7+ enemies aren't trivially cheesed by an off-element pivot. Lower
  than own-bonus so the right element still feels meaningfully better.

Effective own-element / off-element resistance a same-realm player sees:

    R1 ≈ 11 / 0   R2 ≈ 14 / 0   R3 ≈ 20 / 0   R4 ≈ 28 / 0
    R5 ≈ 40 / 0   R6 ≈ 53 / 0   R7 ≈ 63 / 3   R8 ≈ 67 / 6
    R9 ≈ 71 / 10  R10 = 75 / 15

Early realms intentionally rely on the auto own-res alone so brand-new
players aren't gated by elemental resists they have no tools to pierce.

The script overwrites every base_res key it manages and preserves any
unrecognised entries. A bonus of 0 removes the matching entry.

Run from repo root:
    python scripts/add_enemy_ele_res.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ENEMY_DIR = Path("src/data/enemies/normal")

ALL_ELEMENTS = ["kim", "moc", "thuy", "hoa", "tho", "phong", "loi", "quang", "am"]

# Own-element bonus, stacks on top of auto-scaling res (cap 0.35).
OWN_BONUS: dict[int, float] = {
    1: 0.00, 2: 0.00, 3: 0.03, 4: 0.06, 5: 0.12,
    6: 0.18, 7: 0.28, 8: 0.32, 9: 0.36, 10: 0.42,
}

# Off-element bonus — applied to every element except the enemy's own.
# Below R7 there's no off-element pressure; R7+ scales up so endgame
# off-element pivots can't dodge resistance entirely.
OFF_BONUS: dict[int, float] = {
    1: 0.00, 2: 0.00, 3: 0.00, 4: 0.00, 5: 0.00,
    6: 0.00, 7: 0.03, 8: 0.06, 9: 0.10, 10: 0.15,
}


def _migrate(enemy: dict, realm_level: int) -> bool:
    elem = enemy.get("element")
    if not elem:
        return False
    own = OWN_BONUS.get(realm_level)
    off = OFF_BONUS.get(realm_level)
    if own is None or off is None:
        return False

    base_res = dict(enemy.get("base_res") or {})
    desired: dict[str, float] = {}
    if own > 0:
        desired[elem] = own
    if off > 0:
        for e in ALL_ELEMENTS:
            if e == elem:
                continue
            desired[e] = off

    new_block = {k: v for k, v in base_res.items() if k not in ALL_ELEMENTS}
    new_block.update(desired)

    if new_block == base_res:
        return False
    if new_block:
        enemy["base_res"] = new_block
    else:
        enemy.pop("base_res", None)
    return True


def main() -> None:
    total = 0
    for realm in range(1, 11):
        path = ENEMY_DIR / f"realm_{realm:02d}.json"
        if not path.exists():
            continue
        enemies = json.loads(path.read_text(encoding="utf-8"))
        changed = sum(_migrate(e, realm) for e in enemies)
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
