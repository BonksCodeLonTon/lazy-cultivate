"""One-shot generator for elemental forge materials + their drop entries.

Produces 18 G2 materials (one damage-biaser + one resistance-biaser per
element) plus loot-table entries on zones 4–6 (mid-tier G2 zones).

  - Damage variant biases ``pfx_dmg_<elem>`` (3× roll weight).
  - Resistance variant biases ``sfx_res_<elem>``.
  - Both have ``shop_price_merit: 2000`` (matches other G2 mats).

Idempotent — running twice skips entries that already exist.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATERIALS_PATH = ROOT / "src" / "data" / "items" / "elemental_materials.json"
LOOT_PATHS = [
    ROOT / "src" / "data" / "loot_tables" / "zone_04.json",
    ROOT / "src" / "data" / "loot_tables" / "zone_05.json",
    ROOT / "src" / "data" / "loot_tables" / "zone_06.json",
]
LOOT_KEYS = ["LootZone_4", "LootZone_5", "LootZone_6"]
DROP_WEIGHT = 60_000

# Each element gets a pair of thematic Vietnamese names.
ELEMENT_INFO: dict[str, dict[str, str]] = {
    "kim":   {"label": "Kim",   "dmg_vi": "Kim Sát Tinh",   "res_vi": "Kim Hộ Phù"},
    "moc":   {"label": "Mộc",   "dmg_vi": "Mộc Sát Tinh",   "res_vi": "Mộc Hộ Phù"},
    "thuy":  {"label": "Thủy",  "dmg_vi": "Thủy Sát Tinh",  "res_vi": "Thủy Hộ Phù"},
    "hoa":   {"label": "Hỏa",   "dmg_vi": "Hỏa Sát Tinh",   "res_vi": "Hỏa Hộ Phù"},
    "tho":   {"label": "Thổ",   "dmg_vi": "Thổ Sát Tinh",   "res_vi": "Thổ Hộ Phù"},
    "loi":   {"label": "Lôi",   "dmg_vi": "Lôi Sát Tinh",   "res_vi": "Lôi Hộ Phù"},
    "phong": {"label": "Phong", "dmg_vi": "Phong Sát Tinh", "res_vi": "Phong Hộ Phù"},
    "quang": {"label": "Quang", "dmg_vi": "Quang Sát Tinh", "res_vi": "Quang Hộ Phù"},
    "am":    {"label": "Ám",    "dmg_vi": "Ám Sát Tinh",    "res_vi": "Ám Hộ Phù"},
}


def _build_materials() -> list[dict]:
    out: list[dict] = []
    for elem, info in ELEMENT_INFO.items():
        out.append({
            "key": f"MatElemDmg{elem.capitalize()}",
            "vi": info["dmg_vi"],
            "en": f"{info['label']}-Slay Essence",
            "type": "material",
            "grade": 2,
            "shop_price_merit": 2000,
            "affix_bias": [f"pfx_dmg_{elem}"],
            "description_vi": (
                f"Tinh hoa {info['label']} ngưng tụ trong lò luyện — luyện chế "
                f"trang bị thiên về sát thương {info['label']}."
            ),
        })
        out.append({
            "key": f"MatElemRes{elem.capitalize()}",
            "vi": info["res_vi"],
            "en": f"{info['label']}-Guard Talisman",
            "type": "material",
            "grade": 2,
            "shop_price_merit": 2000,
            "affix_bias": [f"sfx_res_{elem}"],
            "description_vi": (
                f"Phù linh hấp thụ năng lượng {info['label']} — luyện chế "
                f"trang bị thiên về kháng {info['label']}."
            ),
        })
    return out


def _write_materials(entries: list[dict]) -> int:
    """Merge ``entries`` into the elemental materials JSON. Returns count added."""
    existing: list[dict] = []
    if MATERIALS_PATH.exists():
        with MATERIALS_PATH.open(encoding="utf-8") as f:
            existing = json.load(f)
    existing_keys = {e["key"] for e in existing}

    added = 0
    for entry in entries:
        if entry["key"] in existing_keys:
            continue
        existing.append(entry)
        added += 1

    MATERIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MATERIALS_PATH.open("w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return added


def _patch_loot_tables(material_keys: list[str]) -> int:
    """Append drop entries to each mid-tier zone loot table. Returns count added."""
    added = 0
    for path, table_key in zip(LOOT_PATHS, LOOT_KEYS):
        with path.open(encoding="utf-8") as f:
            data: dict[str, list[dict]] = json.load(f)
        table = data.get(table_key)
        if table is None:
            continue
        existing_drops = {e["item_key"] for e in table}
        for key in material_keys:
            if key in existing_drops:
                continue
            table.append({
                "item_key": key,
                "weight": DROP_WEIGHT,
                "qty_min": 1,
                "qty_max": 1,
            })
            added += 1
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return added


def main() -> None:
    materials = _build_materials()
    n_mats = _write_materials(materials)
    n_drops = _patch_loot_tables([m["key"] for m in materials])
    print(f"materials added: {n_mats} (file: {MATERIALS_PATH.relative_to(ROOT)})")
    print(f"loot drops added: {n_drops} across {len(LOOT_PATHS)} zones")


if __name__ == "__main__":
    main()
