"""Fill in missing realm tiers (R1, R3, R5, R7, R9, R10) for linh_can enemies.

Each element previously only had 4 entries at R2/R4/R6/R8. This adds the
6 missing tiers so the dungeon's enemy_pool covers the full R1-R10 curve.
The 4 existing entries are left untouched — only the new ones are appended,
then the file is sorted by realm_level for readability.

Stat curve interpolates between the existing R2-R8 anchors and extrapolates
out to R10. Off-element resistance kicks in at R5+ using the same buddy
element each apex already uses.

Run from repo root:
    python scripts/expand_linh_can_enemies.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ENEMY_DIR = Path("src/data/enemies/linh_can")

NEW_REALMS = [1, 3, 5, 7, 9, 10]

# Buddy element each apex already resists (see LCxx_T3 / Apex base_res entries).
BUDDY: dict[str, str] = {
    "am": "thuy", "hoa": "moc", "kim": "tho", "loi": "phong", "moc": "thuy",
    "phong": "moc", "quang": "kim", "tho": "hoa", "thuy": "kim",
}

# Stat anchors per realm. Existing R2/R4/R6/R8 baselines preserved as-is in
# the JSON; this curve fills R1/R3/R5/R7/R9/R10 only.
STATS: dict[int, dict] = {
    1:  {"rank": "tinh_anh",  "hp": 1100,  "atk": 38,   "matk": 65,   "def": 47,   "spd": 11, "hp_scale": 1.5,  "fdb": 0.03, "own": 0.20, "off": 0.0,  "cap": 0.80, "luck": 0.0, "crit": 0,   "crit_dmg": 0,   "mp_regen": 0.07},
    3:  {"rank": "tinh_anh",  "hp": 2900,  "atk": 92,   "matk": 165,  "def": 105,  "spd": 12, "hp_scale": 1.65, "fdb": 0.06, "own": 0.28, "off": 0.0,  "cap": 0.85, "luck": 0.2, "crit": 0,   "crit_dmg": 0,   "mp_regen": 0.07},
    5:  {"rank": "cuong_gia", "hp": 7800,  "atk": 200,  "matk": 360,  "def": 190,  "spd": 14, "hp_scale": 1.8,  "fdb": 0.10, "own": 0.40, "off": 0.05, "cap": 0.88, "luck": 0.8, "crit": 24,  "crit_dmg": 36,  "mp_regen": 0.07},
    7:  {"rank": "hung_manh", "hp": 21000, "atk": 410,  "matk": 730,  "def": 380,  "spd": 16, "hp_scale": 1.95, "fdb": 0.18, "own": 0.55, "off": 0.13, "cap": 0.90, "luck": 1.5, "crit": 72,  "crit_dmg": 96,  "mp_regen": 0.07},
    9:  {"rank": "dai_nang",  "hp": 56000, "atk": 850,  "matk": 1480, "def": 720,  "spd": 17, "hp_scale": 2.05, "fdb": 0.40, "own": 0.70, "off": 0.25, "cap": 0.92, "luck": 2.6, "crit": 192, "crit_dmg": 220, "mp_regen": 0.08},
    10: {"rank": "chi_ton",   "hp": 92000, "atk": 1200, "matk": 2050, "def": 1000, "spd": 18, "hp_scale": 2.2,  "fdb": 0.55, "own": 0.75, "off": 0.30, "cap": 0.92, "luck": 3.5, "crit": 260, "crit_dmg": 300, "mp_regen": 0.10},
}

# Per-element themed names for the 6 new tiers. Tier rank prefix maps:
#   R1  → T0  (lower than existing T1)
#   R3  → T1b (between T1 and T2)
#   R5  → T2b (between T2 and T3)
#   R7  → T3b (between T3 and Apex)
#   R9  → Apex2 (above existing Apex)
#   R10 → Sovereign / End (chi_ton tier)
NAMES: dict[str, dict[int, tuple[str, str]]] = {
    "kim": {
        1:  ("Kim Sa Tiểu Tốt",        "Gold Sand Footsoldier"),
        3:  ("Bạch Kim Đao Vệ",        "Platinum Blade Guard"),
        5:  ("Hắc Kim Cự Phách",       "Black Metal Greatsword"),
        7:  ("Thiên Kim Chiến Vương",  "Celestial Gold War King"),
        9:  ("Vạn Kim Tinh Quân",      "Myriad Metal Star Lord"),
        10: ("Thái Cổ Kim Hoàng",      "Primordial Metal Sovereign"),
    },
    "moc": {
        1:  ("Mộc Tiểu Yêu",           "Wood Sprite Cub"),
        3:  ("Lục Diệp Hộ Linh",       "Verdant Leaf Guardian"),
        5:  ("Ngàn Năm Đằng Quái",     "Millennial Vine Beast"),
        7:  ("Cổ Mộc Tiên Vương",      "Ancient Wood Immortal King"),
        9:  ("Vạn Diệp Sinh Quân",     "Ten-Thousand-Leaf Lord"),
        10: ("Thái Cổ Mộc Tổ",         "Primordial Wood Ancestor"),
    },
    "thuy": {
        1:  ("Thủy Tinh Tiểu Quái",    "Water Crystal Imp"),
        3:  ("Hàn Băng Lăng Vệ",       "Frost Ice Sentinel"),
        5:  ("Long Vĩ Triều Linh",     "Dragontail Tide Spirit"),
        7:  ("Thâm Uyên Hải Tướng",    "Abyssal Sea Commander"),
        9:  ("Bắc Minh Thần Quân",     "Northern Abyss Sovereign"),
        10: ("Thái Sơ Hải Đế",         "Primordial Ocean Emperor"),
    },
    "hoa": {
        1:  ("Tiểu Diễm Hỏa Tinh",     "Lesser Flame Sprite"),
        3:  ("Lưu Ly Hỏa Khôi",        "Glassflame Hellion"),
        5:  ("Diễm Hổ Tinh Quân",      "Blaze Tiger Star Lord"),
        7:  ("Phượng Hoàng Liệt Tướng","Phoenix Inferno General"),
        9:  ("Cửu Dương Hỏa Đế",       "Nine-Suns Fire Emperor"),
        10: ("Hồng Hoang Hỏa Tổ",      "Primordial Flame Ancestor"),
    },
    "tho": {
        1:  ("Đất Linh Tiểu Quái",     "Earth Spirit Imp"),
        3:  ("Thạch Giáp Vệ Sĩ",       "Stone Carapace Guard"),
        5:  ("Hậu Thổ Cự Linh",        "Primal Earth Colossus"),
        7:  ("Kim Cương Thổ Tướng",    "Diamond Earth General"),
        9:  ("Hậu Thổ Thần Quân",      "Earth God Sovereign"),
        10: ("Thái Cổ Địa Hoàng",      "Primordial Earth Sovereign"),
    },
    "phong": {
        1:  ("Phong Tiểu Linh",        "Wind Sprite"),
        3:  ("Lưu Phong Sứ Giả",       "Streaming Wind Envoy"),
        5:  ("Cuồng Phong Linh Hồ",    "Hurricane Spirit Fox"),
        7:  ("Vạn Lý Phong Vũ Tướng",  "Ten-Thousand-Mile Wind General"),
        9:  ("Cửu Tiêu Phong Quân",    "Nine-Sky Wind Lord"),
        10: ("Thái Sơ Phong Hoàng",    "Primordial Wind Sovereign"),
    },
    "loi": {
        1:  ("Lôi Tinh Tiểu Quái",     "Thunder Spark Imp"),
        3:  ("Tử Điện Linh Vũ",        "Violet Spark Spirit Bird"),
        5:  ("Cuồng Lôi Cự Tượng",     "Furious Thunder Greatbeast"),
        7:  ("Tử Lôi Long Vương",      "Violet Lightning Dragon King"),
        9:  ("Cửu Trùng Lôi Tướng",    "Nine-Layer Thunder General"),
        10: ("Thái Cổ Lôi Đế",         "Primordial Thunder Emperor"),
    },
    "quang": {
        1:  ("Quang Tiểu Tinh",        "Light Sprite"),
        3:  ("Tịnh Quang Hộ Vệ",       "Pure Light Guardian"),
        5:  ("Đại Nhật Quang Linh",    "Great Sun Light Spirit"),
        7:  ("Phật Quang Bồ Tát",      "Buddha-Light Bodhisattva"),
        9:  ("Đại La Tiên Tướng",      "Da Luo Immortal General"),
        10: ("Thái Sơ Quang Đế",       "Primordial Light Sovereign"),
    },
    "am": {
        1:  ("Âm Hồn Tiểu Quái",       "Lesser Shadow Imp"),
        3:  ("U Linh Quỷ Vệ",          "Wraith Demon Guard"),
        5:  ("Hắc Vụ Ma Linh",         "Dark Mist Demon Spirit"),
        7:  ("U Minh Tử Tướng",        "Underworld Death General"),
        9:  ("Cửu U Diệt Hồn Quân",    "Nine-Hells Soul-Eater Lord"),
        10: ("Hỗn Độn Âm Đế",          "Primordial Dark Sovereign"),
    },
}

KEY_TIER: dict[int, str] = {
    1: "T0", 3: "T1b", 5: "T2b", 7: "T3b", 9: "Apex2", 10: "Sovereign",
}


def _slug(vi: str) -> str:
    """ASCII slug for the JSON key — strip diacritics + spaces."""
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", vi)
    cleaned = "".join(c for c in nfkd if not unicodedata.combining(c))
    cleaned = cleaned.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in cleaned.title() if c.isalnum())


def _build_skill_keys(elem: str, realm: int) -> list[str]:
    """Skill loadout for the new tier — only references skills known to exist."""
    elem_t = elem.capitalize()
    base = f"EnemyLC{elem_t}"
    if realm <= 2:
        return [f"{base}_T1", f"Enemy{elem_t}_T1"]
    if realm <= 4:
        return [f"{base}_T1", f"{base}_T2"]
    if realm <= 6:
        return [f"{base}_T2", f"{base}_T3"]
    if realm <= 8:
        return [f"{base}_T3", f"{base}_Apex", f"{base}_T2"]
    return [f"{base}_Apex", f"{base}_T3", f"{base}_T2"]


def _build_entry(elem: str, realm: int) -> dict:
    s = STATS[realm]
    vi, en = NAMES[elem][realm]
    key = f"LC{elem.capitalize()}_{KEY_TIER[realm]}_{_slug(vi)}"
    base_res: dict[str, float] = {elem: s["own"]}
    if s["off"] > 0:
        base_res[BUDDY[elem]] = s["off"]

    entry: dict = {
        "key": key,
        "vi": vi,
        "en": en,
        "rank": s["rank"],
        "realm_level": realm,
        "element": elem,
        "base_hp": s["hp"],
        "base_spd": s["spd"],
        "hp_scale": s["hp_scale"],
        "skill_keys": _build_skill_keys(elem, realm),
        "base_atk": s["atk"],
        "base_matk": s["matk"],
        "base_def": s["def"],
        "mp_regen_pct": s["mp_regen"],
        "loot_table_key": f"LinhCanLoot_{elem.capitalize()}",
        "final_dmg_bonus": s["fdb"],
        "base_res": base_res,
        "res_cap_pct": s["cap"],
        "loot_luck_bonus": s["luck"],
    }
    if s["crit"]:
        entry["base_crit_rating"] = s["crit"]
    if s["crit_dmg"]:
        entry["base_crit_dmg_rating"] = s["crit_dmg"]
    if realm == 10:
        entry["immune_hard_cc"] = True
    return entry


def main() -> None:
    total_added = 0
    for elem in BUDDY:
        path = ENEMY_DIR / f"{elem}.json"
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing_realms = {e.get("realm_level") for e in existing}

        added = 0
        for realm in NEW_REALMS:
            if realm in existing_realms:
                continue
            existing.append(_build_entry(elem, realm))
            added += 1

        existing.sort(key=lambda e: e.get("realm_level", 0))
        path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        total_added += added
        print(f"  {path.name}: +{added} entries (now {len(existing)} total)")
    print(f"Done. {total_added} entries added.")


if __name__ == "__main__":
    main()
