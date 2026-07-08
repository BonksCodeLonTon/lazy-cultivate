"""Bách Thể Chú Linh — Thể Tu body-part infusion system.

The Thể Tu rework: body realms no longer grant extra Thể Chất slots
(``the_chat.max_slots`` is 1 for every path). Instead, each of the nine
Luyện Thể realms tempers one **body part**; reaching realm ``i`` unlocks
part ``i``. An unlocked part can be **infused** (chú nhập) with one type
of **Tinh Huyết** (vital essence) dropped by the beasts of Thập Vạn Đại
Sơn — the essence decides the power, the part decides its magnitude
(later parts carry a larger ``part_power_mult``).

Storage: ``player.body_part_infusions`` is a JSON-encoded
``{part_key: {"essence": essence_key, "fed": total_consumed}}`` column
mirroring the ``pill_buff_counts`` pattern (legacy plain-string values
parse as ``fed=0``). Bonuses only apply while ``active_axis == "body"`` —
the same identity rule the old multi-slot system used, so pivoting off
the body axis parks the infusions without erasing them.

Awakening (Giác Tỉnh): feeding a part more copies of the SAME essence
accumulates ``fed``; once it reaches ``awaken_threshold`` the part
awakens, adding the essence's ``awakening.stat_bonuses`` (scaled to the
part's realm like the base kit) and, for legendary/mythic essences,
granting the ``awakening.granted_skill`` to the combat bar. Each realm's
part manifests the same essence type at its own realm strength.

Essence definitions live in ``src/data/items/vital_essences.json``
(``type == "vital_essence"``). Each carries ``stat_bonuses`` (+ an
``awakening`` block) using the exact bonus keys ``compute_combat_stats``
already reads, so signature powers (phoenix revive, true damage, reflect,
DoT crit) ride existing engine lanes with zero combat-engine changes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class BodyPart:
    tier: int          # 0..8 — unlocked when body_realm >= tier
    key: str
    vi: str
    realm_vi: str      # the Luyện Thể realm that tempers this part
    emoji: str


BODY_PARTS: tuple[BodyPart, ...] = (
    BodyPart(0, "huyet_dich", "Huyết Dịch", "Luyện Huyết", "🩸"),
    BodyPart(1, "bi_phu",     "Bì Phu",     "Luyện Bì",    "🛡️"),
    BodyPart(2, "can_mach",   "Cân Mạch",   "Luyện Cân",   "🪢"),
    BodyPart(3, "cot_cach",   "Cốt Cách",   "Luyện Cốt",   "🦴"),
    BodyPart(4, "ngu_tang",   "Ngũ Tạng",   "Luyện Phủ",   "🫀"),
    BodyPart(5, "tam_mach",   "Tâm Mạch",   "Pháp Tướng",  "❤️‍🔥"),
    BodyPart(6, "than_khu",   "Thân Khu",   "Kim Thân",    "🗿"),
    BodyPart(7, "nao_hai",    "Não Hải",    "Siêu Phàm",   "🧠"),
    BodyPart(8, "than_hon",   "Thần Hồn",   "Nhập Thánh",  "✨"),
)

_PARTS_BY_KEY: dict[str, BodyPart] = {p.key: p for p in BODY_PARTS}

# Per-tier magnitude growth: part tier i scales its essence's numeric
# stat_bonuses by ``1 + PART_POWER_MULT_PER_TIER × i`` (tier 0 → ×1.0,
# tier 8 → ×3.0). Late-realm parts are the prize of the grind.
PART_POWER_MULT_PER_TIER: float = 0.25

# Stats that never scale with part tier. Mirrors the compound-offensive
# exclusion set used by Hỗn Độn amplification (``_AMP_EXCLUDED_STATS`` in
# cultivation.py) — these multipliers already stack multiplicatively with
# every other source — plus the phoenix revive pair, which is a threshold,
# not a magnitude (a ×3 revive would exceed 100% HP).
NO_SCALE_STATS: frozenset[str] = frozenset({
    "final_dmg_bonus",
    "true_dmg_pct",
    "cooldown_reduce",
    "final_dmg_reduce",
    "phoenix_revive_pct",
    "phoenix_revive_buff_pct",
    # Thresholds/conversions, not magnitudes — a ×3 endure threshold or a
    # >100% overheal conversion would break their semantics.
    "endure_threshold_pct",
    "endure_cooldown",
    "overheal_to_shield_pct",
    # Per-self-stat conversion RATES: these multiply another whole stat pool
    # (def/shield/hp/heal/evasion), so letting nine parts × tier multipliers
    # stack them produced 225% reflect and +450% def-as-damage during the
    # balance pass. They merge additively per part at authored value only.
    "reflect_pct",
    "bonus_base_dmg_per_self_def_pct",
    "bonus_base_dmg_per_self_shield_pct",
    "bonus_base_dmg_per_self_hp_pct",
    "damage_from_heal_pct",
    "damage_bonus_from_evasion_pct",
    "damage_bonus_from_hp_pct",
    "damage_bonus_from_mp_pct",
    "cleanse_retaliate_dmg_pct",
    # Permanent-mutation proc chances (soul drain / stat steal, 2026-07-08
    # nerf): the effects are lifetime-capped, but part-scaling took Đào
    # Ngột's authored 0.10 to ~0.68/1.02 — near-guaranteed procs from one
    # essence. Chance lanes for permanent mutation merge at authored value.
    "soul_drain_on_hit_pct",
    "stat_steal_on_hit_pct",
})

# ── Part affinity (Bộ Vị Tương Thích) ────────────────────────────────────────
# Every essence carries an ``archetype``; every part favors one. A matching
# infusion resonates with the part's nature and pays ×AFFINITY_POTENCY_MULT
# on its scalable stats — the "each realm favors its own kind of blood" rule.
ARCHETYPE_VI: dict[str, str] = {
    "cuong_luc":  "Cường Lực",    # raw might / offense
    "cuong_the":  "Trấn Thủ",     # bulwark / defense
    "tan_tiet":   "Tật Phong",    # swiftness / evasion
    "am_doc":     "Âm Độc",       # venom / dark attrition
    "linh_phap":  "Linh Pháp",    # arcane / spellpower
    "thanh_linh": "Thánh Linh",   # holy / restoration
}

PART_AFFINITY: dict[str, str] = {
    "huyet_dich": "am_doc",       # blood carries venom and attrition
    "bi_phu":     "cuong_the",    # skin is the first bulwark
    "can_mach":   "tan_tiet",     # tendons drive speed
    "cot_cach":   "cuong_luc",    # bones carry raw might
    "ngu_tang":   "thanh_linh",   # organs nourish and restore
    "tam_mach":   "linh_phap",    # the heart channels spell-force
    "than_khu":   "cuong_the",    # the frame is the fortress
    "nao_hai":    "linh_phap",    # the mind commands arcana
    "than_hon":   "thanh_linh",   # the soul answers to the divine
}

AFFINITY_POTENCY_MULT: float = 1.25

# ── Huyết Mạch Cộng Hưởng (bloodline resonance) ──────────────────────────────
# Infusing several parts with the SAME essence resonates the bloodline:
#   ≥3 parts → every contribution of that essence gains ×1.15
#   ≥6 parts → its awakening special-effect kit additionally gains ×1.5
#   9/9      → Hóa Hình unlocked (full transformation, see below)
RESONANCE_T1_COUNT: int = 3
RESONANCE_T1_MULT: float = 1.15
RESONANCE_T2_COUNT: int = 6
RESONANCE_T2_AWAKEN_MULT: float = 1.5

# ── Hóa Hình (beast transformation) ──────────────────────────────────────────
# The endgame of one bloodline: all NINE parts infused with the same essence.
# Once per battle, dropping below ``HOA_HINH_TRIGGER_HP_PCT`` HP transforms
# the cultivator into the beast for the buff's duration — restoring
# ``HOA_HINH_HEAL_PCT`` max HP and stamping the archetype's form buff
# (``HOA_HINH_BUFF_BY_ARCHETYPE`` → entries in effects/buffs.json). The log
# announces the specific beast, the payload follows its archetype.
HOA_HINH_PART_COUNT: int = 9
HOA_HINH_TRIGGER_HP_PCT: float = 0.60
HOA_HINH_HEAL_PCT: float = 0.35

# ── Giác Tỉnh breadth (awakened-part compounding) ────────────────────────────
# Thể Tu's analogue of the Khí Tu linh-căn breadth multiplier: every AWAKENED
# part deepens the whole body's tempering — all body-part bonuses scale by
# ``1 + AWAKENED_BREADTH_PER_PART × awakened_count`` (9/9 awakened → ×1.45).
# Applied inside ``compute_body_part_bonuses`` on top of part/affinity/
# resonance multipliers; ``NO_SCALE_STATS`` and bools stay untouched.
AWAKENED_BREADTH_PER_PART: float = 0.05

HOA_HINH_BUFF_BY_ARCHETYPE: dict[str, str] = {
    "cuong_luc":  "BuffHoaHinhCuongLuc",
    "cuong_the":  "BuffHoaHinhCuongThe",
    "tan_tiet":   "BuffHoaHinhTanTiet",
    "am_doc":     "BuffHoaHinhAmDoc",
    "linh_phap":  "BuffHoaHinhLinhPhap",
    "thanh_linh": "BuffHoaHinhThanhLinh",
}


def get_part(part_key: str) -> BodyPart | None:
    return _PARTS_BY_KEY.get(part_key)


def is_part_unlocked(part: BodyPart, body_realm: int) -> bool:
    """Part ``tier`` unlocks the moment Luyện Thể reaches realm ``tier``."""
    return body_realm >= part.tier


def unlocked_parts(body_realm: int) -> list[BodyPart]:
    return [p for p in BODY_PARTS if is_part_unlocked(p, body_realm)]


def part_power_mult(tier: int) -> float:
    return 1.0 + PART_POWER_MULT_PER_TIER * max(0, tier)


# Essence rarity ladder: normal → magic → rare → legendary → mythic.
# Higher rarities drop in deeper (rarer) zones, so infusion needs fewer
# copies — the drop rate carries the scarcity, not the cost.
ESSENCE_TIERS: tuple[str, ...] = ("normal", "magic", "rare", "legendary", "mythic")

_COST_BY_TIER: dict[str, tuple[int, int]] = {
    # rarity → (base, tier_divisor): cost = base + part.tier // divisor
    "normal":    (3, 1),   # 3..11
    "magic":     (3, 2),   # 3..7
    "rare":      (2, 2),   # 2..6
    "legendary": (2, 3),   # 2..4
    "mythic":    (2, 2),   # 2..6 — bespoke kits, priced like rare
}


def infusion_cost(part: BodyPart, essence_item: dict) -> int:
    """How many essences one infusion of ``part`` consumes.

    Cost falls as rarity climbs (see ``_COST_BY_TIER``) and grows with part
    tier within a rarity. Unknown rarities price like normal — defensive
    against a data typo ever making an infusion free.
    """
    base, divisor = _COST_BY_TIER.get(
        essence_item.get("essence_tier", "normal"), _COST_BY_TIER["normal"],
    )
    return base + part.tier // divisor


# ── Storage codec (JSON column, pill_buff_counts pattern) ────────────────────
# Entry shape: {"essence": essence_key, "fed": total_essences_consumed}.
# Legacy plain-string values (pre-awakening schema) parse as fed=0.


def parse_infusions(raw: str | None) -> dict[str, dict]:
    """Decode the ``body_part_infusions`` column → {part_key: entry}.

    Defensive: malformed JSON or non-dict payloads collapse to empty so a
    corrupt row can never break stat computation. Unknown part keys are
    dropped at read time (stale data from a removed part survives storage
    but contributes nothing).
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for k, v in data.items():
        if str(k) not in _PARTS_BY_KEY or not v:
            continue
        if isinstance(v, dict):
            essence = str(v.get("essence") or "")
            if not essence:
                continue
            try:
                fed = max(0, int(v.get("fed", 0)))
            except (ValueError, TypeError):
                fed = 0
            out[str(k)] = {"essence": essence, "fed": fed}
        else:
            # Legacy schema: value was the bare essence_key string.
            out[str(k)] = {"essence": str(v), "fed": 0}
    return out


