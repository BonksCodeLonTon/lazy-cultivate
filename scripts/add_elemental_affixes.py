"""One-shot generator that appends elemental damage prefixes + elemental
resistance suffixes to ``src/data/equipment/affixes.json``.

Design:
- Damage:    prefix-only.   General ``pfx_dmg_elem_all`` mirrors the
             ``pfx_final_dmg`` curve (it is one notch narrower in scope:
             elemental rather than universal). Each specific
             ``pfx_dmg_<elem>`` doubles the general values.
- Resistance: suffix-only. Each specific ``sfx_res_<elem>`` doubles the
             values of the existing general ``sfx_res_all``.

Idempotent — running twice will skip keys that already exist.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AFFIX_PATH = ROOT / "src" / "data" / "equipment" / "affixes.json"

# Elements + Vietnamese flavor names. Order matches the 9 Linh Căn.
ELEMENT_VI = {
    "kim":   ("Kim",   "Kim Bích"),
    "moc":   ("Mộc",   "Mộc Linh"),
    "thuy":  ("Thủy",  "Thủy Bảo"),
    "hoa":   ("Hỏa",   "Hỏa Hồ"),
    "tho":   ("Thổ",   "Thổ Vệ"),
    "loi":   ("Lôi",   "Lôi Bế"),
    "phong": ("Phong", "Phong Tỵ"),
    "quang": ("Quang", "Quang Trừ"),
    "am":    ("Ám",    "Ám Trấn"),
}

# Damage curve — heavily nerfed from the original ``pfx_final_dmg`` so a
# G9 max roll lands in the +15–20% band instead of +100%+ (kept in sync
# with ``scripts/nerf_dmg_affixes.py``).
DMG_CURVE = [
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
RES_CURVE = [
    [0.015, 0.03], [0.03, 0.05], [0.05, 0.07], [0.07, 0.107], [0.107, 0.163],
    [0.163, 0.249], [0.249, 0.381], [0.381, 0.582], [0.582, 0.889],
]

DMG_SLOTS = ["weapon", "ring", "amulet", "glove"]
RES_SLOTS = ["all"]

SPECIFIC_MULT = 2.0   # specific = 2× general; rationale: 1/9 element coverage


def _scale(curve: list[list[float]], mult: float) -> list[list[float]]:
    return [[round(lo * mult, 4), round(hi * mult, 4)] for lo, hi in curve]


def build_entries() -> list[dict]:
    out: list[dict] = []

    # ── Damage prefixes ───────────────────────────────────────────────
    out.append({
        "key": "pfx_dmg_elem_all",
        "vi": "Vạn Tượng Sát",
        "type": "prefix",
        "stat": "element_dmg_all",
        "is_pct": True,
        "slots": list(DMG_SLOTS),
        "by_grade": [list(pair) for pair in DMG_CURVE],
    })
    for elem, (vi_short, _) in ELEMENT_VI.items():
        out.append({
            "key": f"pfx_dmg_{elem}",
            "vi": f"{vi_short} Sát",
            "type": "prefix",
            "stat": f"element_dmg_{elem}",
            "is_pct": True,
            "slots": list(DMG_SLOTS),
            "by_grade": _scale(DMG_CURVE, SPECIFIC_MULT),
        })

    # ── Resistance suffixes ───────────────────────────────────────────
    for elem, (_, vi_long) in ELEMENT_VI.items():
        out.append({
            "key": f"sfx_res_{elem}",
            "vi": f"của {vi_long}",
            "type": "suffix",
            "stat": f"res_{elem}",
            "is_pct": True,
            "slots": list(RES_SLOTS),
            "by_grade": _scale(RES_CURVE, SPECIFIC_MULT),
        })

    return out


def main() -> None:
    with AFFIX_PATH.open(encoding="utf-8") as f:
        affixes: list[dict] = json.load(f)
    existing = {a["key"] for a in affixes}

    new_entries = build_entries()
    added: list[str] = []
    for entry in new_entries:
        if entry["key"] in existing:
            continue
        affixes.append(entry)
        added.append(entry["key"])

    with AFFIX_PATH.open("w", encoding="utf-8") as f:
        json.dump(affixes, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"added {len(added)} new affixes:")
    for k in added:
        print(f"  + {k}")


if __name__ == "__main__":
    main()
