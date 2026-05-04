"""Generate split constitution JSONs from the existing combined file + 135 alternates.

Splits ``src/data/constitutions.json`` into per-element files under
``src/data/constitutions/`` and adds 3 stat-slant alternates for every
(element × rarity) cell. Re-running is idempotent: alternates use the
``_alt<N>`` key suffix so existing entries are never overwritten.

Run from project root:  python scripts/gen_constitutions.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "src" / "data" / "constitutions"
# Legacy single-file source — read once when migrating, then never again.
LEGACY_SRC = ROOT / "src" / "data" / "constitutions.json"

ELEMENTS = ("kim", "moc", "thuy", "hoa", "tho", "loi", "phong", "quang", "am")
RARITIES = ("common", "uncommon", "rare", "epic", "legendary")

# Roll weight + cost per rarity (mirrors existing entries' values).
TIER: dict[str, dict] = {
    "common":    {"weight": 30, "cost": 0,     "scale": 1.0},
    "uncommon":  {"weight": 12, "cost": 7000,  "scale": 1.6},
    "rare":      {"weight": 5,  "cost": 20000, "scale": 2.4},
    "epic":      {"weight": 1,  "cost": 55000, "scale": 3.5},
    "legendary": {"weight": 0,  "cost": 80000, "scale": 5.0},
}

# Per-element archetypes (3 stat slants per element, on top of the existing
# canonical line). Each archetype names the slant and lists the 5 base stats
# that scale with rarity.
#
# (slant_key, vi_root, en_root, base_stats_at_common)
ELEM_ARCHETYPES: dict[str, list[tuple[str, str, str, dict]]] = {
    "kim": [
        ("Sat",   "Sát Kim",      "Slaying Gold",        {"crit_rating": 80, "bleed_on_hit_pct": 0.05, "armor_pen_pct": 0.02}),
        ("Cuong", "Cương Kim",    "Hardened Gold",       {"hp_pct": 0.06, "res_all": 20, "thorn_pct": 0.04, "bleed_on_hit_pct": 0.04}),
        ("Tinh",  "Tinh Kim",     "Essence Gold",        {"crit_rating": 60, "crit_dmg_rating": 60, "true_dmg_pct": 0.02}),
    ],
    "moc": [
        ("Sinh",  "Sinh Mộc",     "Living Wood",         {"hp_pct": 0.1, "hp_regen_pct": 0.012, "heal_pct": 0.05}),
        ("Diep",  "Diệp Mộc",     "Leaf Wood",           {"mp_pct": 0.08, "hp_regen_pct": 0.008, "dot_leech_pct": 0.05}),
        ("Tung",  "Cổ Tùng",      "Ancient Pine",        {"hp_pct": 0.12, "res_all": 25, "hp_regen_pct": 0.008}),
    ],
    "thuy": [
        ("Hai",    "Bích Hải",    "Azure Sea",           {"mp_pct": 0.12, "cooldown_reduce": 0.05, "reflect_pct": 0.04}),
        # Hàn Băng: ICE/cold theme — slow_on_hit, NOT shock (shock is Lôi).
        # Legendary tier additionally gets freeze_on_skill + reflect — see
        # the manual buff in src/data/constitutions/thuy.json.
        ("Bang",   "Hàn Băng",    "Cold Frost",          {"mp_pct": 0.08, "slow_on_hit_pct": 0.05, "evasion_rating": 60}),
        ("Luu",    "Lưu Vân",     "Flowing Cloud",       {"mp_pct": 0.1, "mp_regen_pct": 0.015, "cooldown_reduce": 0.04}),
    ],
    "hoa": [
        ("Diem",  "Liệt Diệm",    "Blazing Flame",       {"final_dmg_bonus": 0.05, "burn_on_hit_pct": 0.1, "crit_rating": 50}),
        ("Phung", "Phượng Hỏa",   "Phoenix Flame",       {"final_dmg_bonus": 0.04, "burn_dmg_bonus": 0.08, "burn_on_hit_pct": 0.07}),
        ("Diep",  "Điệp Hỏa",     "Layered Flame",       {"burn_on_hit_pct": 0.12, "burn_stack_cap_bonus": 1, "fire_res_shred": 0.05}),
    ],
    "tho": [
        ("Trach", "Trạch Thổ",    "Marshland Earth",     {"hp_pct": 0.1, "shield_regen_pct": 0.025, "thorn_pct": 0.05}),
        ("Cuong", "Cương Sơn",    "Iron Mountain",       {"hp_pct": 0.08, "res_all": 30, "final_dmg_reduce": 0.04}),
        ("Hoang", "Hoàng Sa",     "Yellow Sand",         {"hp_pct": 0.06, "shield_regen_pct": 0.018, "evasion_rating": 50}),
    ],
    "loi": [
        ("Cuong", "Cuồng Lôi",    "Frenzy Thunder",      {"spd_bonus": 5, "crit_rating": 80, "shock_on_hit_pct": 0.1}),
        ("Tu",    "Tử Lôi",       "Purple Thunder",      {"spd_bonus": 3, "crit_dmg_rating": 80, "shock_dmg_bonus": 0.1}),
        ("Bach",  "Bạch Lôi",     "White Thunder",       {"spd_bonus": 6, "evasion_rating": 80, "shock_on_hit_pct": 0.07}),
    ],
    "phong": [
        ("Phieu", "Phiêu Phong",  "Drifting Wind",       {"evasion_rating": 100, "spd_bonus": 4, "mark_on_hit_pct": 0.06}),
        ("Tinh",  "Tinh Phong",   "Spirit Wind",         {"evasion_rating": 80, "cooldown_reduce": 0.05, "mp_regen_pct": 0.012}),
        ("Loc",   "Lốc Xoáy",     "Cyclone Wind",        {"evasion_rating": 70, "spd_bonus": 5, "shock_on_hit_pct": 0.06}),
    ],
    "quang": [
        ("Tinh",  "Tịnh Quang",   "Pure Light",          {"hp_pct": 0.07, "cleanse_on_turn_pct": 0.1, "heal_pct": 0.05}),
        ("Lien",  "Bạch Liên",    "White Lotus",         {"mp_pct": 0.08, "heal_pct": 0.07, "debuff_immune_pct": 0.05}),
        ("Kim",   "Kim Quang",    "Golden Light",        {"hp_pct": 0.06, "final_dmg_bonus": 0.04, "crit_res_rating": 60}),
    ],
    "am": [
        ("U",      "U Ám",         "Gloom Dark",         {"final_dmg_bonus": 0.05, "soul_drain_on_hit_pct": 0.1, "debuff_immune_pct": 0.06}),
        ("Suong",  "Hắc Sương",    "Black Mist",         {"final_dmg_bonus": 0.04, "evasion_rating": 60, "stat_steal_on_hit_pct": 0.05}),
        ("Tu",     "Tử Vong",      "Death Wraith",       {"final_dmg_bonus": 0.06, "soul_drain_on_hit_pct": 0.08, "true_dmg_pct": 0.02}),
    ],
}

RARITY_VI_PREFIX = {
    "common":    "",
    "uncommon":  "Hậu ",
    "rare":      "Linh ",
    "epic":      "Thần ",
    "legendary": "Đế ",
}
RARITY_EN_PREFIX = {
    "common":    "",
    "uncommon":  "Greater ",
    "rare":      "Spirit ",
    "epic":      "Divine ",
    "legendary": "Imperial ",
}

# Materials per (rarity, has_archetype_element). Keep the existing pattern
# of the canonical entries.
ELEM_MAT_KEY = {
    "kim": "ElemEssKim", "moc": "ElemEssMoc", "thuy": "ElemEssThuy",
    "hoa": "ElemEssHoa", "tho": "ElemEssTho", "loi": "ElemEssLoi",
    "phong": "ElemEssPhong", "quang": "ElemEssQuang", "am": "ElemEssAm",
}

DESC_TEMPLATE = {
    "common":    "Linh khí {elem_vi} sơ khởi tụ trong thân, mọi thuộc tính {tag} đều được khơi dậy.",
    "uncommon":  "Linh khí {elem_vi} đã hậu nhập huyết mạch — sức mạnh {tag} vượt trội hơn thường nhân.",
    "rare":      "Thân hóa linh thể {elem_vi}, {tag} mạnh mẽ áp đảo đối thủ cùng cảnh giới.",
    "epic":      "Thần thể {elem_vi} cộng hưởng với đại đạo, {tag} bùng nổ đến cực hạn.",
    "legendary": "Đế thể {elem_vi} — đỉnh cao của lộ trình {tag}, vạn cổ truyền tụng.",
}

ELEM_VI = {
    "kim": "Kim", "moc": "Mộc", "thuy": "Thủy", "hoa": "Hỏa", "tho": "Thổ",
    "loi": "Lôi", "phong": "Phong", "quang": "Quang", "am": "Âm",
}
SLANT_TAG_VI = {
    # Maps the keys we use in `base_stats` to a short Vietnamese tag for the
    # description sentence.
    "crit_rating":          "bạo kích",
    "crit_dmg_rating":      "sát thương bạo kích",
    "bleed_on_hit_pct":     "chảy máu",
    "armor_pen_pct":        "xuyên giáp",
    "true_dmg_pct":         "sát thương chân thực",
    "hp_pct":               "sinh lực",
    "hp_regen_pct":         "hồi phục",
    "heal_pct":             "trị liệu",
    "dot_leech_pct":        "hút sinh",
    "res_all":              "phòng ngự",
    "thorn_pct":            "phản đòn",
    "mp_pct":               "linh lực",
    "mp_regen_pct":         "hồi linh",
    "cooldown_reduce":      "hồi chiêu",
    "reflect_pct":          "phản kích",
    "shock_on_hit_pct":     "tê liệt",
    "shock_dmg_bonus":      "sấm sét",
    "evasion_rating":       "né tránh",
    "final_dmg_bonus":      "công kích",
    "burn_on_hit_pct":      "thiêu đốt",
    "burn_dmg_bonus":       "lửa",
    "burn_stack_cap_bonus": "lửa chồng lớp",
    "fire_res_shred":       "rút kháng hỏa",
    "shield_regen_pct":     "khiên",
    "final_dmg_reduce":     "giảm sát thương",
    "mark_on_hit_pct":      "đánh dấu",
    "cleanse_on_turn_pct":  "thanh tẩy",
    "debuff_immune_pct":    "miễn dịch",
    "crit_res_rating":      "kháng bạo",
    "soul_drain_on_hit_pct": "hồn phệ",
    "stat_steal_on_hit_pct": "đạo pháp",
}


def scale_stats(base: dict, scale: float) -> dict:
    """Multiply numeric stat values by ``scale`` with sane rounding.

    Floats round to 2 decimals (so a 0.05 final_dmg_bonus stays clean at
    higher tiers); integers floor-divide. ``stack_cap_bonus`` stays integer.
    """
    out = {}
    for k, v in base.items():
        if isinstance(v, int) and "stack_cap_bonus" in k:
            out[k] = max(int(v * scale), v)
        elif isinstance(v, int):
            out[k] = int(round(v * scale))
        else:
            out[k] = round(float(v) * scale, 3)
    return out


# ── Hand-authored overrides ───────────────────────────────────────────────────
# Specific (element, slant_key, rarity) cells whose stat_bonuses + description
# are tuned by hand instead of using the formulaic scale_stats output. The
# override's ``stats`` dict REPLACES the auto-generated stat_bonuses entirely
# (so authors can drop generator stats they don't want and add specials like
# freeze_on_skill_chance, *_res_shred, damage_bonus_from_*_pct, etc.).
# ``desc`` is optional — when present it replaces the template description.
#
# Add a new entry here to lock down a hand-tuned cell. Re-running the generator
# then keeps your edits. Cells not listed continue to use the formulaic output.
OVERRIDES: dict[tuple[str, str, str], dict] = {
    # ── THUY legendaries: ice/cold theme, mana-pool DPS, flowing reflect ────
    ("thuy", "Bang", "epic"): {
        "stats": {
            "mp_pct": 0.28,
            "slow_on_hit_pct": 0.175,
            "evasion_rating": 210,
            "reflect_pct": 0.08,
        },
        "desc": "Thần thể Hàn Băng — hàn khí thấm xương khiến kẻ địch như di chuyển trong dòng nước đặc, kèm theo phản kích băng giá.",
    },
    ("thuy", "Bang", "legendary"): {
        "stats": {
            "mp_pct": 0.4,
            "slow_on_hit_pct": 0.25,
            "evasion_rating": 300,
            "freeze_on_skill_chance": 0.20,
            "reflect_pct": 0.12,
            "thuy_res_shred": 0.12,
            "damage_bonus_from_evasion_pct": 0.10,
        },
        "desc": "Đế thể Hàn Băng — vạn vật chạm phải đều bị đóng băng kết tinh; mỗi kỹ năng tung ra có 20% cơ hội Đông Băng đối thủ, gương băng phản kích đòn tấn công.",
    },
    ("thuy", "Hai", "legendary"): {
        "stats": {
            "mp_pct": 0.6,
            "cooldown_reduce": 0.25,
            "reflect_pct": 0.2,
            "damage_bonus_from_mp_pct": 0.15,
            "mana_stack_per_attack": 1,
            "mana_stack_dmg_bonus": 0.04,
        },
        "desc": "Linh lực sâu như Bích Hải, mỗi đòn tấn công đều tích tụ Linh Khí — biển càng đầy, sát thương càng kinh hoàng.",
    },
    ("thuy", "Luu", "legendary"): {
        "stats": {
            "mp_pct": 0.5,
            "mp_regen_pct": 0.075,
            "cooldown_reduce": 0.2,
            "reflect_applies_effects": True,
            "damage_bonus_from_mp_pct": 0.12,
            "thuy_res_shred": 0.10,
        },
        "desc": "Thân hóa lưu thủy phù vân — đòn phản kích không chỉ trả sát thương, mà còn ép ngược mọi hiệu ứng kẻ thù vừa giáng xuống.",
    },
    # ── PHONG legendaries: cyclone+shock, marked-target assassin, wind+mana ─
    ("phong", "Loc", "legendary"): {
        "stats": {
            "evasion_rating": 350,
            "spd_bonus": 25,
            "shock_on_hit_pct": 0.3,
            "shock_stack_cap_bonus": 1,
            "phong_res_shred": 0.12,
            "damage_bonus_from_evasion_pct": 0.10,
        },
        "desc": "Cuồng phong cuốn theo lôi điện, mỗi đòn đánh đều mang sấm sét — đối thủ tê liệt sâu hơn và phòng phong kháng tan rã.",
    },
    ("phong", "Phieu", "legendary"): {
        "stats": {
            "evasion_rating": 500,
            "spd_bonus": 20,
            "mark_on_hit_pct": 0.3,
            "crit_rating_vs_marked": 200,
            "crit_dmg_vs_marked": 150,
            "damage_bonus_from_evasion_pct": 0.15,
            "phong_res_shred": 0.10,
        },
        "desc": "Phiêu hốt vô hình, kẻ địch bị Phong-Ấn không thể chạy trốn — bạo kích lao đến từ mọi hướng, né càng cao thì sát thương càng kinh hoàng.",
    },
    ("phong", "Tinh", "legendary"): {
        "stats": {
            "evasion_rating": 400,
            "cooldown_reduce": 0.25,
            "mp_regen_pct": 0.06,
            "damage_bonus_from_evasion_pct": 0.12,
            "phong_res_shred": 0.10,
            "mana_stack_per_attack": 1,
        },
        "desc": "Linh phong dung hợp linh khí, mỗi nhịp né tránh lại tích tụ Linh Khí — cuồng phong càng dày, đòn cuối càng dữ dội.",
    },
    # ── THO legendaries: bulk brawler, sand-wall counter, marshland crusher ─
    ("tho", "Cuong", "legendary"): {
        "stats": {
            "hp_pct": 0.4,
            "res_all": 150,
            "final_dmg_reduce": 0.2,
            "damage_bonus_from_hp_pct": 0.12,
            "thorn_from_shield": True,
            "stun_on_hit_pct": 0.08,
        },
        "desc": "Sơn nhạc trấn cương, máu thịt là vũ khí — sinh lực càng dồi dào, đòn đánh càng đè bẹp đối thủ; va chạm với khiên đá khiến chúng choáng váng.",
    },
    ("tho", "Hoang", "legendary"): {
        "stats": {
            "hp_pct": 0.3,
            "shield_regen_pct": 0.09,
            "evasion_rating": 250,
            "shield_cap_pct_bonus": 0.08,
            "damage_bonus_from_shield_pct": 0.12,
            "thorn_from_shield": True,
        },
        "desc": "Hoàng sa trấn ngàn dặm, khiên cát dâng cao gấp bội — gai phản bật ngược từ tường cát, mỗi đòn vào khiên đều biến thành sát thương dội ra.",
    },
    ("tho", "Trach", "legendary"): {
        "stats": {
            "hp_pct": 0.5,
            "shield_regen_pct": 0.125,
            "thorn_pct": 0.25,
            "damage_bonus_from_hp_pct": 0.10,
            "stun_on_hit_pct": 0.08,
            "damage_bonus_from_shield_pct": 0.10,
        },
        "desc": "Trạch địa nuốt vạn vật, đầm lầy ngầm rút lực kẻ địch — máu thịt và khiên cùng hóa gai phản, đối thủ bị choáng ngã trong bùn đất.",
    },
}


def materials_for(rarity: str, elem: str) -> dict:
    """Material cost tier — mirrors the pattern from existing canonical entries."""
    elem_mat = ELEM_MAT_KEY[elem]
    tier = {
        "common":    {"MatDaoCotTinh": 2, elem_mat: 3},
        "uncommon":  {"MatDaoCotTinh": 2, elem_mat: 4},
        "rare":      {"MatDaoCotTinh": 3, "MatThienDaoTuy": 1, elem_mat: 4},
        "epic":      {"MatDaoCotTinh": 4, "MatThienDaoTuy": 2, elem_mat: 5},
        "legendary": {"MatDaoCotTinh": 5, "MatThienDaoTuy": 3, "MatHonNguyenCot": 1, elem_mat: 6},
    }
    return tier[rarity]


def make_entry(elem: str, rarity: str, slant_key: str, vi_root: str, en_root: str, base_stats: dict) -> dict:
    """Build one constitution entry from the archetype + tier.

    Cells listed in ``OVERRIDES`` get their ``stat_bonuses`` and (optionally)
    ``passive_description_vi`` replaced with hand-tuned values — used for
    legendaries that need special-effect mechanics beyond the formulaic
    stat scaling (e.g. ``freeze_on_skill_chance``, ``thorn_from_shield``,
    ``damage_bonus_from_*_pct``).
    """
    t = TIER[rarity]
    key = f"Constitution{elem.capitalize()}{slant_key}_{rarity[:3].capitalize()}"
    vi = f"{RARITY_VI_PREFIX[rarity]}{vi_root} Thể"
    en = f"{RARITY_EN_PREFIX[rarity]}{en_root} Body"

    override = OVERRIDES.get((elem, slant_key, rarity))
    if override:
        stat_bonuses = dict(override["stats"])
        desc = override.get("desc")
        if desc is None:
            main_stat = max(stat_bonuses.items(), key=lambda kv: abs(kv[1]) if isinstance(kv[1], (int, float)) else 0)[0]
            tag = SLANT_TAG_VI.get(main_stat, "thuộc tính")
            desc = DESC_TEMPLATE[rarity].format(elem_vi=ELEM_VI[elem], tag=tag)
    else:
        stat_bonuses = scale_stats(base_stats, t["scale"])
        main_stat = max(base_stats.items(), key=lambda kv: abs(kv[1]) if isinstance(kv[1], (int, float)) else 0)[0]
        tag = SLANT_TAG_VI.get(main_stat, "thuộc tính")
        desc = DESC_TEMPLATE[rarity].format(elem_vi=ELEM_VI[elem], tag=tag)

    return {
        "key": key,
        "vi": vi,
        "en": en,
        "rarity": rarity,
        "element": elem,
        "roll_weight": t["weight"],
        "cost_merit": t["cost"],
        "passive_description_vi": desc,
        "stat_bonuses": stat_bonuses,
        "special_requirements": None,
        "materials": materials_for(rarity, elem),
    }


def _load_existing() -> list[dict]:
    """Return all current constitution entries from the per-element directory.

    Falls back to the legacy ``constitutions.json`` if the directory is empty
    (first-run migration path). Subsequent runs read from the directory so
    hand-written entries outside the generator's archetype matrix survive.
    """
    if OUT_DIR.exists() and any(OUT_DIR.glob("*.json")):
        out: list[dict] = []
        for path in sorted(OUT_DIR.glob("*.json")):
            out.extend(json.loads(path.read_text(encoding="utf-8")))
        return out
    if LEGACY_SRC.exists():
        return json.loads(LEGACY_SRC.read_text(encoding="utf-8"))
    return []


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    existing = _load_existing()

    # Generator-owned keys: every (element, archetype, rarity) cell. On re-run
    # these are REPLACED with fresh output (so override edits in this script
    # propagate). Anything else in the existing data is preserved untouched.
    generated: dict[str, dict] = {}
    for elem in ELEMENTS:
        for slant_key, vi_root, en_root, base in ELEM_ARCHETYPES[elem]:
            for rarity in RARITIES:
                e = make_entry(elem, rarity, slant_key, vi_root, en_root, base)
                generated[e["key"]] = e

    # Bucket by element. Existing entries that the generator doesn't own keep
    # their original content; generator-owned ones are replaced.
    buckets: dict[str, list[dict]] = {e: [] for e in ELEMENTS}
    buckets["universal"] = []
    seen_keys: set[str] = set()

    for entry in existing:
        if entry["key"] in generated:
            entry = generated[entry["key"]]
        elem = entry.get("element")
        buckets[elem if elem else "universal"].append(entry)
        seen_keys.add(entry["key"])

    # Add brand-new generator entries that aren't in the existing data yet.
    new_count = 0
    for key, entry in generated.items():
        if key in seen_keys:
            continue
        elem = entry.get("element")
        buckets[elem if elem else "universal"].append(entry)
        new_count += 1

    # Sort each bucket by rarity then key for stable output
    rarity_order = {r: i for i, r in enumerate(RARITIES)}
    for bucket in buckets.values():
        bucket.sort(key=lambda c: (rarity_order.get(c["rarity"], 99), c["key"]))

    for name, entries in buckets.items():
        out_path = OUT_DIR / f"{name}.json"
        out_path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"  {name:10s} {len(entries):>3} entries → {out_path.relative_to(ROOT)}")

    print(
        f"\nGenerator-owned: {len(generated)} entries "
        f"(replaced existing where present, added {new_count} new)."
    )


if __name__ == "__main__":
    main()