def encode_infusions(infusions: dict[str, dict]) -> str:
    """Inverse of ``parse_infusions`` — stable key order for clean diffs."""
    clean: dict[str, dict] = {}
    for k, v in sorted((infusions or {}).items()):
        if k not in _PARTS_BY_KEY or not v:
            continue
        if isinstance(v, str):                 # tolerate legacy in-memory shape
            v = {"essence": v, "fed": 0}
        if not v.get("essence"):
            continue
        clean[k] = {"essence": v["essence"], "fed": max(0, int(v.get("fed", 0)))}
    return json.dumps(clean, separators=(",", ":"))


# ── Essence lookups ──────────────────────────────────────────────────────────


def essence_item(essence_key: str) -> dict | None:
    """Find the vital-essence item definition for ``essence_key``."""
    from src.data.registry import registry
    for item in registry.items.values():
        if (
            item.get("type") == "vital_essence"
            and item.get("essence_key") == essence_key
        ):
            return item
    return None


def all_essence_items() -> list[dict]:
    from src.data.registry import registry
    return [
        i for i in registry.items.values() if i.get("type") == "vital_essence"
    ]


# ── Awakening (Giác Tỉnh) ────────────────────────────────────────────────────


def awaken_threshold(part: BodyPart, item: dict) -> int:
    """Total essences a part must consume before its infusion awakens.

    Base threshold is authored per essence (rarer blood awakens sooner);
    higher-realm parts demand a little more nourishment (+2 per tier).
    """
    base = int(((item.get("awakening") or {}).get("threshold", 0)) or 0)
    if base <= 0:
        return 0            # no awakening authored → never awakens
    return base + 2 * part.tier


