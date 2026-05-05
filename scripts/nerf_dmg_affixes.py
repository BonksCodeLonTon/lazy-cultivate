"""One-shot rebalance: heavily nerfs final/elemental damage affixes from
equipment so a maxed late-game roll is comparable to a strong constitution
stat instead of dwarfing it (~85-87% reduction at G9).

Affixes touched:
  - pfx_final_dmg          (universal final dmg prefix)
  - sfx_dmg_bonus          (universal final dmg suffix)
  - pfx_dmg_elem_all       (general elemental dmg prefix)
  - pfx_dmg_<elem> × 9     (specific elemental dmg prefix; 2× general)

Resistance affixes are NOT touched.

Idempotent — values are overwritten with the new curves regardless of the
existing values.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AFFIX_PATH = ROOT / "src" / "data" / "equipment" / "affixes.json"

# New nerfed curves. Shape preserved (~1.41× growth/grade), magnitudes cut so
# G9 max is in the +15–20% band instead of +100%+ from a single affix.

PREFIX_DMG_CURVE = [
    [0.005, 0.012],
    [0.010, 0.020],
    [0.018, 0.032],
    [0.028, 0.048],
    [0.042, 0.068],
    [0.060, 0.095],
    [0.082, 0.130],
    [0.108, 0.165],
    [0.140, 0.210],
]

# Suffix is ~75% of prefix.
SUFFIX_DMG_CURVE = [
    [0.004, 0.009],
    [0.008, 0.015],
    [0.014, 0.024],
    [0.022, 0.036],
    [0.032, 0.052],
    [0.045, 0.072],
    [0.062, 0.098],
    [0.082, 0.125],
    [0.105, 0.158],
]

ELEMENTS = ("kim", "moc", "thuy", "hoa", "tho", "loi", "phong", "quang", "am")
SPECIFIC_MULT = 2.0


def _scale(curve, mult):
    return [[round(lo * mult, 4), round(hi * mult, 4)] for lo, hi in curve]


def main() -> None:
    with AFFIX_PATH.open(encoding="utf-8") as f:
        affixes: list[dict] = json.load(f)

    by_key = {a["key"]: a for a in affixes}

    targets: dict[str, list[list[float]]] = {
        "pfx_final_dmg":    [list(p) for p in PREFIX_DMG_CURVE],
        "sfx_dmg_bonus":    [list(p) for p in SUFFIX_DMG_CURVE],
        "pfx_dmg_elem_all": [list(p) for p in PREFIX_DMG_CURVE],
    }
    for elem in ELEMENTS:
        targets[f"pfx_dmg_{elem}"] = _scale(PREFIX_DMG_CURVE, SPECIFIC_MULT)

    updated: list[str] = []
    missing: list[str] = []
    for key, curve in targets.items():
        if key not in by_key:
            missing.append(key)
            continue
        by_key[key]["by_grade"] = curve
        updated.append(key)

    with AFFIX_PATH.open("w", encoding="utf-8") as f:
        json.dump(affixes, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"updated {len(updated)} affix curves:")
    for k in updated:
        print(f"  ~ {k}")
    if missing:
        print("missing keys (skipped):")
        for k in missing:
            print(f"  ! {k}")


if __name__ == "__main__":
    main()
