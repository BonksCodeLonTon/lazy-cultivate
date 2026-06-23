"""Character combat stat computation — single source of truth.

Every system that needs derived combat stats (combat, dungeon, tribulation,
status embed) calls compute_combat_stats() so the math can never diverge
between display and actual combat.
"""
from __future__ import annotations

from src.game.models.character import Character
from src.game.constants.balance import (
    REALM_POWER_BONUS_PER_STAGE,
    BASE_MP_REGEN_PCT,
    COOLDOWN_REDUCE_CAP,
    MAX_FINAL_DMG_REDUCE,
    MAX_ELEMENTAL_RES,
    DEFAULT_MANA_STACK_CAP,
    DEFAULT_SHIELD_RECHARGE_DELAY,
)
from src.game.constants.elements import ALL_ELEMENTS, RESISTANCE_KEYS
from src.game.engine import linh_can_effects as lc_effects
from src.game.systems.combat_stats import (
    CombatStats,
    _read_constitution_flags,
    _BURN_PCT_DEFAULT,
    _BLEED_PCT_DEFAULT,
    _SHOCK_PCT_DEFAULT,
    _POISON_PCT_DEFAULT,
)


def active_formation_gem_keys(player) -> list[str]:
    """Flattened gem_keys across EVERY active formation slot.

    ``player.active_formation`` holds a comma-separated list of formation keys
    (one per active slot); this returns the concatenation of each active
    formation's inlaid gems. Returns the single-slot list when the player has
    only one formation equipped.
    """
    from src.game.systems.cultivation import get_active_formations

    actives = get_active_formations(getattr(player, "active_formation", None))
    if not actives:
        return []
    by_key = {
        getattr(f, "formation_key", None): list((f.gem_slots or {}).values())
        for f in getattr(player, "formations", None) or []
    }
    out: list[str] = []
    for key in actives:
        out.extend(by_key.get(key) or [])
    return out


def active_formation_gem_map(player) -> dict[str, list[str]]:
    """Per-formation gem lists keyed by formation_key, restricted to the
    formations currently in an active slot. Used by compute_formations_bonuses
    so each formation's own threshold bonuses + per-gem elemental bonuses fire
    with the right gem set — flattening via ``active_formation_gem_keys``
    would misattribute gems across formations.
    """
    from src.game.systems.cultivation import get_active_formations

    actives = set(get_active_formations(getattr(player, "active_formation", None)))
    if not actives:
        return {}
    return {
        getattr(f, "formation_key"): list((f.gem_slots or {}).values())
        for f in getattr(player, "formations", None) or []
        if getattr(f, "formation_key", None) in actives
    }


# ── Grouped-key denesting ────────────────────────────────────────────────
# JSON authors prefer the nested ``element_pen`` / ``dmg_taken`` shape
# (single key with a sub-dict per element/kind/state). The legacy bonus reads
# in build_combat_stats use flat keys (``burn_dmg_bonus``, ``crit_rating_vs_<state>``,
# …). This pass flattens grouped → flat in-place so authors get the clean
# nested JSON without forcing every read site to switch. Existing flat keys
# in older data continue to work — the denester only *adds* flat keys when a
# nested counterpart is present.

# Nested-key → ``(suffix_template, value_converter)`` describing how each
# inner key maps to a flat key. ``{}`` placeholder is replaced by the inner
# key. ``crit_amp_vs`` is special-cased below because its inner value is a
# dict, not a scalar.
_GROUPED_FLAT_KEYS: dict[str, tuple[str, type]] = {
    "dot_dmg_bonus_by_kind":   ("{}_dmg_bonus",            float),
    "dot_stack_cap_bonus":     ("{}_stack_cap_bonus",      int),
    "dot_per_stack_pct_bonus": ("{}_per_stack_pct_bonus",  float),
}


def _denest_grouped_bonuses(bonuses: dict) -> None:
    """Flatten author-friendly nested keys into the flat read-site keys.

    Mutates ``bonuses`` in place. Safe to call multiple times — pops the
    nested entry after fanning it out so a second call is a no-op.
    """
    for grouped_key, (template, conv) in _GROUPED_FLAT_KEYS.items():
        nested = bonuses.pop(grouped_key, None)
        if not nested:
            continue
        for inner_key, val in nested.items():
            flat_key = template.format(inner_key)
            bonuses[flat_key] = conv(bonuses.get(flat_key, conv(0)) + conv(val))
    crit_amp = bonuses.pop("crit_amp_vs", None)
    if crit_amp:
        for state, amps in crit_amp.items():
            if not isinstance(amps, dict):
                continue
            r = int(amps.get("rating", 0))
            d = int(amps.get("dmg", 0))
            if r:
                bonuses[f"crit_rating_vs_{state}"] = int(bonuses.get(f"crit_rating_vs_{state}", 0)) + r
            if d:
                bonuses[f"crit_dmg_vs_{state}"] = int(bonuses.get(f"crit_dmg_vs_{state}", 0)) + d


# ── Finalization helpers ─────────────────────────────────────────────────
# These run at the tail of compute_combat_stats, after all bonus sources
# (formation, constitution, linh_can, equipment) have been folded in. Each
# mutates a small slice of the final stat block; keeping them as named
# helpers makes the order of operations easy to reason about.

def _apply_formation_mp_reserve(
    mp_max: int,
    form_bonuses: dict,
    active_formation_keys: list[str] | None,
    formation_stages: int,
) -> tuple[int, int, float]:
    """Lock part of mp_max behind active formations. Two reservation
    sources stack here, both bound by the same MAX cap:
      1. Per-formation gem reservation (from active formation slots' gems)
      2. Per-formation channeling cost (the ``formation_skill_key`` tied to
         each active formation contributes its ``reserved_mp_pct``)
    Trận Đạo path reduction is already applied inside each helper, so we
    just sum and re-cap.

    Returns: (remaining mp_max, mp_reserved, reserve_pct).
    """
    from src.game.constants.balance import FORMATION_MAX_RESERVE_PCT
    from src.game.systems.cultivation import compute_formation_skill_reserve_pct

    skill_reserve_pct = compute_formation_skill_reserve_pct(
        active_formation_keys, formation_stages=formation_stages,
    )
    raw_reserve = float(form_bonuses.get("_mp_reserve_pct", 0.0)) + skill_reserve_pct
    reserve_pct = max(0.0, min(FORMATION_MAX_RESERVE_PCT, raw_reserve))
    mp_reserved = int(mp_max * reserve_pct)
    return max(0, mp_max - mp_reserved), mp_reserved, reserve_pct


def _apply_hp_to_shield(
    hp_max: int,
    shield_max_base: int,
    hp_to_shield_pct: float,
) -> tuple[int, int, float]:
    """Convert a fraction of hp_max into flat shield_max_base. Clamped at
    0.95 so HP never drops to 0. Applied last so every other hp/shield
    bonus has already been folded in.

    Returns: (hp_max, shield_max_base, clamped hp_to_shield_pct).
    """
    hp_to_shield_pct = max(0.0, min(0.95, hp_to_shield_pct))
    if hp_to_shield_pct > 0:
        converted = int(hp_max * hp_to_shield_pct)
        shield_max_base += converted
        hp_max = max(1, hp_max - converted)
    return hp_max, shield_max_base, hp_to_shield_pct


def _apply_pill_buffs(
    char: Character,
    spd_final: int,
    def_stat: int,
    atk: int,
    element_dmg_bonus: dict[str, float],
) -> tuple[int, int, int]:
    """Apply permanent combat-buff pill bonuses. Each consume of a buff_*
    /buff_element_* pill raises the matching counter (capped at
    PILL_BUFF_CAP per key); we read those counters and add
    ``count × per_pill_value`` to the relevant stat. Applied before
    toxicity so the dan_doc penalty still stacks on top.

    ``element_dmg_bonus`` is mutated in place. Returns updated scalars
    (spd_final, def_stat, atk).
    """
    pill_counts = getattr(char, "pill_buff_counts", None) or {}
    if not pill_counts:
        return spd_final, def_stat, atk

    from src.game.systems.pill_buffs import total_buff_for_stat

    spd_final += int(round(total_buff_for_stat(pill_counts, "spd")))
    def_stat += int(round(total_buff_for_stat(pill_counts, "def_stat")))
    atk += int(round(total_buff_for_stat(pill_counts, "atk")))
    for elem in ("kim", "moc", "thuy", "hoa", "tho", "loi", "phong", "quang", "am"):
        bump = total_buff_for_stat(pill_counts, f"element_dmg_bonus_{elem}")
        if bump:
            element_dmg_bonus[elem] = element_dmg_bonus.get(elem, 0.0) + bump
    return spd_final, def_stat, atk