def is_awakened(part: BodyPart, entry: dict | None) -> bool:
    """Whether a part's infusion entry has consumed enough to awaken."""
    if not entry or not entry.get("essence"):
        return False
    item = essence_item(entry["essence"])
    if not item:
        return False
    threshold = awaken_threshold(part, item)
    return threshold > 0 and int(entry.get("fed", 0)) >= threshold


# ── Bonus computation ────────────────────────────────────────────────────────


def is_the_tu(active_axis: str | None) -> bool:
    return (active_axis or "") == "body"


def _scale_kit(base: dict, mult: float) -> dict:
    """Scale one stat kit by ``mult``: nested dicts per-sub-key, bools and
    ``NO_SCALE_STATS`` pass through at base."""
    out: dict = {}
    for stat, val in base.items():
        if isinstance(val, bool) or stat in NO_SCALE_STATS:
            out[stat] = val
        elif isinstance(val, dict):
            out[stat] = {
                sub: (type(sv)(sv * mult) if isinstance(sv, int) else sv * mult)
                if isinstance(sv, (int, float)) and not isinstance(sv, bool)
                else sv
                for sub, sv in val.items()
            }
        elif isinstance(val, (int, float)):
            out[stat] = type(val)(round(val * mult)) if isinstance(val, int) else val * mult
        else:
            out[stat] = val
    return out


