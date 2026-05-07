"""Generate new enemy skills for every (realm × element × grade) combo
present among dungeon enemies, then wire each dungeon enemy to use the
matching skill.

Scope
-----
Targets only *dungeon* enemies, i.e. JSON files under:
    src/data/enemies/normal/
    src/data/enemies/duoc_vien/
    src/data/enemies/linh_can/

`the_chat` bosses are skipped — they use a curated apex skill list.

Output
------
1. ``src/data/skills/enemy/dungeon_grades.json`` — the new skill bank.
2. Each dungeon enemy file is rewritten in place with the new
   ``EnemyDungeon_<Elem>_<Rank>_R<R>`` key appended to ``skill_keys``
   (deduplicated, idempotent on re-runs).

Run from repo root::

    python scripts/add_dungeon_enemy_skills.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# ── Paths ────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
ENEMY_DIRS = (
    ROOT / "src/data/enemies/normal",
    ROOT / "src/data/enemies/duoc_vien",
    ROOT / "src/data/enemies/linh_can",
)
OUT_SKILLS_FILE = ROOT / "src/data/skills/enemy/dungeon_grades.json"

# ── Domain constants ─────────────────────────────────────────────────
ELEMENTS = ["kim", "moc", "thuy", "hoa", "tho", "phong", "loi", "quang", "am"]

# Rank order (weak → strong). Multipliers applied to base_dmg only;
# mp/cooldown stay realm-driven so the tempo curve is consistent.
RANK_ORDER: tuple[str, ...] = (
    "pho_thong",
    "tinh_anh",
    "cuong_gia",
    "hung_manh",
    "dai_nang",
    "than_thu",
    "tien_thu",
    "chi_ton",
)
RANK_DMG_MULT: dict[str, float] = {
    "pho_thong": 0.45,
    "tinh_anh":  0.60,
    "cuong_gia": 0.75,
    "hung_manh": 0.90,
    "dai_nang":  1.05,
    "than_thu":  1.15,
    "tien_thu":  1.25,
    "chi_ton":   1.40,
}
# Number of debuff effects rolled by skill, per rank tier.
RANK_EFFECT_COUNT: dict[str, int] = {
    "pho_thong": 1, "tinh_anh": 1,
    "cuong_gia": 2, "hung_manh": 2,
    "dai_nang":  2, "than_thu": 3,
    "tien_thu":  3, "chi_ton":  3,
}
# Base proc chance for the *primary* debuff at each rank (later debuffs
# decay by 0.10 per slot).
RANK_PROC_BASE: dict[str, float] = {
    "pho_thong": 0.20, "tinh_anh": 0.30,
    "cuong_gia": 0.40, "hung_manh": 0.50,
    "dai_nang":  0.60, "than_thu": 0.65,
    "tien_thu":  0.75, "chi_ton":  0.85,
}

# Per-realm tuning (1..10 — dungeon enemies span the full range).
REALM_BASE_DMG: dict[int, int] = {
    1: 10, 2: 18, 3: 30, 4: 50, 5: 90,
    6: 145, 7: 220, 8: 320, 9: 470, 10: 700,
}
REALM_MP: dict[int, int] = {
    1: 10, 2: 18, 3: 28, 4: 42, 5: 58,
    6: 75, 7: 95, 8: 118, 9: 142, 10: 170,
}
REALM_CD: dict[int, int] = {
    1: 1, 2: 2, 3: 2, 4: 2, 5: 3,
    6: 3, 7: 4, 8: 4, 9: 5, 10: 5,
}
# atk/matk slope applied to the dominant stat (rank-independent — rank
# only changes the additive base_dmg). Roughly tracks the curated
# realm_NN.json skills.
REALM_DMG_SCALE: dict[int, float] = {
    1: 0.30, 2: 0.45, 3: 0.60, 4: 0.80, 5: 1.00,
    6: 1.10, 7: 1.20, 8: 1.25, 9: 1.30, 10: 1.40,
}

# Element archetype — physical or magical, plus debuff list ordered
# from most-thematic (slot 0) to least.
ARCHETYPE: dict[str, tuple[str, tuple[str, ...]]] = {
    "kim":   ("physical", ("DebuffPhaGiap", "DebuffChayMau", "DebuffXeRach")),
    "moc":   ("magical",  ("DebuffDocTo", "DebuffBaoMon", "DebuffCatDut")),
    "thuy":  ("magical",  ("DebuffLamCham", "DebuffDongBang", "DebuffChayMau")),
    "hoa":   ("magical",  ("DebuffThieuDot", "DebuffDotChay", "DebuffBaoMon")),
    "tho":   ("physical", ("DebuffTroBuoc", "DebuffLunDat", "CCStun")),
    "phong": ("physical", ("DebuffChayMau", "DebuffCuonBay", "CCInterrupt")),
    "loi":   ("magical",  ("DebuffTeLiet", "DebuffSetDanh", "CCStun")),
    "quang": ("magical",  ("DebuffPhaGiap", "DebuffSetDanh", "DebuffLoaMat")),
    "am":    ("magical",  ("DebuffLoaMat", "DebuffXeRach", "DebuffCatDut")),
}

# Display strings for skill names.
ELEM_VI: dict[str, str] = {
    "kim": "Kim", "moc": "Mộc", "thuy": "Thủy", "hoa": "Hỏa", "tho": "Thổ",
    "phong": "Phong", "loi": "Lôi", "quang": "Quang", "am": "Ám",
}
ELEM_EN: dict[str, str] = {
    "kim": "Metal", "moc": "Wood", "thuy": "Water", "hoa": "Fire", "tho": "Earth",
    "phong": "Wind", "loi": "Thunder", "quang": "Light", "am": "Shadow",
}
RANK_VI: dict[str, str] = {
    "pho_thong": "Phổ Thông",
    "tinh_anh":  "Tinh Anh",
    "cuong_gia": "Cường Giả",
    "hung_manh": "Hùng Mãnh",
    "dai_nang":  "Đại Năng",
    "than_thu":  "Thần Thú",
    "tien_thu":  "Tiên Thú",
    "chi_ton":   "Chí Tôn",
}
RANK_EN: dict[str, str] = {
    "pho_thong": "Common",
    "tinh_anh":  "Elite",
    "cuong_gia": "Strong",
    "hung_manh": "Fierce",
    "dai_nang":  "Mighty",
    "than_thu":  "Divine Beast",
    "tien_thu":  "Immortal Beast",
    "chi_ton":   "Sovereign",
}
# Compact rank token for the skill key (PascalCase, no underscores).
RANK_KEY_TOKEN: dict[str, str] = {
    "pho_thong": "PhoThong",
    "tinh_anh":  "TinhAnh",
    "cuong_gia": "CuongGia",
    "hung_manh": "HungManh",
    "dai_nang":  "DaiNang",
    "than_thu":  "ThanThu",
    "tien_thu":  "TienThu",
    "chi_ton":   "ChiTon",
}


def skill_key(elem: str, rank: str, realm: int) -> str:
    return f"EnemyDungeon_{elem.capitalize()}_{RANK_KEY_TOKEN[rank]}_R{realm}"


def build_skill(elem: str, rank: str, realm: int) -> dict:
    atk_type, debuff_pool = ARCHETYPE[elem]
    is_phys = atk_type == "physical"

    base_dmg = int(round(REALM_BASE_DMG[realm] * RANK_DMG_MULT[rank]))
    dmg_slope = REALM_DMG_SCALE[realm]
    # High-rank, late-realm skills get a sliver of off-stat scaling
    # (mirrors the EmperorR9 pattern in realm_09.json).
    off_slope = 0.0
    if realm >= 8 and rank in ("dai_nang", "than_thu", "tien_thu", "chi_ton"):
        off_slope = round(dmg_slope * 0.55, 2)

    n_effects = min(RANK_EFFECT_COUNT[rank], len(debuff_pool))
    effects = list(debuff_pool[:n_effects])
    proc0 = RANK_PROC_BASE[rank]
    effect_chances = {
        eff: round(max(0.10, proc0 - 0.10 * idx), 2)
        for idx, eff in enumerate(effects)
    }

    return {
        "key": skill_key(elem, rank, realm),
        "vi": f"{RANK_VI[rank]} {ELEM_VI[elem]} Bí Pháp",
        "en": f"{RANK_EN[rank]} {ELEM_EN[elem]} Secret Art",
        "realm": realm,
        "category": "attack",
        "element": elem,
        "attack_type": atk_type,
        "dmg_scale": {
            "atk":  dmg_slope if is_phys else off_slope,
            "matk": off_slope if is_phys else dmg_slope,
        },
        "mp_cost": REALM_MP[realm],
        "cooldown": REALM_CD[realm],
        "base_dmg": base_dmg,
        "effects": effects,
        "effect_chances": effect_chances,
        "formation_key": None,
    }


def collect_enemy_combos() -> tuple[
    list[Path],
    set[tuple[str, str, int]],
]:
    """Walk dungeon enemy dirs and return (file paths, set of combos)."""
    files: list[Path] = []
    combos: set[tuple[str, str, int]] = set()
    for d in ENEMY_DIRS:
        if not d.exists():
            continue
        for path in sorted(d.rglob("*.json")):
            files.append(path)
            for entry in json.loads(path.read_text(encoding="utf-8")):
                elem = entry.get("element")
                rank = entry.get("rank")
                realm = entry.get("realm_level")
                if not elem or rank not in RANK_ORDER or realm not in REALM_BASE_DMG:
                    continue
                combos.add((elem, rank, int(realm)))
    return files, combos


def write_skills(combos: set[tuple[str, str, int]]) -> int:
    """Write the dungeon skill bank. Preserves any pre-existing entries
    in the same file so a partial hand-edit isn't blown away.
    """
    existing: list[dict] = []
    if OUT_SKILLS_FILE.exists():
        existing = json.loads(OUT_SKILLS_FILE.read_text(encoding="utf-8"))
    by_key: dict[str, dict] = {s["key"]: s for s in existing}

    added = 0
    for elem, rank, realm in sorted(combos):
        key = skill_key(elem, rank, realm)
        skill = build_skill(elem, rank, realm)
        if key not in by_key:
            added += 1
        by_key[key] = skill  # always refresh — re-runnable

    out = sorted(by_key.values(), key=lambda s: (s["realm"], s["element"], s["key"]))
    OUT_SKILLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_SKILLS_FILE.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return added


def wire_enemies(files: list[Path]) -> int:
    """Append the matching skill key to every dungeon enemy. Returns
    number of enemies whose ``skill_keys`` list was actually changed.
    """
    changed = 0
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        dirty = False
        for entry in data:
            elem = entry.get("element")
            rank = entry.get("rank")
            realm = entry.get("realm_level")
            if not elem or rank not in RANK_ORDER or realm not in REALM_BASE_DMG:
                continue
            key = skill_key(elem, rank, int(realm))
            keys = list(entry.get("skill_keys", []))
            if key not in keys:
                keys.append(key)
                entry["skill_keys"] = keys
                dirty = True
                changed += 1
        if dirty:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    return changed


def main() -> None:
    files, combos = collect_enemy_combos()
    print(f"Scanned {len(files)} dungeon enemy file(s); {len(combos)} unique (element, rank, realm) cells.")

    by_realm: dict[int, int] = defaultdict(int)
    for _e, _r, rl in combos:
        by_realm[rl] += 1
    for rl in sorted(by_realm):
        print(f"  R{rl:>2}: {by_realm[rl]} cell(s)")

    new_skills = write_skills(combos)
    print(f"Wrote {OUT_SKILLS_FILE.relative_to(ROOT)} ({new_skills} new entries).")

    enemies_changed = wire_enemies(files)
    print(f"Wired {enemies_changed} enemy skill_keys list(s).")


if __name__ == "__main__":
    main()