def _apply_toxicity_penalty(
    char: Character,
    final_dmg_bonus: float,
    hp_regen_pct: float,
) -> tuple[float, float]:
    """Đan Độc penalty — pill toxicity drags down outgoing damage and
    slows HP regen. Applied AFTER all bonuses so it's a true override that
    equipment / constitutions can't escape. final_dmg_bonus can go
    negative; the damage pipeline multiplies by ``(1 + final_dmg_bonus)``
    which is fine for values down to -1.0 (toxicity caps at -0.25).
    """
    dan_doc = int(getattr(char, "dan_doc", 0) or 0)
    if dan_doc <= 0:
        return final_dmg_bonus, hp_regen_pct

    from src.game.systems.toxicity import (
        final_dmg_penalty as _tox_final_dmg_penalty,
        hp_regen_multiplier as _tox_hp_regen_mult,
    )
    final_dmg_bonus -= _tox_final_dmg_penalty(dan_doc)
    hp_regen_pct *= _tox_hp_regen_mult(dan_doc)
    return final_dmg_bonus, hp_regen_pct


def _build_formation_dicts(bonuses: dict) -> dict:
    """Formation-tunable nested dicts read straight from ``bonuses``.

    Each entry is clamped at read time (non-negative counts, [0, 1] pcts) so
    a misconfigured gem stack can't drive a formation past its design limits.
    None of these are equipment-merged — they are pure pass-through reads,
    which is why they extract cleanly. Returns a ``{field: dict}`` map the
    caller unpacks into the matching ``CombatStats`` fields.
    """
    # Per-element MP cost multiplier (extra cost on top of base 1.0×). Comes
    # from formations like Thiên Nhất Sinh Thủy Trận that amplify a single
    # element's MP outlay (and damage, via the base + mp_cost formula).
    element_mp_cost_mult: dict[str, float] = dict(bonuses.get("element_mp_cost_mult", {}) or {})
    # Cửu Khúc Hoàng Hà formation tunables — single dict (see CombatStats).
    # Followup chain power capped at 1.0 so misconfigured stacks can't push
    # past 100 % and double-trigger the formula.
    _ck_raw = bonuses.get("cuu_khuc") or {}
    cuu_khuc: dict = {
        "per_hit":            max(0, int(_ck_raw.get("per_hit", 0))),
        "atk_reduce_pct":     max(0.0, float(_ck_raw.get("atk_reduce_pct", 0.0))),
        "res_shred_pct":      max(0.0, float(_ck_raw.get("res_shred_pct", 0.0))),
        "followup_pct":       max(0.0, min(1.0, float(_ck_raw.get("followup_pct", 0.0)))),
        "followup_skill_key": str(_ck_raw.get("followup_skill_key", "")),
    }
    # Vạn Kiếp Lôi Ngục Trận — single dict pulled from bonuses. Every entry
    # is non-negative (clamps any misconfigured negative gem stack to 0) so
    # the formation can't end up healing the target or refunding > MP_max.
    _vk_raw = bonuses.get("loi_kiep_an") or {}
    loi_kiep_an: dict = {
        "per_cast_bonus":           max(0, int(_vk_raw.get("per_cast_bonus", 0))),
        "bolt_base_dmg":            max(0, int(_vk_raw.get("bolt_base_dmg", 0))),
        "bolt_matk_scale":          max(0.0, float(_vk_raw.get("bolt_matk_scale", 0.0))),
        "bolt_te_liet_chance":      max(0.0, min(1.0, float(_vk_raw.get("bolt_te_liet_chance", 0.0)))),
        "capstone_base_floor":      max(0, int(_vk_raw.get("capstone_base_floor", 0))),
        "capstone_base_per_stack":  max(0, int(_vk_raw.get("capstone_base_per_stack", 0))),
        "capstone_matk_floor":      max(0.0, float(_vk_raw.get("capstone_matk_floor", 0.0))),
        "capstone_matk_per_stack":  max(0.0, float(_vk_raw.get("capstone_matk_per_stack", 0.0))),
        "capstone_te_liet_chance":  max(0.0, min(1.0, float(_vk_raw.get("capstone_te_liet_chance", 0.0)))),
        "capstone_refund_mp_pct":   max(0.0, min(1.0, float(_vk_raw.get("capstone_refund_mp_pct", 0.0)))),
    }
    # Thiên Lôi Tru Tà Trận — single dict pulled from bonuses. Clamping
    # mirrors the loi_kiep_an block: counts/cooldowns are non-negative ints,
    # pcts are clamped to [0, 1] so misconfigured gems can't push past 100%.
    _tt_raw = bonuses.get("thien_loi_tru_ta") or {}
    thien_loi_tru_ta: dict = {
        "per_debuff_amp":         max(0.0, float(_tt_raw.get("per_debuff_amp", 0.0))),
        "debuff_count_cap":       max(0, int(_tt_raw.get("debuff_count_cap", 8))),
        "capstone_threshold":     max(1, int(_tt_raw.get("capstone_threshold", 5))),
        "dai_phan_cd":            max(0, int(_tt_raw.get("dai_phan_cd", 7))),
        "dai_phan_buff_strip":    max(0, int(_tt_raw.get("dai_phan_buff_strip", 1))),
        "low_hp_threshold_pct":   max(0.0, min(1.0, float(_tt_raw.get("low_hp_threshold_pct", 0.0)))),
        "low_hp_amp_pct":         max(0.0, float(_tt_raw.get("low_hp_amp_pct", 0.0))),
        "bolt_base":              max(0, int(_tt_raw.get("bolt_base", 0))),
        "bolt_base_per_debuff":   max(0, int(_tt_raw.get("bolt_base_per_debuff", 0))),
        "bolt_matk_scale":        max(0.0, float(_tt_raw.get("bolt_matk_scale", 0.0))),
        "bolt_matk_per_debuff":   max(0.0, float(_tt_raw.get("bolt_matk_per_debuff", 0.0))),
        "bolt_te_liet_chance_floor":     max(0.0, min(1.0, float(_tt_raw.get("bolt_te_liet_chance_floor", 0.0)))),
        "bolt_te_liet_chance_per_debuff": max(0.0, float(_tt_raw.get("bolt_te_liet_chance_per_debuff", 0.0))),
        "capstone_base":          max(0, int(_tt_raw.get("capstone_base", 0))),
        "capstone_base_per_debuff": max(0, int(_tt_raw.get("capstone_base_per_debuff", 0))),
        "capstone_matk_scale":    max(0.0, float(_tt_raw.get("capstone_matk_scale", 0.0))),
        "capstone_matk_per_debuff": max(0.0, float(_tt_raw.get("capstone_matk_per_debuff", 0.0))),
        "capstone_te_liet_chance": max(0.0, min(1.0, float(_tt_raw.get("capstone_te_liet_chance", 0.0)))),
        "capstone_te_liet_duration": max(0, int(_tt_raw.get("capstone_te_liet_duration", 2))),
    }
    # Thái Cực Âm Dương Lôi Đại Trận — single dict pulled from bonuses.
    # Capstone target HP-pct nerfed to 0.20 (was 0.50 in initial proposal) so
    # the fusion can't trivialize high-HP bosses on a single fire.
    _ad_raw = bonuses.get("thai_cuc_am_duong_loi") or {}
    thai_cuc_am_duong_loi: dict = {
        # Dương phase (attack pole) payload
        "duong_base":             max(0, int(_ad_raw.get("duong_base", 0))),
        "duong_matk_scale":       max(0.0, float(_ad_raw.get("duong_matk_scale", 0.0))),
        "duong_dmg_amp_pct":      max(0.0, float(_ad_raw.get("duong_dmg_amp_pct", 0.0))),
        "duong_shock_stacks":     max(0, int(_ad_raw.get("duong_shock_stacks", 1))),
        "duong_te_liet_chance":   max(0.0, min(1.0, float(_ad_raw.get("duong_te_liet_chance", 0.0)))),
        # Âm phase (utility pole) payload
        "am_shock_stacks":        max(0, int(_ad_raw.get("am_shock_stacks", 2))),
        "am_hp_restore_pct":      max(0.0, min(1.0, float(_ad_raw.get("am_hp_restore_pct", 0.0)))),
        "am_mp_restore_pct":      max(0.0, min(1.0, float(_ad_raw.get("am_mp_restore_pct", 0.0)))),
        "am_buff_extend_cap":     max(0, int(_ad_raw.get("am_buff_extend_cap", 6))),
        "am_apply_te_nguyen_luc": bool(_ad_raw.get("am_apply_te_nguyen_luc", True)),
        # Thái Cực Lưỡng Nghi Lôi fusion (capstone)
        "fusion_base":            max(0, int(_ad_raw.get("fusion_base", 0))),
        "fusion_matk_scale":      max(0.0, float(_ad_raw.get("fusion_matk_scale", 0.0))),
        "fusion_target_hp_pct":   max(0.0, min(1.0, float(_ad_raw.get("fusion_target_hp_pct", 0.0)))),
        "fusion_shock_bonus_per_stack": max(0, int(_ad_raw.get("fusion_shock_bonus_per_stack", 0))),
        "fusion_te_liet_duration": max(0, int(_ad_raw.get("fusion_te_liet_duration", 3))),
        "fusion_cd":              max(0, int(_ad_raw.get("fusion_cd", 8))),
        "khi_cap":                max(1, int(_ad_raw.get("khi_cap", 5))),
        # 10-gem tier: both poles fire every tick (duality collapses).
        "dual_phase_fire":        bool(_ad_raw.get("dual_phase_fire", False)),
    }
    # Cửu Long Thần Hỏa Trận — single dict pulled from bonuses.
    _cl_raw = bonuses.get("cuu_long") or {}
    cuu_long: dict = {
        "dmg_pct_bonus":   max(0.0, float(_cl_raw.get("dmg_pct_bonus", 0.0))),
        "crit_rating":     max(0, int(_cl_raw.get("crit_rating", 0))),
        "crit_dmg_rating": max(0, int(_cl_raw.get("crit_dmg_rating", 0))),
        "stun_chance":     max(0.0, min(1.0, float(_cl_raw.get("stun_chance", 0.0)))),
        "finisher_pct":    max(0.0, float(_cl_raw.get("finisher_pct", 0.0))),
    }
    # Xích Luyện Tỏa Hồn Trận — single dict pulled from bonuses (mirrors
    # element_pen / dmg_taken). Clamp each delta to (-1.0, +∞) so a
    # misconfigured negative stack can't drive a debuff past 100% reduction.
    toa_hon_amp: dict[str, float] = {
        k: max(-1.0, float(v))
        for k, v in (bonuses.get("toa_hon_amp") or {}).items()
    }
    # Per-element "damage taken" amps — single dict pulled from bonuses
    # (mirrors element_pen). Floor each entry at 0 so a misconfigured
    # negative gem stack can't end up healing a target on the amp step.
    dmg_taken: dict[str, float] = {
        e: max(0.0, float(v))
        for e, v in (bonuses.get("dmg_taken") or {}).items()
    }
    return {
        "element_mp_cost_mult": element_mp_cost_mult,
        "cuu_khuc": cuu_khuc,
        "loi_kiep_an": loi_kiep_an,
        "thien_loi_tru_ta": thien_loi_tru_ta,
        "thai_cuc_am_duong_loi": thai_cuc_am_duong_loi,
        "cuu_long": cuu_long,
        "toa_hon_amp": toa_hon_amp,
        "dmg_taken": dmg_taken,
    }