def essence_archetype(item: dict | None) -> str | None:
    return (item or {}).get("archetype")


def part_affinity_matches(part: BodyPart, item: dict | None) -> bool:
    """True when the essence's archetype is the part's favored one."""
    arch = essence_archetype(item)
    return bool(arch) and PART_AFFINITY.get(part.key) == arch


def resonance_count(infusions: dict[str, dict] | None, body_realm: int,
                    essence_key: str) -> int:
    """How many unlocked parts currently carry ``essence_key``."""
    if not infusions:
        return 0
    total = 0
    for part_key, entry in infusions.items():
        part = _PARTS_BY_KEY.get(part_key)
        if part is None or not is_part_unlocked(part, body_realm):
            continue
        key = entry if isinstance(entry, str) else (entry or {}).get("essence")
        if key == essence_key:
            total += 1
    return total


def awakened_breadth_mult(
    infusions: dict[str, dict] | None, body_realm: int,
) -> float:
    """1 + 0.05 × (unlocked parts whose infusion has awakened)."""
    if not infusions:
        return 1.0
    count = 0
    for part in BODY_PARTS:
        entry = infusions.get(part.key)
        if entry is None or not is_part_unlocked(part, body_realm):
            continue
        if isinstance(entry, str):
            entry = {"essence": entry, "fed": 0}
        if is_awakened(part, entry):
            count += 1
    return 1.0 + AWAKENED_BREADTH_PER_PART * count


def scaled_essence_bonuses(
    essence_key: str, tier: int, awakened: bool = False,
    *, affinity: bool = False, resonance: int = 1, breadth: float = 1.0,
) -> dict:
    """One part's contribution: essence base stats × part_power_mult(tier),
    plus the ``awakening.stat_bonuses`` kit when the part has awakened —
    the same essence type manifests its special effect at each realm's own
    strength.

    Potency multipliers stack multiplicatively on the part multiplier:
      * ``affinity``  — part's favored archetype matches (×1.25)
      * ``resonance`` — parts carrying the same essence: ≥3 → ×1.15 on
        everything; ≥6 → an extra ×1.5 on the awakening kit only.
      * ``breadth``   — the whole-body Giác Tỉnh multiplier
        (``awakened_breadth_mult``), passed in by the aggregate pipeline.
    ``NO_SCALE_STATS`` and bools ignore every multiplier. Unknown essence
    keys yield {} so a data removal can't crash stats.
    """
    item = essence_item(essence_key)
    if not item:
        return {}
    from src.game.systems.cultivation import _merge_bonus_dict

    mult = part_power_mult(tier) * max(1.0, breadth)
    if affinity:
        mult *= AFFINITY_POTENCY_MULT
    if resonance >= RESONANCE_T1_COUNT:
        mult *= RESONANCE_T1_MULT
    out = _scale_kit(item.get("stat_bonuses") or {}, mult)
    if awakened:
        awaken_mult = mult
        if resonance >= RESONANCE_T2_COUNT:
            awaken_mult *= RESONANCE_T2_AWAKEN_MULT
        awaken_kit = (item.get("awakening") or {}).get("stat_bonuses") or {}
        _merge_bonus_dict(out, _scale_kit(awaken_kit, awaken_mult))
    return out


