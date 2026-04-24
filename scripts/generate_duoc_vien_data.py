"""Generate Dược Viên enemy files, loot table, and dungeon entries.

Run from the repo root after ``convert_alchemy_xlsx.py`` has produced
``herbs.json`` and ``yeu_thu.json`` — this script uses the herbs list to
build realm-appropriate drop tables.

Writes:
    src/data/enemies/duoc_vien/r01.json  ...  r09.json
    src/data/loot_tables/duoc_vien.json             (contains DuocVienLoot_1..9)
    appends 9 DungeonDuocVien_R1..R9 entries to src/data/dungeons.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
HERBS_PATH = REPO_ROOT / "src" / "data" / "items" / "herbs.json"
YEUTHU_PATH = REPO_ROOT / "src" / "data" / "items" / "yeu_thu.json"
ENEMIES_DIR = REPO_ROOT / "src" / "data" / "enemies" / "duoc_vien"
LOOT_PATH = REPO_ROOT / "src" / "data" / "loot_tables" / "duoc_vien.json"
DUNGEONS_PATH = REPO_ROOT / "src" / "data" / "dungeons.json"


# Enemy archetypes per realm — (vi, en, rank, skill_key, hp, spd, hp_scale).
# Sized/named to feel like a sprawling medicinal garden guarded by plant-spirits.
ENEMY_TEMPLATES: dict[int, list[tuple]] = {
    1: [
        ("Lục Diệp Linh Thảo", "Green Leaf Spirit Herb", "pho_thong", "EnemyMoc_T1", 220, 7, 1.5),
        ("Dược Viên Đằng Xà", "Herbal Garden Vine Snake", "pho_thong", "EnemyMoc_T1", 200, 9, 1.5),
        ("Tinh Căn Tiểu Yêu", "Spirit Root Imp",          "pho_thong", "EnemyMoc_T1", 180, 10, 1.5),
        ("Dược Lão Linh Thú", "Elder Herb Beast",         "cuong_gia", "EnemyMoc_T1", 320, 8,  1.5),
    ],
    2: [
        ("Linh Mộc Thủ Vệ",   "Spirit Wood Guardian",     "pho_thong", "EnemyMoc_R2", 300, 10, 1.5),
        ("Bích Lam Hoa Yêu",  "Azure Blue Flower Yao",    "pho_thong", "EnemyMoc_R2", 260, 12, 1.5),
        ("Cổ Đằng Triền Ma",  "Ancient Vine Binding Demon","cuong_gia","EnemyMoc_R2", 420, 9,  1.5),
    ],
    3: [
        ("Mộc Linh Tuần Canh","Wood Spirit Patroller",    "cuong_gia", "EnemyMoc_T2", 560, 11, 1.5),
        ("Hương Dược Yêu Cơ", "Fragrant Herb Enchantress","cuong_gia", "EnemyMoc_T2", 500, 13, 1.5),
        ("Thiết Diệp Ma Căn", "Iron Leaf Demon Root",     "dai_nang",  "EnemyMoc_T2", 820, 9,  1.5),
    ],
    4: [
        ("Hắc Linh Mộc Yêu",  "Black Spirit Wood Yao",    "cuong_gia", "EnemyMoc_R4", 880,  10, 1.5),
        ("Lục Ảnh Dược Tinh", "Green Shadow Pill Spirit", "dai_nang",  "EnemyMoc_R4", 1100, 11, 1.5),
        ("Cự Mộc Ma Tướng",   "Giant Wood Demon General", "dai_nang",  "EnemyMoc_R4", 1280, 9,  1.5),
    ],
    5: [
        ("Dược Viên Vương Giả","Medicine Garden Sovereign","dai_nang", "EnemyMoc_T3", 1500, 10, 1.5),
        ("Cửu Diệp Mộc Ma",    "Nine-Leaf Wood Demon",    "dai_nang",  "EnemyMoc_T3", 1650, 11, 1.5),
        ("Thiên Linh Đằng Thần","Heavenly Spirit Vine God","chi_ton",  "EnemyMoc_T3", 2100, 9,  1.5),
    ],
    6: [
        ("Thiên Mộc Đại Yêu",  "Heaven Wood Great Demon", "dai_nang",  "EnemyMoc_R6", 2400, 11, 1.5),
        ("Nguyên Linh Dược Thần","Primal Spirit Herb God","chi_ton",   "EnemyMoc_R6", 2900, 10, 1.5),
        ("Thánh Thảo Cổ Thần", "Saint Herb Ancient God",  "chi_ton",   "EnemyMoc_R6", 3300, 9,  1.5),
    ],
    7: [
        ("Bách Thảo Ma Vương", "Hundred Herb Demon King", "chi_ton",   "EnemyMoc_T3", 4000, 10, 1.5),
        ("Dược Viên Chủ Thần", "Medicine Garden Master God","chi_ton", "EnemyMoc_T3", 4600, 11, 1.5),
        ("Linh Mộc Thiên Đế",  "Spirit Wood Heavenly Emperor","chi_ton","SkillDefMoc",5200, 9,  1.5),
    ],
    8: [
        ("Thái Cổ Mộc Thần",   "Primordial Wood God",     "chi_ton",   "EnemyMoc_R8", 6200, 10, 1.5),
        ("Thiên Căn Linh Tổ",  "Heaven Root Spirit Ancestor","chi_ton","EnemyMoc_R8", 7000, 11, 1.5),
        ("Vạn Dược Chi Vương", "King of Ten Thousand Herbs","chi_ton", "EnemyMoc_R8", 7800, 9,  1.5),
    ],
    9: [
        ("Vô Thượng Mộc Thần", "Supreme Wood God",        "chi_ton",   "EnemyMoc_R9", 9500,  11, 1.5),
        ("Hỗn Nguyên Dược Tôn","Chaos Primal Herb Lord",  "chi_ton",   "EnemyMoc_R9", 10800, 10, 1.5),
        ("Thiên Đạo Mộc Đế",   "Heavenly Dao Wood Emperor","chi_ton",  "EnemyMoc_R9", 12500, 9,  1.5),
    ],
}

# HP regen aura ramps with realm — the signature Dược Viên mechanic.
HP_REGEN_BY_REALM = {1: 0.06, 2: 0.07, 3: 0.07, 4: 0.08, 5: 0.09, 6: 0.10, 7: 0.11, 8: 0.12, 9: 0.12}


def _load_items_by_grade(path: Path, type_field: str) -> dict[int, list[str]]:
    items = json.loads(path.read_text(encoding="utf-8"))
    by_grade: dict[int, list[str]] = defaultdict(list)
    for item in items:
        if item.get("type") == type_field:
            by_grade[int(item.get("grade", 1))].append(item["key"])
    return by_grade


def _realm_herb_grades(realm: int) -> tuple[int, int]:
    """Lower/upper herb grade that should drop at this realm (1..9)."""
    if realm <= 2:
        return (1, 2)
    if realm <= 4:
        return (2, 3)
    if realm <= 6:
        return (3, 4)
    if realm <= 8:
        return (4, 5)
    return (5, 6)


def _realm_yeu_thu_grades(realm: int) -> tuple[int, int]:
    if realm <= 3:
        return (1, 1)
    if realm <= 6:
        return (1, 2)
    return (2, 3)


def generate_enemies() -> None:
    for realm, templates in ENEMY_TEMPLATES.items():
        regen = HP_REGEN_BY_REALM[realm]
        enemies = []
        for idx, (vi, en, rank, skill, hp, spd, hp_scale) in enumerate(templates, start=1):
            key_base = f"DuocVienR{realm}"
            key = f"{key_base}_{idx:02d}"
            enemies.append({
                "key": key,
                "vi": vi,
                "en": en,
                "rank": rank,
                "realm_level": realm,
                "element": "moc",
                "base_hp": hp,
                "base_spd": spd,
                "hp_scale": hp_scale,
                "hp_regen_pct": regen,
                "skill_keys": [skill],
                "loot_table_key": f"DuocVienLoot_{realm}",
            })
        ENEMIES_DIR.mkdir(parents=True, exist_ok=True)
        out = ENEMIES_DIR / f"r{realm:02d}.json"
        out.write_text(json.dumps(enemies, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  enemies: {out.relative_to(REPO_ROOT)} ({len(enemies)} entries)")


def generate_loot() -> None:
    herbs_by_grade = _load_items_by_grade(HERBS_PATH, "herb")
    yeu_thu_by_grade = _load_items_by_grade(YEUTHU_PATH, "yeu_thu")

    loot: dict[str, list[dict]] = {}
    for realm in range(1, 10):
        entries: list[dict] = []
        # Herb drops — primary loot, split across grade bands.
        lo, hi = _realm_herb_grades(realm)
        for grade in range(lo, hi + 1):
            keys = herbs_by_grade.get(grade, [])
            weight_each = max(2, 60 // max(1, len(keys)))
            for key in keys:
                entries.append({
                    "item_key": key,
                    "weight": weight_each,
                    "qty_min": 1,
                    "qty_max": 2 if grade <= lo else 1,
                })
        # Beast materials — secondary but thematic (herb gardens sometimes
        # drop essence from minor wood beasts that inhabit them).
        yt_lo, yt_hi = _realm_yeu_thu_grades(realm)
        for grade in range(yt_lo, yt_hi + 1):
            for key in yeu_thu_by_grade.get(grade, []):
                entries.append({
                    "item_key": key,
                    "weight": 4,
                    "qty_min": 1,
                    "qty_max": 1,
                    "pool_id": f"duoc_vien_r{realm}_yeuthu",
                })
        # Minor chance of healing pills at low realms for consistency with
        # normal zone tables.
        if realm <= 3:
            entries.append({"item_key": "DanHoiHPSmall", "weight": 15, "qty_min": 1, "qty_max": 2})
            entries.append({"item_key": "DanHoiMPSmall", "weight": 15, "qty_min": 1, "qty_max": 2})

        loot[f"DuocVienLoot_{realm}"] = entries

    LOOT_PATH.write_text(json.dumps(loot, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(len(v) for v in loot.values())
    print(f"  loot:    {LOOT_PATH.relative_to(REPO_ROOT)} ({len(loot)} zones, {total} entries)")


def patch_dungeons() -> None:
    data = json.loads(DUNGEONS_PATH.read_text(encoding="utf-8"))

    # Annotate existing entries (idempotent) — gives the JSON explicit type info.
    for entry in data:
        entry.setdefault("dungeon_type", "normal")

    # Remove any previously generated Dược Viên entries so we can regenerate.
    data = [d for d in data if not d["key"].startswith("DungeonDuocVien_")]

    base_merit = [300, 600, 1500, 3500, 8000, 18000, 40000, 90000, 220000]
    base_stone = [0, 0, 30, 100, 220, 500, 1200, 3500, 12000]
    for realm in range(1, 10):
        enemy_keys = [f"DuocVienR{realm}_{i:02d}" for i in range(1, len(ENEMY_TEMPLATES[realm]) + 1)]
        data.append({
            "key": f"DungeonDuocVien_R{realm}",
            "vi": f"Dược Viên Cấp {realm}",
            "en": f"Medicine Garden Tier {realm}",
            "description": (
                "Vườn dược linh rộng lớn, dược khí nồng đậm thấm khắp cây cỏ."
                " Các linh thú hệ Mộc tại đây hấp thụ tinh hoa thảo dược nên"
                " hồi phục huyết mạch cực nhanh trong chiến đấu."
            ),
            "dungeon_type": "duoc_vien",
            "required_qi_realm": realm - 1,
            "wave_count": 3,
            "enemy_pool": enemy_keys,
            "merit_reward": base_merit[realm - 1],
            "stone_reward": base_stone[realm - 1],
            "cooldown_hours": 4 + (realm // 3) * 2,
        })

    DUNGEONS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    duoc_count = sum(1 for d in data if d["key"].startswith("DungeonDuocVien_"))
    print(f"  dungeons:{DUNGEONS_PATH.relative_to(REPO_ROOT)} ({len(data)} total, {duoc_count} Dược Viên)")


def main() -> int:
    if not HERBS_PATH.exists() or not YEUTHU_PATH.exists():
        print("ERROR: run scripts/convert_alchemy_xlsx.py first", file=sys.stderr)
        return 1
    generate_enemies()
    generate_loot()
    patch_dungeons()
    return 0


if __name__ == "__main__":
    sys.exit(main())