def _build_stack_cap_bonuses(
    stack_cap_bonus_by_kind: dict[str, int], shock_stack_cap_bonus: int,
) -> dict[str, int]:
    """Route per-kind DoT stack-cap bonuses (+ shock) onto debuff effect keys.

    burn/bleed/poison feed in from the consolidated per-kind dict; shock keeps
    its own scalar since its build path differs from that family.
    """
    kind_to_debuff = {
        "burn":   "DebuffThieuDot",
        "bleed":  "DebuffChayMau",
        "poison": "DebuffDocTo",
    }
    out: dict[str, int] = {}
    for kind, debuff_key in kind_to_debuff.items():
        v = stack_cap_bonus_by_kind.get(kind, 0)
        if v:
            out[debuff_key] = v
    if shock_stack_cap_bonus:
        out["DebuffSocDien"] = shock_stack_cap_bonus
    return out


def _aggregate_bonuses(
    char: Character,
    gem_keys: list[str] | None,
    gem_keys_by_formation: dict[str, list[str]] | None,
) -> tuple[dict, dict, list[str], int]:
    """Formation + constitution + Linh Căn bonus pipeline (axis-clamped).

    Resolves the active formations/gems, scales them by Trận Đạo progress,
    folds in the constitution (flag-gated process read) and Linh Căn layers,
    merges everything and denests author-friendly grouped keys. Returns
    ``(bonuses, form_bonuses, active_formations, formation_stages)`` for the
    downstream stat derivation, resistance, and MP-reservation stages.
    """
    from src.game.systems.cultivation import (
        compute_formations_bonuses, compute_constitution_bonuses, merge_bonuses,
        get_active_formations,
    )
    from src.game.constants.linh_can import compute_linh_can_bonuses

    # ── Bonus merge ──────────────────────────────────────────────────────────
    # Trận Đạo cultivation progress scales formation bonuses: late-game
    # formation cultivators get meaningfully stronger formation effects.
    formation_stages = char.formation_realm * 9 + char.formation_level
    active_axis = getattr(char, "active_axis", "qi") or "qi"
    active_formations = get_active_formations(char.active_formation)

    # Build a per-formation gem map. Callers that only have a flat gem list
    # (single-formation contexts) still work because a single active
    # formation always owns all the gems.
    if gem_keys_by_formation is None:
        if len(active_formations) == 1 and gem_keys:
            gem_keys_by_formation = {active_formations[0]: list(gem_keys)}
        else:
            gem_keys_by_formation = {}

    # Pass active_axis + relevant realms through so the bonus pipeline can
    # clamp past-cap entries to whatever the current path supports — a
    # player who switches off body/formation focus stops cashing in on
    # extra slots until they switch back.
    form_bonuses = compute_formations_bonuses(
        active_formations,
        active_axis,
        char.formation_realm,
        gem_keys_by_formation=gem_keys_by_formation,
        formation_stages=formation_stages,
    )
    # Constitution Process read path — flag-gated. When OFF (default) we pass
    # ``process_levels=None`` so ``compute_constitution_bonuses`` reads the flat
    # ``stat_bonuses`` exactly as before (byte-identical, never touches the
    # process code). When ON, a populated ``char.constitution_levels`` resolves
    # each equipped body at its stored level; an empty map (NPC-style Characters
    # built outside the DB pipeline) still collapses to ``None`` → inert.
    from src.utils.config import settings
    const_process_levels = (
        (char.constitution_levels or None)
        if settings.constitution_process_enabled
        else None
    )
    const_bonuses = compute_constitution_bonuses(
        char.constitution_type, active_axis, char.body_realm,
        process_levels=const_process_levels,
    )
    # Per-element levels drive the linh_can stat scaling. NPC-style Characters
    # that don't carry ``linh_can_levels`` (built outside the DB pipeline) fall
    # back to level 1 per element from the bare ``linh_can`` list.
    lc_levels = dict(getattr(char, "linh_can_levels", {}) or {})
    if not lc_levels:
        lc_levels = {elem: 1 for elem in char.linh_can}
    lc_bonuses    = compute_linh_can_bonuses(lc_levels)

    # Khí Tu archetype payoff — rewards breadth (many high-level Linh Căn)
    # in the same data-driven shape as Hỗn Độn's all_passives_multiplier.
    # Gated on is_khi_tu (active_axis == "qi") so off-path players can't
    # dip into the bonus while focusing body or formation. Compound-offensive
    # stats are excluded for the same reason they're excluded from Hỗn Độn —
    # preventing one-shot damage against world bosses.
    from src.game.systems.cultivation import is_khi_tu
    from src.game.constants.linh_can import linh_can_breadth_multiplier
    _BREADTH_EXCLUDED_STATS: frozenset[str] = frozenset({
        "final_dmg_bonus", "true_dmg_pct", "cooldown_reduce", "final_dmg_reduce",
    })
    if is_khi_tu(active_axis):
        breadth_mult = linh_can_breadth_multiplier(lc_levels)
        if breadth_mult > 1.0:
            scaled: dict = {}
            for k, v in lc_bonuses.items():
                if isinstance(v, bool) or k in _BREADTH_EXCLUDED_STATS:
                    scaled[k] = v
                elif isinstance(v, (int, float)):
                    scaled[k] = type(v)(v * breadth_mult) if isinstance(v, int) else v * breadth_mult
                else:
                    scaled[k] = v
            lc_bonuses = scaled

    # Mộc Linh Căn passive: Hồi Xuân — always heals a flat % HP per turn in combat
    moc_regen = lc_effects.get_regen_bonus(char.linh_can, lc_levels.get("moc", 1))
    if moc_regen:
        lc_bonuses["hp_regen_pct"] = lc_bonuses.get("hp_regen_pct", 0.0) + moc_regen

    bonuses = merge_bonuses(form_bonuses, const_bonuses, lc_bonuses)
    # Flatten grouped author-friendly nested keys into the flat keys the
    # rest of this function already reads. Authors can declare:
    #   ``dot_dmg_bonus_by_kind: {"burn": 0.15}``
    # in JSON / passive_bonus / gem thresholds and it lands here as
    # ``burn_dmg_bonus: 0.15`` — same data, no churn on the read sites.
    _denest_grouped_bonuses(bonuses)
    return bonuses, form_bonuses, active_formations, formation_stages