def compute_body_part_bonuses(
    infusions: dict[str, dict] | None,
    active_axis: str | None,
    body_realm: int,
) -> dict:
    """Merge every infused, unlocked part's scaled bonuses into one dict.

    Thể Tu identity rule: returns {} unless ``active_axis == "body"`` —
    mirrors how the old multi-slot constitutions only paid out on-path.
    Locked parts (realm regressed data / bad writes) contribute nothing.
    Awakened parts additionally contribute their essence's special-effect
    kit (see ``is_awakened``).
    """
    if not infusions or not is_the_tu(active_axis):
        return {}
    from src.game.systems.cultivation import _merge_bonus_dict

    breadth = awakened_breadth_mult(infusions, body_realm)
    merged: dict = {}
    for part_key, entry in infusions.items():
        part = _PARTS_BY_KEY.get(part_key)
        if part is None or not is_part_unlocked(part, body_realm):
            continue
        if isinstance(entry, str):             # tolerate legacy in-memory shape
            entry = {"essence": entry, "fed": 0}
        essence_key = entry.get("essence")
        if not essence_key:
            continue
        _merge_bonus_dict(merged, scaled_essence_bonuses(
            essence_key, part.tier,
            awakened=is_awakened(part, entry),
            affinity=part_affinity_matches(part, essence_item(essence_key)),
            resonance=resonance_count(infusions, body_realm, essence_key),
            breadth=breadth,
        ))
    return merged


def hoa_hinh_form(
    infusions: dict[str, dict] | None,
    active_axis: str | None,
    body_realm: int,
) -> tuple[str, str] | None:
    """Return ``(buff_key, beast_vi)`` when Hóa Hình is unlocked, else None.

    Requirements: Thể Tu, all nine parts unlocked (body_realm 8) AND infused
    with the SAME essence. The buff key follows the essence's archetype; the
    beast name feeds the transformation log line.
    """
    if not infusions or not is_the_tu(active_axis):
        return None
    if len(unlocked_parts(body_realm)) < HOA_HINH_PART_COUNT:
        return None
    essence_keys: set[str] = set()
    for part in BODY_PARTS:
        entry = infusions.get(part.key)
        if isinstance(entry, str):
            entry = {"essence": entry}
        key = (entry or {}).get("essence")
        if not key:
            return None                    # any empty part breaks the set
        essence_keys.add(key)
    if len(essence_keys) != 1:
        return None
    item = essence_item(next(iter(essence_keys)))
    if not item:
        return None
    buff_key = HOA_HINH_BUFF_BY_ARCHETYPE.get(essence_archetype(item) or "")
    if not buff_key:
        return None
    return buff_key, item.get("vi", "Yêu Thú")


def granted_awakening_skills(
    infusions: dict[str, dict] | None,
    active_axis: str | None,
    body_realm: int,
) -> list[str]:
    """Skill keys unlocked by awakened infusions (legendary/mythic essences).

    Same gating as the stat pipeline: Thể Tu only, unlocked parts only,
    awakened entries only. Deduplicated in part order so the same essence
    awakened in two parts grants its skill once.
    """
    if not infusions or not is_the_tu(active_axis):
        return []
    out: list[str] = []
    for part in BODY_PARTS:                    # stable part order
        entry = infusions.get(part.key)
        if entry is None or not is_part_unlocked(part, body_realm):
            continue
        if isinstance(entry, str):
            entry = {"essence": entry, "fed": 0}
        if not is_awakened(part, entry):
            continue
        item = essence_item(entry.get("essence", ""))
        skill = ((item or {}).get("awakening") or {}).get("granted_skill")
        if skill and skill not in out:
            out.append(skill)
    return out