def compute_combat_stats(
    char: Character,
    gem_count: int = 0,
    equip_stats: dict | None = None,
    gem_keys: list[str] | None = None,
    gem_keys_by_formation: dict[str, list[str]] | None = None,
) -> CombatStats:
    """Compute all derived combat stats for a player character.

    Applies (in order): formation bonuses → constitution bonuses →
    linh_can bonuses → realm_power_bonus → equipment bonuses →
    formation MP reservation (locks part of mp_max).

    Args:
        char:        Character dataclass (from DB or model layer).
        gem_count:   Number of gems inlaid in the active formation (used if
                     ``gem_keys`` is not supplied and only one slot active).
        equip_stats: Pre-computed equipment stat totals from
                     ``compute_equipment_stats(equipped_instances)``.
                     Pass None (or {}) when equipment should be ignored.
        gem_keys:    Flat gem list — valid ONLY when the character has a
                     single formation active. Multi-slot Trận Tu callers must
                     pass ``gem_keys_by_formation`` instead so threshold +
                     per-gem bonuses attach to the right formation.
        gem_keys_by_formation: ``{formation_key: [gem_keys]}`` when multiple
                     formations are active. Takes precedence over ``gem_keys``.

    Returns:
        CombatStats with every field ready for use in Combatant construction
        or status-embed display. ``mp_max`` is already reduced by reservation;
        ``mp_reserved`` carries the locked amount for UI.
    """
    from src.game.systems.cultivation import (
        compute_hp_max, compute_mp_max,
        compute_atk, compute_matk, compute_def_stat,
    )

    # Formation / constitution / Linh Căn bonus pipeline (axis-clamped, denested).
    bonuses, form_bonuses, active_formations, formation_stages = _aggregate_bonuses(
        char, gem_keys, gem_keys_by_formation,
    )

    # ── L6 Vạn Khí Giai Kim — equipment-stat amplifier (build-time pre-pass) ──
    # Thái Bạch Canh Kim Thể's milestone-6 stamps a config-only
    # ``equip_stat_amp_pct`` (×1.25) that amplifies ONLY the equipment-derived
    # portion of the stat sheet, baked static for the whole battle. Runs BEFORE
    # the equip-merge block below so every ``equip_stats.get(...)`` read sees the
    # amplified value; the key is popped from ``bonuses`` so it never lands as a
    # stat. Inert (no-op) when the body isn't equipped / the flag is off — the
    # key is simply absent, leaving ``equip_stats`` untouched.
    _equip_amp = float(bonuses.pop("equip_stat_amp_pct", 0.0))
    if _equip_amp > 0 and equip_stats:
        # Copy so we never mutate the caller's dict; amplify each numeric value
        # (bools and nested dicts pass through unscaled — they aren't flat
        # equipment magnitudes).
        equip_stats = {
            k: (v * (1.0 + _equip_amp)
                if isinstance(v, (int, float)) and not isinstance(v, bool)
                else v)
            for k, v in equip_stats.items()
        }

    # ── Base stats from cultivation ───────────────────────────────────────────
    hp_max   = compute_hp_max(char, bonuses)
    mp_max   = compute_mp_max(char, bonuses)
    atk      = compute_atk(char, bonuses)
    matk     = compute_matk(char, bonuses)
    def_stat = compute_def_stat(char, bonuses)

    # Realm power: flat damage bonus scaling with total cultivation stages
    total_stages = (
        char.body_realm * 9 + char.body_level
        + char.qi_realm  * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    )
    realm_power_bonus = total_stages * REALM_POWER_BONUS_PER_STAGE

    # Equipment can contribute flat spd_bonus too (e.g. boot affixes).
    _equip_spd_bonus = int((equip_stats or {}).get("spd_bonus", 0))
    spd_base  = char.stats.spd + bonuses.get("spd_bonus", 0) + _equip_spd_bonus
    # ``spd_pct`` aggregates the constitution/formation/linh_can layer
    # (``bonuses``) and any equipment/skill-passive layer (``equip_stats``) —
    # equipped passives like Vạn Lý Truy Phong's +20 % spd land in equip_stats
    # via ``compute_skill_passive_stats`` and need this read to stick.
    _spd_pct_total = (
        float(bonuses.get("spd_pct", 0.0))
        + float((equip_stats or {}).get("spd_pct", 0.0))
    )
    spd_final = round(spd_base * (1.0 + _spd_pct_total))

    # ── Combat ratings ────────────────────────────────────────────────────────
    crit_rating     = char.stats.crit_rating     + bonuses.get("crit_rating", 0)
    crit_dmg_rating = char.stats.crit_dmg_rating + bonuses.get("crit_dmg_rating", 0)
    evasion_rating  = char.stats.evasion_rating  + bonuses.get("evasion_rating", 0)
    crit_res_rating = char.stats.crit_res_rating + bonuses.get("crit_res_rating", 0)
    accuracy_rating = bonuses.get("accuracy_rating", 0)

    final_dmg_bonus  = char.stats.final_dmg_bonus + bonuses.get("final_dmg_bonus", 0.0) + realm_power_bonus
    final_dmg_reduce = bonuses.get("final_dmg_reduce", 0.0)
    hp_regen_pct     = bonuses.get("hp_regen_pct", 0.0)
    hp_regen_flat    = int(bonuses.get("hp_regen_flat", 0))
    mp_regen_pct     = BASE_MP_REGEN_PCT + bonuses.get("mp_regen_pct", 0.0)
    mp_regen_flat    = int(bonuses.get("mp_regen_flat", 0))
    heal_pct         = bonuses.get("heal_pct", 0.0)
    cooldown_reduce  = char.stats.cooldown_reduce + bonuses.get("cooldown_reduce", 0.0)

    # On-hit / on-crit procs from formation thresholds
    burn_on_hit_pct   = bonuses.get("burn_on_hit_pct", 0.0)
    # Per-DoT-kind stack bonuses (burn/bleed/poison). JSON / equipment /
    # constitution data keep flat ``<kind>_stack_cap_bonus`` and
    # ``<kind>_per_stack_pct_bonus`` keys; aggregated into kind-keyed dicts
    # below so the equip merge / Combatant wiring stays a single loop. Final
    # ``stack_cap_bonuses`` (debuff-keyed) is built further down; per-stack
    # pcts feed the engine's scalar Combatant fields at construction.
    _STACK_DOT_KINDS = ("burn", "bleed", "poison")
    _stack_cap_bonus_by_kind: dict[str, int] = {
        k: int(bonuses.get(f"{k}_stack_cap_bonus", 0)) for k in _STACK_DOT_KINDS
    }
    _per_stack_pct_bonus_by_kind: dict[str, float] = {
        k: float(bonuses.get(f"{k}_per_stack_pct_bonus", 0.0)) for k in _STACK_DOT_KINDS
    }
    bonus_dmg_vs_burn = float(bonuses.get("bonus_dmg_vs_burn", 0.0))
    dot_can_crit      = bool(bonuses.get("dot_can_crit", False))
    # Per-element penetration — single dict-of-dicts pulled from bonuses.
    # Constitutions / Linh Căn / formations all emit ``element_pen``;
    # equip-side merge happens further below.
    element_pen: dict[str, float] = {
        e: float(v) for e, v in (bonuses.get("element_pen") or {}).items()
    }
    # Kim-build fields (bleed stack/per-stack pcts consolidated above in
    # ``_stack_cap_bonus_by_kind`` / ``_per_stack_pct_bonus_by_kind``)
    bleed_on_hit_pct        = float(bonuses.get("bleed_on_hit_pct", 0.0))
    poison_on_hit_pct       = float(bonuses.get("poison_on_hit_pct", 0.0))
    bleed_heal_reduce       = float(bonuses.get("bleed_heal_reduce", 0.0))
    # Conditional crit amps consolidated into ``crit_amp_vs`` (see CombatStats).
    # JSON keeps flat ``crit_rating_vs_<state>`` / ``crit_dmg_vs_<state>``
    # keys for backwards compat; translated into the nested dict here.
    _CRIT_AMP_STATES = ("bleed", "marked", "drained")
    crit_amp_vs: dict[str, dict[str, int]] = {}
    for _state in _CRIT_AMP_STATES:
        _r = int(bonuses.get(f"crit_rating_vs_{_state}", 0))
        _d = int(bonuses.get(f"crit_dmg_vs_{_state}", 0))
        if _r or _d:
            crit_amp_vs[_state] = {"rating": _r, "dmg": _d}
    true_dmg_pct            = float(bonuses.get("true_dmg_pct", 0.0))
    life_steal_pct           = float(bonuses.get("life_steal_pct", 0.0))
    crit_dmg_rating_to_dmg_pct = float(bonuses.get("crit_dmg_rating_to_dmg_pct", 0.0))
    # Moc-build fields
    dot_leech_pct            = float(bonuses.get("dot_leech_pct", 0.0))
    damage_from_heal_pct     = float(bonuses.get("damage_from_heal_pct", 0.0))
    damage_bonus_from_hp_pct = float(bonuses.get("damage_bonus_from_hp_pct", 0.0))
    # Thủy-build fields
    reflect_pct              = float(bonuses.get("reflect_pct", 0.0))
    reflect_applies_effects  = bool(bonuses.get("reflect_applies_effects", False))
    damage_bonus_from_mp_pct = float(bonuses.get("damage_bonus_from_mp_pct", 0.0))
    mp_leech_pct             = float(bonuses.get("mp_leech_pct", 0.0))
    mana_stack_cap_bonus     = int(bonuses.get("mana_stack_cap_bonus", 0))
    mana_stack_per_attack    = int(bonuses.get("mana_stack_per_attack", 0))
    mana_stack_dmg_bonus     = float(bonuses.get("mana_stack_dmg_bonus", 0.0))
    # Thổ-build fields
    shield_regen_pct             = float(bonuses.get("shield_regen_pct", 0.0))
    shield_regen_flat            = int(bonuses.get("shield_regen_flat", 0))
    shield_max_base              = int(bonuses.get("shield_max_base", 0))
    shield_max_flat              = int(bonuses.get("shield_max_flat", 0))
    shield_max_pct               = float(bonuses.get("shield_max_pct", 0.0))
    # Mirrors hp_flat_per_realm: lets shield-regen constitutions scale
    # their flat pool with cultivation so the regen has something to top
    # up at every tier without hand-tuning per-rarity numbers.
    shield_flat_per_realm        = int(bonuses.get("shield_flat_per_realm", 0))
    if shield_flat_per_realm:
        max_realm = max(char.body_realm, char.qi_realm, char.formation_realm)
        shield_max_flat += shield_flat_per_realm * max_realm
    hp_to_shield_pct             = float(bonuses.get("hp_to_shield_pct", 0.0))
    matk_from_shield_pct         = float(bonuses.get("matk_from_shield_pct", 0.0))
    atk_from_shield_pct          = float(bonuses.get("atk_from_shield_pct", 0.0))
    endure_threshold_pct         = float(bonuses.get("endure_threshold_pct", 0.0))
    endure_cooldown              = int(bonuses.get("endure_cooldown", 0))
    cleanse_heal_pct             = float(bonuses.get("cleanse_heal_pct", 0.0))
    cleanse_retaliate_dmg_pct    = float(bonuses.get("cleanse_retaliate_dmg_pct", 0.0))
    kill_buff_per_kill_pct       = float(bonuses.get("kill_buff_per_kill_pct", 0.0))
    kill_buff_cap                = int(bonuses.get("kill_buff_cap", 0))
    multi_strike_pct             = float(bonuses.get("multi_strike_pct", 0.0))
    multi_strike_dmg_pct         = float(bonuses.get("multi_strike_dmg_pct", 0.50))
    formation_echo_bonus         = max(0, int(bonuses.get("formation_echo_bonus", 0)))
    formation_skill_dmg_bonus    = max(0.0, float(bonuses.get("formation_skill_dmg_bonus", 0.0)))
    summon_limit_bonus           = max(0, int(bonuses.get("summon_limit_bonus", 0)))
    # Negative bonus shortens the recharge pause; clamp at 0 (instant regen).
    shield_recharge_delay_bonus  = int(bonuses.get("shield_recharge_delay_bonus", 0))
    damage_bonus_from_shield_pct = float(bonuses.get("damage_bonus_from_shield_pct", 0.0))
    thorn_pct                    = float(bonuses.get("thorn_pct", 0.0))
    thorn_from_shield            = bool(bonuses.get("thorn_from_shield", False))
    stun_on_hit_pct              = float(bonuses.get("stun_on_hit_pct", 0.0))
    # Per-constitution config flags (Kim killing-aura / crit-bleeder, Ám
    # shadow-mage, Quang silence-on-crit) — read from ``bonuses`` and merged
    # from ``equip_stats`` in one pass via the flag registry. ``equip_stats``
    # is already finalised here (the L6 equip-amp pre-pass is the only thing
    # that rebinds it, and it runs earlier), so merging now matches the old
    # two-stage read-then-equip-merge byte-for-byte. The result is splatted into
    # ``CombatStats(...)`` below. A new body adds ONE registry row, not four
    # edits. See ``_CONSTITUTION_FLAG_FIELDS``.
    _constitution_flags = _read_constitution_flags(bonuses, equip_stats)
    # Lôi-build fields
    shock_stack_cap_bonus        = int(bonuses.get("shock_stack_cap_bonus", 0))
    shock_per_stack_pct_bonus    = float(bonuses.get("shock_per_stack_pct_bonus", 0.0))
    shock_on_hit_pct             = float(bonuses.get("shock_on_hit_pct", 0.0))
    turn_steal_pct               = float(bonuses.get("turn_steal_pct", 0.0))
    # Poison-stack build fields (Mộc / Âm) — stack cap & per-stack pct
    # consolidated above in ``_stack_cap_bonus_by_kind`` / ``_per_stack_pct_bonus_by_kind``.
    # Phong-build fields
    mark_on_hit_pct              = float(bonuses.get("mark_on_hit_pct", 0.0))
    damage_bonus_from_evasion_pct= float(bonuses.get("damage_bonus_from_evasion_pct", 0.0))
    # ``crit_rating_vs_marked`` / ``crit_dmg_vs_marked`` and the Âm variants
    # are now consolidated into ``crit_amp_vs`` above.
    # Quang-build fields (``silence_on_crit_pct`` read via the flag registry)
    heal_reduce_on_hit_pct       = float(bonuses.get("heal_reduce_on_hit_pct", 0.0))
    blind_on_hit_pct             = float(bonuses.get("blind_on_hit_pct", 0.0))
    cleanse_on_turn_pct          = float(bonuses.get("cleanse_on_turn_pct", 0.0))
    barrier_on_cleanse           = bool(bonuses.get("barrier_on_cleanse", False))
    heal_can_crit                = bool(bonuses.get("heal_can_crit", False))
    # Âm-build fields
    soul_drain_on_hit_pct        = float(bonuses.get("soul_drain_on_hit_pct", 0.0))
    stat_steal_on_hit_pct        = float(bonuses.get("stat_steal_on_hit_pct", 0.0))
    # DoT-amplifier fields (cross-build). Per-kind amps consolidate into one
    # dict (mirrors element_pen / dmg_taken). Most authored data declares
    # ``dot_dmg_bonus_by_kind: {<kind>: X}`` (denested into flat keys at the
    # function entrance); equipment affix templates remain the one source
    # that still emits flat ``<kind>_dmg_bonus`` keys directly because
    # rolled-stat instances are stored that way in player inventories.
    dot_dmg_bonus                = float(bonuses.get("dot_dmg_bonus", 0.0))
    dot_dmg_bonus_by_kind: dict[str, float] = {}
    for _kind in ("burn", "bleed", "poison"):
        _v = float(bonuses.get(f"{_kind}_dmg_bonus", 0.0))
        if _v:
            dot_dmg_bonus_by_kind[_kind] = _v
    dot_scales_hp_pct            = bool(bonuses.get("dot_scales_hp_pct", False))
    solar_aura_pct               = float(bonuses.get("solar_aura_pct", 0.0))
    wither_aura_pct              = float(bonuses.get("wither_aura_pct", 0.0))
    phoenix_revive_pct           = float(bonuses.get("phoenix_revive_pct", 0.0))
    phoenix_revive_buff_pct      = float(bonuses.get("phoenix_revive_buff_pct", 0.0))
    stat_drain_aura_pct          = float(bonuses.get("stat_drain_aura_pct", 0.0))
    damage_defer_turns           = int(bonuses.get("damage_defer_turns", 0))
    damage_defer_pct             = float(bonuses.get("damage_defer_pct", 0.0))
    fortify_per_turn_pct         = float(bonuses.get("fortify_per_turn_pct", 0.0))
    fortify_stack_cap            = int(bonuses.get("fortify_stack_cap", 0))
    fortify_post_hit_dr_pct      = float(bonuses.get("fortify_post_hit_dr_pct", 0.0))
    sword_heart_per_stack_dr     = float(bonuses.get("sword_heart_per_stack_dr", 0.0))
    sword_summon_dmg_amp         = float(bonuses.get("sword_summon_dmg_amp", 0.0))
    loot_qty_bonus               = float(bonuses.get("loot_qty_bonus", 0.0))
    loot_luck_bonus              = float(bonuses.get("loot_luck_bonus", 0.0))
    bonus_base_dmg_per_self_hp_pct     = float(bonuses.get("bonus_base_dmg_per_self_hp_pct", 0.0))
    bonus_base_dmg_per_self_shield_pct = float(bonuses.get("bonus_base_dmg_per_self_shield_pct", 0.0))
    bonus_base_dmg_per_self_def_pct    = float(bonuses.get("bonus_base_dmg_per_self_def_pct", 0.0))
    def_applies_to_elemental_pct       = float(bonuses.get("def_applies_to_elemental_pct", 0.0))
    damage_taken_convert_pct: dict[str, float] = dict(bonuses.get("damage_taken_convert_pct", {}) or {})
    element_dmg_bonus: dict[str, float] = dict(bonuses.get("element_dmg_bonus", {}) or {})
    # Per-element max-res cap lifters. Two JSON shapes are accepted:
    #   "element_max_resist_bonus": {"hoa": 0.10, "kim": 0.05}     (preferred dict)
    #   "hoa_max_resist_bonus": 0.10, "kim_max_resist_bonus": 0.05  (flat keys)
    # Both flow into the same dict; combined at build time via additive merge.
    element_max_resist_bonus: dict[str, float] = dict(
        bonuses.get("element_max_resist_bonus", {}) or {}
    )
    for _elem in ALL_ELEMENTS:
        _ek = _elem.value
        _flat = float(bonuses.get(f"{_ek}_max_resist_bonus", 0.0))
        if _flat:
            element_max_resist_bonus[_ek] = (
                element_max_resist_bonus.get(_ek, 0.0) + _flat
            )
    # Formation-tunable nested dicts (clamped reads; not equipment-merged).
    _fdicts = _build_formation_dicts(bonuses)
    element_mp_cost_mult = _fdicts["element_mp_cost_mult"]
    cuu_khuc = _fdicts["cuu_khuc"]
    loi_kiep_an = _fdicts["loi_kiep_an"]
    thien_loi_tru_ta = _fdicts["thien_loi_tru_ta"]
    thai_cuc_am_duong_loi = _fdicts["thai_cuc_am_duong_loi"]
    cuu_long = _fdicts["cuu_long"]
    toa_hon_amp = _fdicts["toa_hon_amp"]
    dmg_taken = _fdicts["dmg_taken"]
    slow_on_hit_pct   = bonuses.get("slow_on_hit_pct", 0.0)
    paralysis_on_crit = bonuses.get("paralysis_on_crit", False)
    freeze_on_skill_chance = float(bonuses.get("freeze_on_skill_chance", 0.0))
    poison_immunity   = bonuses.get("poison_immunity", False)
    debuff_immune_pct = bonuses.get("debuff_immune_pct", 0.0)

    # ── Resistances ───────────────────────────────────────────────────────────
    resistances: dict[str, float] = {
        e.value: getattr(char.stats, RESISTANCE_KEYS[e]) for e in ALL_ELEMENTS
    }

    res_all = bonuses.get("res_all", 0.0)
    if res_all:
        for elem in resistances:
            resistances[elem] = min(MAX_ELEMENTAL_RES, max(0.0, resistances[elem] + res_all))

    # Per-element resistance pickups (constitutions and equipment can raise
    # individual element resistances via ``res_hoa``, ``res_kim``, etc.).
    for elem in resistances:
        bump = float(bonuses.get(f"res_{elem}", 0.0))
        if bump:
            resistances[elem] = min(MAX_ELEMENTAL_RES, max(0.0, resistances[elem] + bump))

    form_elem = form_bonuses.get("_formation_element")
    res_elem  = form_bonuses.get("res_element", 0.0)
    if form_elem and res_elem:
        resistances[form_elem] = min(MAX_ELEMENTAL_RES, max(0.0, resistances.get(form_elem, 0.0) + res_elem))

    # ── Equipment bonuses (applied last) ──────────────────────────────────────
    if equip_stats:
        # Unique items declare ``passive_bonus`` with nested grouped keys
        # (dot_dmg_bonus_by_kind, crit_amp_vs, …). Denest in place so the
        # flat ``.get("burn_dmg_bonus", 0.0)`` reads below pick them up.
        _denest_grouped_bonuses(equip_stats)
        atk             += int(equip_stats.get("atk", 0))
        matk            += int(equip_stats.get("matk", 0))
        def_stat        += int(equip_stats.get("def_stat", 0))
        crit_rating     += int(equip_stats.get("crit_rating", 0))
        crit_dmg_rating += int(equip_stats.get("crit_dmg_rating", 0))
        evasion_rating  += int(equip_stats.get("evasion_rating", 0))
        crit_res_rating += int(equip_stats.get("crit_res_rating", 0))
        accuracy_rating += int(equip_stats.get("accuracy_rating", 0))
        final_dmg_bonus  += equip_stats.get("final_dmg_bonus", 0.0)
        final_dmg_reduce  = min(
            MAX_FINAL_DMG_REDUCE,
            final_dmg_reduce + equip_stats.get("final_dmg_reduce", 0.0),
        )
        hp_regen_pct  += equip_stats.get("hp_regen_pct", 0.0)
        hp_regen_flat += int(equip_stats.get("hp_regen_flat", 0))
        mp_regen_pct  += equip_stats.get("mp_regen_pct", 0.0)
        mp_regen_flat += int(equip_stats.get("mp_regen_flat", 0))
        hp_max += int(equip_stats.get("hp_max", 0))
        mp_max += int(equip_stats.get("mp_max", 0))

        eq_res_all = equip_stats.get("res_all", 0.0)
        if eq_res_all:
            for elem in resistances:
                resistances[elem] = min(MAX_ELEMENTAL_RES, resistances[elem] + eq_res_all)
        for elem in resistances:
            eq_bump = float(equip_stats.get(f"res_{elem}", 0.0))
            if eq_bump:
                resistances[elem] = min(MAX_ELEMENTAL_RES, max(0.0, resistances[elem] + eq_bump))

        # Passive bonuses from unique items (on-hit procs, immunities, etc.)
        burn_on_hit_pct   += equip_stats.get("burn_on_hit_pct", 0.0)
        # Per-kind stack cap / per-stack pct equip merges — single loop over
        # the burn/bleed/poison kinds.
        for _kind in _STACK_DOT_KINDS:
            _stack_cap_bonus_by_kind[_kind] += int(
                equip_stats.get(f"{_kind}_stack_cap_bonus", 0)
            )
            _per_stack_pct_bonus_by_kind[_kind] += float(
                equip_stats.get(f"{_kind}_per_stack_pct_bonus", 0.0)
            )
        bonus_dmg_vs_burn += float(equip_stats.get("bonus_dmg_vs_burn", 0.0))
        dot_can_crit       = dot_can_crit or bool(equip_stats.get("dot_can_crit", False))
        # Per-element penetration dict from equip — additive merge.
        for _e, _v in (equip_stats.get("element_pen") or {}).items():
            element_pen[_e] = element_pen.get(_e, 0.0) + float(_v)
        # Kim-build fields (bleed stack cap / per-stack pct merged above
        # via ``_stack_cap_bonus_by_kind`` / ``_per_stack_pct_bonus_by_kind``)
        bleed_on_hit_pct        += float(equip_stats.get("bleed_on_hit_pct", 0.0))
        poison_on_hit_pct       += float(equip_stats.get("poison_on_hit_pct", 0.0))
        bleed_heal_reduce       += float(equip_stats.get("bleed_heal_reduce", 0.0))
        # Conditional crit amps — merge bleed/marked/drained from equip into
        # the consolidated crit_amp_vs dict (see CombatStats). Equipment
        # affixes keep flat keys; translated here.
        for _state in _CRIT_AMP_STATES:
            _r = int(equip_stats.get(f"crit_rating_vs_{_state}", 0))
            _d = int(equip_stats.get(f"crit_dmg_vs_{_state}", 0))
            if _r or _d:
                _existing = crit_amp_vs.setdefault(_state, {"rating": 0, "dmg": 0})
                _existing["rating"] = _existing.get("rating", 0) + _r
                _existing["dmg"]    = _existing.get("dmg", 0) + _d
        true_dmg_pct            += float(equip_stats.get("true_dmg_pct", 0.0))
        life_steal_pct           += float(equip_stats.get("life_steal_pct", 0.0))
        crit_dmg_rating_to_dmg_pct += float(equip_stats.get("crit_dmg_rating_to_dmg_pct", 0.0))
        # Moc-build fields
        dot_leech_pct            += float(equip_stats.get("dot_leech_pct", 0.0))
        damage_from_heal_pct     += float(equip_stats.get("damage_from_heal_pct", 0.0))
        damage_bonus_from_hp_pct += float(equip_stats.get("damage_bonus_from_hp_pct", 0.0))
        # Thủy-build fields
        reflect_pct              += float(equip_stats.get("reflect_pct", 0.0))
        reflect_applies_effects   = reflect_applies_effects or bool(equip_stats.get("reflect_applies_effects", False))
        damage_bonus_from_mp_pct += float(equip_stats.get("damage_bonus_from_mp_pct", 0.0))
        mp_leech_pct             += float(equip_stats.get("mp_leech_pct", 0.0))
        mana_stack_cap_bonus     += int(equip_stats.get("mana_stack_cap_bonus", 0))
        mana_stack_per_attack    += int(equip_stats.get("mana_stack_per_attack", 0))
        mana_stack_dmg_bonus     += float(equip_stats.get("mana_stack_dmg_bonus", 0.0))
        # Thổ-build fields
        shield_regen_pct             += float(equip_stats.get("shield_regen_pct", 0.0))
        shield_regen_flat            += int(equip_stats.get("shield_regen_flat", 0))
        shield_max_base              += int(equip_stats.get("shield_max_base", 0))
        shield_max_flat              += int(equip_stats.get("shield_max_flat", 0))
        shield_max_pct               += float(equip_stats.get("shield_max_pct", 0.0))
        eq_shield_per_realm = int(equip_stats.get("shield_flat_per_realm", 0))
        if eq_shield_per_realm:
            max_realm = max(char.body_realm, char.qi_realm, char.formation_realm)
            shield_max_flat += eq_shield_per_realm * max_realm
        hp_to_shield_pct             += float(equip_stats.get("hp_to_shield_pct", 0.0))
        matk_from_shield_pct         += float(equip_stats.get("matk_from_shield_pct", 0.0))
        atk_from_shield_pct          += float(equip_stats.get("atk_from_shield_pct", 0.0))
        endure_threshold_pct         = max(endure_threshold_pct, float(equip_stats.get("endure_threshold_pct", 0.0)))
        endure_cooldown              = max(endure_cooldown, int(equip_stats.get("endure_cooldown", 0)))
        cleanse_heal_pct             += float(equip_stats.get("cleanse_heal_pct", 0.0))
        cleanse_retaliate_dmg_pct    += float(equip_stats.get("cleanse_retaliate_dmg_pct", 0.0))
        kill_buff_per_kill_pct       = max(kill_buff_per_kill_pct, float(equip_stats.get("kill_buff_per_kill_pct", 0.0)))
        kill_buff_cap                = max(kill_buff_cap, int(equip_stats.get("kill_buff_cap", 0)))
        multi_strike_pct             += float(equip_stats.get("multi_strike_pct", 0.0))
        multi_strike_dmg_pct         = max(multi_strike_dmg_pct, float(equip_stats.get("multi_strike_dmg_pct", 0.0)))
        damage_bonus_from_shield_pct += float(equip_stats.get("damage_bonus_from_shield_pct", 0.0))
        thorn_pct                    += float(equip_stats.get("thorn_pct", 0.0))
        thorn_from_shield             = thorn_from_shield or bool(equip_stats.get("thorn_from_shield", False))
        stun_on_hit_pct              += float(equip_stats.get("stun_on_hit_pct", 0.0))
        # Per-constitution config flags (Kim / Ám / Quang silence) are merged
        # from ``equip_stats`` inside ``_read_constitution_flags`` above — no
        # per-flag equip line needed here. See ``_CONSTITUTION_FLAG_FIELDS``.
        # Lôi-build fields
        shock_stack_cap_bonus        += int(equip_stats.get("shock_stack_cap_bonus", 0))
        shock_per_stack_pct_bonus    += float(equip_stats.get("shock_per_stack_pct_bonus", 0.0))
        shock_on_hit_pct             += float(equip_stats.get("shock_on_hit_pct", 0.0))
        turn_steal_pct               += float(equip_stats.get("turn_steal_pct", 0.0))
        # Poison-stack build fields — stack cap / per-stack pct merged above
        # in the per-kind loop.
        # Phong-build fields
        mark_on_hit_pct              += float(equip_stats.get("mark_on_hit_pct", 0.0))
        damage_bonus_from_evasion_pct+= float(equip_stats.get("damage_bonus_from_evasion_pct", 0.0))
        # crit_rating_vs_marked / crit_dmg_vs_marked merged into crit_amp_vs above.
        # Quang-build fields (``silence_on_crit_pct`` merged via the flag registry)
        heal_reduce_on_hit_pct       += float(equip_stats.get("heal_reduce_on_hit_pct", 0.0))
        blind_on_hit_pct             += float(equip_stats.get("blind_on_hit_pct", 0.0))
        cleanse_on_turn_pct          += float(equip_stats.get("cleanse_on_turn_pct", 0.0))
        barrier_on_cleanse            = barrier_on_cleanse or bool(equip_stats.get("barrier_on_cleanse", False))
        heal_can_crit                 = heal_can_crit or bool(equip_stats.get("heal_can_crit", False))
        # Âm-build fields
        soul_drain_on_hit_pct        += float(equip_stats.get("soul_drain_on_hit_pct", 0.0))
        stat_steal_on_hit_pct        += float(equip_stats.get("stat_steal_on_hit_pct", 0.0))
        # crit_rating_vs_drained merged into crit_amp_vs above.
        # DoT-amplifier fields. Equipment affixes keep the flat ``<kind>_dmg_bonus``
        # key convention; merged into the per-kind dict here.
        dot_dmg_bonus                += float(equip_stats.get("dot_dmg_bonus", 0.0))
        for _kind in ("burn", "bleed", "poison"):
            _v = float(equip_stats.get(f"{_kind}_dmg_bonus", 0.0))
            if _v:
                dot_dmg_bonus_by_kind[_kind] = dot_dmg_bonus_by_kind.get(_kind, 0.0) + _v
        dot_scales_hp_pct             = dot_scales_hp_pct or bool(equip_stats.get("dot_scales_hp_pct", False))
        solar_aura_pct               += float(equip_stats.get("solar_aura_pct", 0.0))
        wither_aura_pct              += float(equip_stats.get("wither_aura_pct", 0.0))
        phoenix_revive_pct           = max(phoenix_revive_pct, float(equip_stats.get("phoenix_revive_pct", 0.0)))
        phoenix_revive_buff_pct      = max(phoenix_revive_buff_pct, float(equip_stats.get("phoenix_revive_buff_pct", 0.0)))
        stat_drain_aura_pct          = max(stat_drain_aura_pct, float(equip_stats.get("stat_drain_aura_pct", 0.0)))
        damage_defer_turns           = max(damage_defer_turns, int(equip_stats.get("damage_defer_turns", 0)))
        damage_defer_pct             = max(damage_defer_pct, float(equip_stats.get("damage_defer_pct", 0.0)))
        fortify_per_turn_pct         = max(fortify_per_turn_pct, float(equip_stats.get("fortify_per_turn_pct", 0.0)))
        fortify_stack_cap            = max(fortify_stack_cap, int(equip_stats.get("fortify_stack_cap", 0)))
        fortify_post_hit_dr_pct      = max(fortify_post_hit_dr_pct, float(equip_stats.get("fortify_post_hit_dr_pct", 0.0)))
        sword_heart_per_stack_dr    += float(equip_stats.get("sword_heart_per_stack_dr", 0.0))
        sword_summon_dmg_amp        += float(equip_stats.get("sword_summon_dmg_amp", 0.0))
        loot_qty_bonus              += float(equip_stats.get("loot_qty_bonus", 0.0))
        loot_luck_bonus             += float(equip_stats.get("loot_luck_bonus", 0.0))
        for elem, val in (equip_stats.get("damage_taken_convert_pct") or {}).items():
            damage_taken_convert_pct[elem] = damage_taken_convert_pct.get(elem, 0.0) + float(val)
        for elem, val in (equip_stats.get("element_dmg_bonus") or {}).items():
            element_dmg_bonus[elem] = element_dmg_bonus.get(elem, 0.0) + float(val)
        # ``element_max_resist_bonus`` from equipment — dict form merges per-
        # element; flat ``<elem>_max_resist_bonus`` keys roll up to the same dict.
        for elem, val in (equip_stats.get("element_max_resist_bonus") or {}).items():
            element_max_resist_bonus[elem] = element_max_resist_bonus.get(elem, 0.0) + float(val)
        for _elem in ALL_ELEMENTS:
            _ek = _elem.value
            _flat = float(equip_stats.get(f"{_ek}_max_resist_bonus", 0.0))
            if _flat:
                element_max_resist_bonus[_ek] = (
                    element_max_resist_bonus.get(_ek, 0.0) + _flat
                )
        # Affixes contribute via flat keys: ``element_dmg_all`` adds to every
        # element; ``element_dmg_<elem>`` adds to that one element only.
        # Both fold into the same ``element_dmg_bonus`` dict the combat
        # pipeline reads (see ``combat_hit.py``).
        eq_dmg_all = float(equip_stats.get("element_dmg_all", 0.0))
        if eq_dmg_all:
            for _e in ALL_ELEMENTS:
                element_dmg_bonus[_e.value] = element_dmg_bonus.get(_e.value, 0.0) + eq_dmg_all
        for _e in ALL_ELEMENTS:
            bump = float(equip_stats.get(f"element_dmg_{_e.value}", 0.0))
            if bump:
                element_dmg_bonus[_e.value] = element_dmg_bonus.get(_e.value, 0.0) + bump
        slow_on_hit_pct   += equip_stats.get("slow_on_hit_pct", 0.0)
        paralysis_on_crit  = paralysis_on_crit or bool(equip_stats.get("paralysis_on_crit", False))
        freeze_on_skill_chance += float(equip_stats.get("freeze_on_skill_chance", 0.0))
        poison_immunity    = poison_immunity   or bool(equip_stats.get("poison_immunity", False))
        debuff_immune_pct += equip_stats.get("debuff_immune_pct", 0.0)
        heal_pct          += equip_stats.get("heal_pct", 0.0)
        cooldown_reduce   += equip_stats.get("cooldown_reduce", 0.0)

    # Clamp cumulative CDR after every source has contributed. Constitution +
    # equipment + gem stacks can push past 200 % on end-game Thể Tu builds;
    # the cap keeps the per-cast accumulator from refunding faster than the
    # economy intends.
    cooldown_reduce = min(cooldown_reduce, COOLDOWN_REDUCE_CAP)

    mp_max, mp_reserved, reserve_pct = _apply_formation_mp_reserve(
        mp_max, form_bonuses, active_formations, formation_stages,
    )
    hp_max, shield_max_base, hp_to_shield_pct = _apply_hp_to_shield(
        hp_max, shield_max_base, hp_to_shield_pct,
    )
    # Lưu Ly Thuẫn Thân — the aegis body has no flesh: convert the ENTIRE
    # remaining hp_max into shield (× the conversion ratio) and lock hp_max to 1.
    # Runs after _apply_hp_to_shield so any partial conversion already folded in.
    # Combat-only — the persistent Character HP is untouched (this builds the
    # in-fight Combatant). With hp_max 1, take_damage forces all damage through
    # the shield (true-dmg / pierce / DoT can't bypass).
    if bool(bonuses.get("shield_only_body", False)):
        _aegis_ratio = float(bonuses.get("shield_from_hp_max_pct", 1.0))
        shield_max_base += int(hp_max * _aegis_ratio)
        hp_max = 1
    spd_final, def_stat, atk = _apply_pill_buffs(
        char, spd_final, def_stat, atk, element_dmg_bonus,
    )
    final_dmg_bonus, hp_regen_pct = _apply_toxicity_penalty(
        char, final_dmg_bonus, hp_regen_pct,
    )

    # ── Player soft-cap pass ──────────────────────────────────────────────
    # After all gear / constitution / Linh Căn contributions have folded in,
    # clamp each element's static resistance to the player's effective cap:
    #   cap = min(MAX_ELEMENTAL_RES, RES_SOFT_CAP + element_max_resist_bonus[elem])
    # Buff-driven additions at runtime are clamped by the same formula via
    # ``effective_res_cap``.
    from src.game.constants.balance import RES_SOFT_CAP
    for elem in resistances:
        soft = RES_SOFT_CAP + float(element_max_resist_bonus.get(elem, 0.0))
        cap = min(MAX_ELEMENTAL_RES, soft)
        if resistances[elem] > cap:
            resistances[elem] = cap

    # Stack-cap bonuses dict — per-kind DoT caps + shock routed to debuff keys.
    stack_cap_bonuses = _build_stack_cap_bonuses(
        _stack_cap_bonus_by_kind, shock_stack_cap_bonus,
    )

    return CombatStats(
        hp_max=hp_max,
        mp_max=mp_max,
        atk=atk,
        matk=matk,
        def_stat=def_stat,
        spd=spd_final,
        crit_rating=crit_rating,
        crit_dmg_rating=crit_dmg_rating,
        evasion_rating=evasion_rating,
        crit_res_rating=crit_res_rating,
        accuracy_rating=accuracy_rating,
        final_dmg_bonus=final_dmg_bonus,
        final_dmg_reduce=final_dmg_reduce,
        hp_regen_pct=hp_regen_pct,
        hp_regen_flat=hp_regen_flat,
        mp_regen_pct=mp_regen_pct,
        mp_regen_flat=mp_regen_flat,
        heal_pct=heal_pct,
        cooldown_reduce=cooldown_reduce,
        burn_on_hit_pct=burn_on_hit_pct,
        slow_on_hit_pct=slow_on_hit_pct,
        paralysis_on_crit=paralysis_on_crit,
        freeze_on_skill_chance=freeze_on_skill_chance,
        poison_immunity=poison_immunity,
        debuff_immune_pct=debuff_immune_pct,
        burn_per_stack_pct=_BURN_PCT_DEFAULT + _per_stack_pct_bonus_by_kind.get("burn", 0.0),
        bonus_dmg_vs_burn=bonus_dmg_vs_burn,
        dot_can_crit=dot_can_crit,
        bleed_per_stack_pct=_BLEED_PCT_DEFAULT + _per_stack_pct_bonus_by_kind.get("bleed", 0.0),
        bleed_on_hit_pct=bleed_on_hit_pct,
        poison_on_hit_pct=poison_on_hit_pct,
        bleed_heal_reduce=bleed_heal_reduce,
        crit_amp_vs=crit_amp_vs,
        true_dmg_pct=true_dmg_pct,
        life_steal_pct=life_steal_pct,
        crit_dmg_rating_to_dmg_pct=crit_dmg_rating_to_dmg_pct,
        dot_leech_pct=dot_leech_pct,
        damage_from_heal_pct=damage_from_heal_pct,
        damage_bonus_from_hp_pct=damage_bonus_from_hp_pct,
        reflect_pct=reflect_pct,
        reflect_applies_effects=reflect_applies_effects,
        damage_bonus_from_mp_pct=damage_bonus_from_mp_pct,
        mp_leech_pct=mp_leech_pct,
        mana_stack_cap=DEFAULT_MANA_STACK_CAP + mana_stack_cap_bonus,
        mana_stack_per_attack=mana_stack_per_attack,
        mana_stack_dmg_bonus=mana_stack_dmg_bonus,
        shield_regen_pct=shield_regen_pct,
        shield_regen_flat=shield_regen_flat,
        shield_max_base=max(0, shield_max_base),
        shield_max_flat=max(0, shield_max_flat),
        shield_max_pct=max(0.0, shield_max_pct),
        hp_to_shield_pct=hp_to_shield_pct,
        matk_from_shield_pct=max(0.0, matk_from_shield_pct),
        atk_from_shield_pct=max(0.0, atk_from_shield_pct),
        endure_threshold_pct=max(0.0, min(0.95, endure_threshold_pct)),
        endure_cooldown=max(0, endure_cooldown),
        cleanse_heal_pct=max(0.0, cleanse_heal_pct),
        cleanse_retaliate_dmg_pct=max(0.0, cleanse_retaliate_dmg_pct),
        kill_buff_per_kill_pct=max(0.0, kill_buff_per_kill_pct),
        kill_buff_cap=max(0, kill_buff_cap),
        multi_strike_pct=max(0.0, min(1.0, multi_strike_pct)),
        multi_strike_dmg_pct=max(0.0, min(2.0, multi_strike_dmg_pct)),
        formation_echo_bonus=formation_echo_bonus,
        formation_skill_dmg_bonus=formation_skill_dmg_bonus,
        summon_limit_bonus=summon_limit_bonus,
        shield_recharge_delay=max(0, DEFAULT_SHIELD_RECHARGE_DELAY + shield_recharge_delay_bonus),
        damage_bonus_from_shield_pct=damage_bonus_from_shield_pct,
        thorn_pct=thorn_pct,
        thorn_from_shield=thorn_from_shield,
        stun_on_hit_pct=stun_on_hit_pct,
        # Per-constitution config flags (Kim / Ám / Quang silence) — splatted
        # from the flag registry's single read+merge pass. See
        # ``_CONSTITUTION_FLAG_FIELDS`` / ``_read_constitution_flags``.
        **_constitution_flags,
        shock_per_stack_pct=_SHOCK_PCT_DEFAULT + shock_per_stack_pct_bonus,
        shock_on_hit_pct=shock_on_hit_pct,
        turn_steal_pct=turn_steal_pct,
        poison_per_stack_pct=_POISON_PCT_DEFAULT + _per_stack_pct_bonus_by_kind.get("poison", 0.0),
        mark_on_hit_pct=mark_on_hit_pct,
        damage_bonus_from_evasion_pct=damage_bonus_from_evasion_pct,
        heal_reduce_on_hit_pct=heal_reduce_on_hit_pct,
        blind_on_hit_pct=blind_on_hit_pct,
        cleanse_on_turn_pct=cleanse_on_turn_pct,
        barrier_on_cleanse=barrier_on_cleanse,
        heal_can_crit=heal_can_crit,
        soul_drain_on_hit_pct=soul_drain_on_hit_pct,
        stat_steal_on_hit_pct=stat_steal_on_hit_pct,
        dot_dmg_bonus=dot_dmg_bonus,
        dot_dmg_bonus_by_kind=dot_dmg_bonus_by_kind,
        dot_scales_hp_pct=dot_scales_hp_pct,
        solar_aura_pct=solar_aura_pct,
        wither_aura_pct=wither_aura_pct,
        phoenix_revive_pct=phoenix_revive_pct,
        phoenix_revive_buff_pct=phoenix_revive_buff_pct,
        stat_drain_aura_pct=stat_drain_aura_pct,
        damage_defer_turns=damage_defer_turns,
        damage_defer_pct=damage_defer_pct,
        fortify_per_turn_pct=fortify_per_turn_pct,
        fortify_stack_cap=fortify_stack_cap,
        fortify_post_hit_dr_pct=fortify_post_hit_dr_pct,
        sword_heart_per_stack_dr=sword_heart_per_stack_dr,
        sword_summon_dmg_amp=sword_summon_dmg_amp,
        loot_qty_bonus=loot_qty_bonus,
        loot_luck_bonus=loot_luck_bonus,
        bonus_base_dmg_per_self_hp_pct=bonus_base_dmg_per_self_hp_pct,
        bonus_base_dmg_per_self_shield_pct=bonus_base_dmg_per_self_shield_pct,
        bonus_base_dmg_per_self_def_pct=bonus_base_dmg_per_self_def_pct,
        def_applies_to_elemental_pct=def_applies_to_elemental_pct,
        damage_taken_convert_pct=damage_taken_convert_pct,
        element_dmg_bonus=element_dmg_bonus,
        element_mp_cost_mult=element_mp_cost_mult,
        cuu_khuc=cuu_khuc,
        loi_kiep_an=loi_kiep_an,
        thien_loi_tru_ta=thien_loi_tru_ta,
        thai_cuc_am_duong_loi=thai_cuc_am_duong_loi,
        cuu_long=cuu_long,
        toa_hon_amp=toa_hon_amp,
        dmg_taken=dmg_taken,
        element_pen=element_pen,
        mp_reserved=mp_reserved,
        mp_reserve_pct=reserve_pct,
        resistances=resistances,
        stack_cap_bonuses=stack_cap_bonuses,
        element_max_resist_bonus=element_max_resist_bonus,
    )
