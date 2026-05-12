"""Combat effects registry — 23 buffs + 19 debuffs/CC.

Provides:
  EFFECTS                 dict[key → EffectMeta]
  get_combat_modifiers    sum all active effect stat modifiers on a combatant
  get_periodic_damage     list of (effect_key, damage) for active DoTs
  check_cc_skip_turn      returns CC key if combatant should skip turn, else None
  check_prevents_skills   returns CC key if combatant cannot use skills, else None
  default_duration        default turn duration for an effect
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from src.game.constants.effects import EffectKey

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant


class EffectKind(StrEnum):
    BUFF = "buff"
    DEBUFF = "debuff"
    CC = "cc"


@dataclass(frozen=True)
class EffectMeta:
    key: str
    vi: str                          # Vietnamese name
    en: str                          # English name
    kind: EffectKind
    description_vi: str
    # Stat modifiers while effect is active (see stat key docs below)
    # Positive = bonus, negative = penalty. Applied to the combatant who holds the effect.
    # Stat keys: final_dmg_bonus, final_dmg_reduce, crit_rating, crit_dmg_rating,
    #            evasion_rating, crit_res_rating, spd_pct, hp_regen_pct, res_all
    stat_bonus: dict[str, float] = field(default_factory=dict)
    # Periodic damage per turn as fraction of holder's hp_max (DoT effects).
    # Stack-based DoTs (burn / bleed / poison) leave this at 0.0 and declare
    # ``stack_kind`` instead — the per-tick damage there comes from
    # ``combatant.<kind>_per_stack_pct × stacks`` (see engine/damage/dot.py).
    dot_pct: float = 0.0
    # Element of the DoT damage — holder's resistance to this element reduces DoT damage
    dot_element: str | None = None
    # Stack-based DoT kind ("burn" / "bleed" / "poison"). When set, the
    # tick math reads the combatant's per-stack pct and stack counter
    # instead of ``dot_pct``. Lets each stack DoT declare its kind in the
    # data once and removes the per-effect-key branches from dot.py.
    stack_kind: str | None = None
    # Whether this CC effect causes the holder to skip their turn (deterministic)
    skips_turn: bool = False
    # Whether this effect prevents skill usage (silence / interrupt)
    prevents_skills: bool = False
    # Aura-on-hit: ``(effect_key, chance)`` or ``(effect_key, chance, duration)``
    # — when the holder lands a hit, roll ``chance`` to apply ``effect_key``
    # to the target. Used for buffs whose flavor affects enemies (e.g.
    # BuffHanKhi slows anyone the holder strikes). The optional 3rd element
    # overrides the default turn duration for the spread effect — without it
    # the proc falls back to ``default_duration(effect_key)``. Applied in
    # ``combat._run_on_hit_procs``; respects hard-CC immunity.
    aura_on_hit: tuple[str, float] | tuple[str, float, int] | None = None
    # Instant-pulse magnitudes (one-shot when the effect is "applied" by a
    # support skill, NOT a per-turn DoT/regen). Used by HpRegen / MpRegen
    # and any future cleanse/recharge effects. A skill can override on a
    # per-cast basis via ``effect_overrides[<key>]``:
    #   "effect_overrides": { "HpRegen": { "instant_heal_pct": 0.25 } }
    # Routed through ``_apply_heal`` so bleed-heal-reduce, heal-can-crit,
    # and queued_heal_dmg conversions all behave consistently.
    instant_heal_pct: float = 0.0
    instant_mp_pct:   float = 0.0
    # On each DoT tick, ``dot_shield_drain_pct`` × tick damage also drains the
    # holder's energy shield (regular DoT bypasses shield). Used by curses that
    # punch through PoE-style shields. ``dot_mp_drain_pct`` × the holder's
    # ``mp_max`` is taken from MP per tick. Both default to zero — only set on
    # effects that need anti-shield / mana-burn flavor (e.g. Lục Hồn Chú).
    dot_shield_drain_pct: float = 0.0
    dot_mp_drain_pct:     float = 0.0
    # Caster-stat-driven DoT tick. When EITHER field is > 0, the standard
    # ``power × dot_pct × DOT_POWER_COEF`` formula is bypassed for this effect
    # and the tick instead reads the strongest applier's recorded stats:
    #     tick = caster.hp_max × dot_caster_hp_pct
    #          + caster.matk   × dot_caster_matk_scale
    # Lets a curse like Lục Hồn Chú scale off the caster's own HP pool + spell
    # power instead of the holder's stats. ``dot_pct`` becomes irrelevant when
    # these are set; resistance + ``dot_taken_bonus`` amps still apply on top.
    dot_caster_hp_pct:     float = 0.0
    dot_caster_matk_scale: float = 0.0
    # Per-tick damage cap for this DoT against world-boss / stat-mutation-immune
    # holders, expressed as a multiplier of the strongest applier's recorded
    # ``caster_matk``. 0.0 (the default) means uncapped — most DoTs don't need
    # one because their tick formula already self-limits via per-stack pct.
    # Set this when a DoT's stack scaling can otherwise nuke huge boss HP pools
    # (e.g. ``DebuffChanHoa: 6.0`` → tick clamped to 6×matk on world bosses).
    # Bosses without ``is_world_boss`` / ``immune_stat_mutation`` see no cap.
    boss_dot_cap_matk_scale: float = 0.0
    # Stack-DoT defaults — when non-zero, ``inflict_debuff`` seeds the holder's
    # ``<kind>_stack_cap`` / ``<kind>_per_stack_pct`` fields with these values
    # via max-merge on first apply. Designers tune from the EffectMeta entry
    # instead of editing Combatant field defaults; gear/build uplift on those
    # same Combatant fields still wins (max-merge keeps the larger value).
    # Only meaningful for effects with ``stack_kind`` set; ignored otherwise.
    # Leave at 0 to defer to whatever the Combatant carries (the legacy path
    # for burn/bleed/shock/poison whose defaults already flow through
    # balance.py constants + gear bonuses).
    stack_cap: int = 0
    per_stack_pct: float = 0.0
    # Default chance to apply this effect when listed in a skill's
    # ``effects`` array. ``apply_skill_effects`` and ``apply_support_skill``
    # use it as the fallback when the skill JSON omits an explicit
    # ``effect_chances[<key>]``. Most effects stay at 1.0 (apply on hit);
    # set < 1.0 here for inherently probabilistic effects (e.g. a CC that
    # is supposed to land 35 % of the time by design rather than per-skill).
    apply_chance: float = 1.0
    # Whether Quang Thanh Tẩy (and any future cleanse source) can remove
    # this effect. Replaces the old string-substring filter
    # (``"Debuff" in key or "CC" in key``) with an explicit, data-driven
    # flag — ``EffectNgungDong`` and any other oddly-named debuff now flow
    # through correctly.
    #
    # Tri-state default: ``None`` defers to the kind-based default
    # (DEBUFF / CC → True, BUFF → False). Pass ``True`` to make a buff
    # cleansable, or ``False`` to make a debuff *uncleansable* (e.g.
    # DebuffTanDiet — Tận Diệt is meant to be irreversible). Explicit
    # values are preserved verbatim.
    cleansable: bool | None = None
    # Whether ApplyBuffSteal can rip this buff off the holder and stamp it
    # on the attacker. Tri-state: ``None`` defers to the kind-based default
    # — buffs default to ``True`` (every buff is fair game by default),
    # debuffs / CC default to ``False`` (steal semantics for negatives don't
    # apply — use ``cleansable`` for those instead). Pass an explicit value
    # to override (e.g. ``False`` on a unique mode buff that shouldn't leave
    # its owner — Lục Dục Cộng Minh is locked to its caster, etc.).
    stealable: bool | None = None
    # When an effect expires naturally (duration tick → 0), automatically
    # apply this follow-up effect to the same holder. Tuple is
    # ``(effect_key, override_dict_or_None)``. Used for self-cycling passives
    # like Thiên Ma Giải Thể, where a Buff phase expires into a Vulnerable
    # phase, which expires back into the Buff — both metas point at each
    # other to form an infinite loop. Cleansed effects skip this chain
    # (handled in tick_effects, not in the cleanse path).
    on_expire_apply: tuple[str, dict | None] | None = None
    # Generic scaling rules — replace bespoke "placeholder key + manual
    # expansion in get_combat_modifiers" patterns with data-driven specs.
    # Each rule reads a value off the holder, optionally buckets/gates it,
    # and adds ``source × per_unit`` to an output stat. The per-unit
    # magnitude lives in ``stat_bonus`` under ``key``, so per-skill
    # ``effect_overrides[<effect>].stat_bonus[<key>]`` still wins via the
    # standard override flow.
    #
    # Rule shape (dict):
    #   key:         str   — either a stat_bonus placeholder name (popped from
    #                        ``result`` so per-skill overrides flow through),
    #                        or ``"field:<attr>"`` to read the per-unit magnitude
    #                        directly off the combatant (e.g. snapshotted
    #                        applier values like ``cuu_khuc_atk_reduce_active``).
    #   source:      str   — what to read from the holder (see _resolve_scaling_source)
    #   output:      str   — real stat key to write into (e.g. "hp_regen_pct")
    #   bucket:      float — optional, default 0. When > 0, floor(source/bucket) units.
    #   gate_source: str   — optional. When set, min/max check this value instead
    #                        of ``source`` — lets a flat ``source: "constant"``
    #                        rule gate on HP/MP without scaling by it.
    #   min:         float — optional gate, rule skipped when gate_value < min
    #   max:         float — optional gate, rule skipped when gate_value > max
    #   multiplier:  float — optional, default 1.0. Final output is
    #                        ``per_unit × units × multiplier``. Use -1.0 to
    #                        subtract from the output stat (debuff-style)
    #                        when the source magnitude is naturally positive.
    #
    # Sources:
    #   "constant"        — always 1.0 (flat-on-threshold pattern)
    #   "hp_pct"          — combatant.hp / hp_max (0.0 - 1.0)
    #   "hp_missing_pct"  — 1 - hp/hp_max
    #   "mp_pct"          — combatant.mp / mp_max
    #   "mp_missing_pct"  — 1 - mp/mp_max
    #   "stack:<name>"    — getattr(combatant, "<name>_stacks", 0) as float
    #
    # Convention: each rule's ``key`` must be unique across the whole
    # registry so the placeholder doesn't collide with another effect's
    # rule (the placeholder is popped from the aggregated stat dict after
    # the rule scales it, so a shared key would only fire once).
    scaling_rules: tuple[dict, ...] = ()
    # When set, any buff carrying this field auto-casts the named skill
    # back at the attacker every time the HOLDER (defender) successfully
    # evades. Skipped if the holder doesn't have ``proc_on_holder_evade_cast``
    # in their ``skill_keys`` (equipping gate). MP + cooldown of the named
    # skill still apply. Engine hook lives in
    # ``_fire_self_evade_procs`` — generalizes the old hardcoded Lưu Quang
    # Huyễn Ảnh → Cực Quang Trảm reactive into a reusable mechanism that
    # any future buff can opt into via this single field.
    proc_on_holder_evade_cast: str | None = None
    # Display emoji
    emoji: str = "✨"

    def __post_init__(self) -> None:
        # Frozen dataclass — bypass the freeze for the kind-based default.
        # Only resolves the ``None`` sentinel; explicit True / False set
        # by the caller passes through untouched.
        if self.cleansable is None:
            object.__setattr__(self, "cleansable", self.kind != EffectKind.BUFF)
        # Inverse default for stealable: BUFF → True (buffs are stealable by
        # default), DEBUFF / CC → False (don't apply to non-buffs). Explicit
        # values pass through untouched, same pattern as cleansable.
        if self.stealable is None:
            object.__setattr__(self, "stealable", self.kind == EffectKind.BUFF)


# ── Buff definitions (23) ─────────────────────────────────────────────────────

_BUFFS: list[EffectMeta] = [
    EffectMeta(
        key="BuffKiemKhi",
        vi="Kiếm Khí", en="Sword Qi",
        kind=EffectKind.BUFF,
        description_vi="Tụ kiếm khí, tăng sát thương và tỉ lệ bạo kích.",
        stat_bonus={"final_dmg_bonus": 0.15, "crit_rating": 150},
        emoji="⚔️",
    ),
    EffectMeta(
        key="BuffKiemY",
        vi="Kiếm Ý", en="Sword Intent",
        kind=EffectKind.BUFF,
        description_vi="Kiếm ý sung mãn, tăng mạnh sát thương.",
        stat_bonus={"final_dmg_bonus": 0.25},
        emoji="🗡️",
    ),
    EffectMeta(
        key="BuffVoNgaKiemTam",
        vi="Vô Ngã Kiếm Tâm", en="Selfless Sword Heart",
        kind=EffectKind.BUFF,
        description_vi="Đạt cảnh giới vô ngã, tăng tối đa sát thương kiếm.",
        stat_bonus={"final_dmg_bonus": 0.40},
        emoji="✨",
    ),
    EffectMeta(
        key="BuffNhietTinh",
        vi="Nhiệt Tình", en="Blazing Passion",
        kind=EffectKind.BUFF,
        description_vi="Chiến ý bùng cháy, tăng sát thương và sát thương bạo kích.",
        stat_bonus={"final_dmg_bonus": 0.20, "crit_dmg_rating": 200},
        emoji="🔥",
    ),
    EffectMeta(
        key="BuffHoaThan",
        vi="Hỏa Thần Giáng Lâm", en="Fire God Descent",
        kind=EffectKind.BUFF,
        description_vi="Hỏa thần tạm hạ phàm, tăng sát thương kỹ năng Hỏa và đánh trúng có xác suất gây Thiêu Đốt 4 lượt.",
        stat_bonus={"dmg_bonus_hoa": 0.20},
        aura_on_hit=("DebuffThieuDot", 0.35, 4),
        emoji="🔥",
    ),
    EffectMeta(
        key="BuffPhuongHoangChanHoa",
        vi="Phượng Hoàng Chân Hỏa", en="Phoenix True Fire",
        kind=EffectKind.BUFF,
        description_vi=(
            "Chân hỏa Phượng Hoàng quấn quanh thân — passive: mỗi 5% HP đã "
            "mất tăng 0.2% hồi sinh lực. Khi địch đánh trúng, in 1 tầng "
            "**Phượng Hỏa** lên địch (tối đa 3 tầng): mỗi tầng đốt 2% HP "
            "mỗi lượt và **giảm 10% hồi máu nhận vào** từ mọi nguồn."
        ),
        # Per-bucket missing-HP regen scaling expressed as a generic rule.
        # The reflective ``DebuffPhuongHoa`` proc lives in
        # ``procs.apply_reactive_damage`` because ``aura_on_hit`` fires on
        # the holder's outbound hits (wrong direction) — we need an inbound
        # hook on hit-taken.
        stat_bonus={"phuong_hoang_per_5pct_hp_lost_regen": 0.002},
        scaling_rules=(
            {
                "key": "phuong_hoang_per_5pct_hp_lost_regen",
                "source": "hp_missing_pct",
                "bucket": 0.05,
                "output": "hp_regen_pct",
            },
        ),
        emoji="🦅",
    ),
    EffectMeta(
        key="BuffLuuLyTinhHoa",
        vi="Lưu Ly Tịnh Hỏa", en="Lapis Pure Fire",
        kind=EffectKind.BUFF,
        description_vi=(
            "Lưu ly tịnh hỏa hộ thân — mỗi lượt, với **mỗi loại Hỏa DoT** "
            "đang cháy trên địch, có **30% cơ hội Thanh Tẩy 1 trạng thái "
            "xấu** trên thân. Mỗi lần Thanh Tẩy thành công cộng dồn "
            "**+200 Kháng Bạo** (tối đa 3 tầng, hết khi buff tan)."
        ),
        # ``luu_ly_cleanse_chance`` is read by the periodic hook in
        # ``CombatSession`` (tunable via per-skill effect_overrides). The
        # per-cleanse +crit_res_rating is wired through the generic scaling
        # rules — each successful cleanse bumps ``luu_ly_tinh_hoa_stacks``
        # and the rule converts stacks × per_unit → crit_res_rating.
        # The stack cap rides on the standard ``EffectMeta.stack_cap`` field
        # — read by the periodic hook via ``effective_stack_cap`` so a
        # per-skill ``effect_overrides[...].stack_cap`` still wins. No
        # dedicated Combatant cap field needed because there's no gear /
        # build hook that scales this counter.
        stack_cap=3,
        stat_bonus={
            "luu_ly_cleanse_chance": 0.30,
            "luu_ly_per_stack_crit_res": 200,
        },
        scaling_rules=(
            {
                "key": "luu_ly_per_stack_crit_res",
                "source": "stack:luu_ly_tinh_hoa",
                "output": "crit_res_rating",
            },
        ),
        stealable=False,
        cleansable=False,
        emoji="🔮",
    ),
    EffectMeta(
        key="BuffCuuDuong",
        vi="Cửu Dương Hộ Thể", en="Nine-Sun Body Guard",
        kind=EffectKind.BUFF,
        description_vi=(
            "Cửu dương chân khí cuộn quanh thân — **kháng 90% Đóng Băng**; "
            "**+60% Kháng Hỏa** kèm **+10% Cap Kháng Hỏa** (tối đa 85%); "
            "**40% sát thương phải nhận** được chuyển hóa thành Hỏa trước "
            "khi đè lên thân (mitigation qua res Hỏa đã được tăng cường)."
        ),
        # Four layers in one stat_bonus dict, all already wired:
        #   * ``res_hoa`` folds into the damage-taken element-mitigation pipe
        #     (combatant.py reads ``active_mods.get(f"res_{elem}")``).
        #   * ``hoa_max_resist_bonus`` lifts the player's hoa cap from 0.75
        #     to 0.85 for the buff's duration — read by ``effective_res_cap``
        #     so the +60% res actually has headroom past the default soft cap.
        #   * ``damage_convert_hoa`` is consumed by the element-conversion
        #     step in ``Combatant.take_damage`` — 40% of incoming damage is
        #     reclassified as Hỏa and mitigated by the holder's (boosted) res.
        #   * ``effect_resist:DebuffDongBang`` is a generic per-effect resist
        #     gate read by ``inflict_debuff`` — 90% roll to shrug off freeze.
        stat_bonus={
            "res_hoa": 0.60,
            "hoa_max_resist_bonus": 0.10,
            "damage_convert_hoa": 0.40,
            "effect_resist:DebuffDongBang": 0.90,
        },
        stealable=False,
        cleansable=False,
        emoji="🌞",
    ),
    EffectMeta(
        key="BuffPhuongHoangTrienSi",
        vi="Phượng Hoàng Triển Sí", en="Phoenix Wing Spread",
        kind=EffectKind.BUFF,
        description_vi=(
            "Phượng hoàng triển sí — chấn cánh hỏa vũ, **+500 Bạo Kích "
            "Sát Thương** và **+20% Né Tránh** (theo % của Né Tránh nền). "
            "Khi né được đòn, tự động phát động **Phượng Hoàng Chân Hỏa** "
            "(nếu đang trang bị) để hộ thân."
        ),
        # ``evasion_rating_pct`` is a new multiplier on the holder's base
        # ``evasion_rating`` — read in ``build_defense_stats`` so it stacks
        # with flat ``evasion_rating`` mods naturally. The auto-cast trigger
        # of Phượng Hoàng Chân Hỏa rides on the skill's
        # ``proc_on_self_evade_cast`` config, not on this buff — so the
        # passive proc fires even if the buff has expired.
        stat_bonus={"crit_dmg_rating": 500, "evasion_rating_pct": 0.20},
        stealable=False,
        cleansable=False,
        emoji="🦅",
    ),
    EffectMeta(
        key="BuffLuuTinhCanNguyet",
        vi="Lưu Tinh Cản Nguyệt", en="Shooting Star Blocking Moon",
        kind=EffectKind.BUFF,
        description_vi=(
            "Lưu tinh thân pháp — **+20% tốc độ** vĩnh viễn. Khi địch mang "
            "Hỏa DoT: mỗi loại Hỏa DoT cộng thêm **+150 Né Tránh** và "
            "**+5% Tốc Độ** cho người niệm (cập nhật mỗi lượt). Aura — "
            "không thể giải trừ / cướp."
        ),
        # Base passive: +20% spd. The per-fire-DoT scaling for spd_pct +
        # evasion_rating is updated each turn by
        # ``CombatSession._refresh_luu_tinh_can_nguyet`` based on the
        # opponent's live ``count_elemental_dots("hoa")``. The hook writes
        # the computed totals into ``effect_overrides[...].stat_bonus``
        # (``spd_pct`` and ``evasion_rating``), which ``get_combat_modifiers``
        # reads through the standard per-instance-override path.
        stat_bonus={"spd_pct": 0.20},
        stealable=False,
        cleansable=False,
        emoji="🌠",
    ),
    EffectMeta(
        key="BuffBatDietHoaChung",
        vi="Bất Diệt Hỏa Chủng", en="Immortal Fire Seed",
        kind=EffectKind.BUFF,
        description_vi=(
            "Hỏa chủng bất diệt cuộn quanh chân khí — **+70% giảm sát thương "
            "phải nhận** trong lúc thân hóa hỏa chủng, đồng thời **không thể "
            "hành động 1 lượt**. Khi buff tan, bùng nổ trả đòn: gây sát "
            "thương Hỏa = 15% HP tối đa và choáng địch 1 lượt."
        ),
        # ``skips_turn`` makes the holder skip on the next turn (the
        # "can't action" phase). Duration 2 covers the residual current
        # turn + the next full turn. ``cleansable=False`` so the panic
        # button can't be Thanh Tẩy'd off mid-window.
        stat_bonus={"final_dmg_reduce": 0.70},
        skips_turn=True,
        cleansable=False,
        stealable=False,
        emoji="🔥",
    ),
    EffectMeta(
        key="BuffLietDiem",
        vi="Liệt Diệm Hộ Thể", en="Blazing Flame Guard",
        kind=EffectKind.BUFF,
        description_vi="Lửa thiêu đốt bảo vệ cơ thể, giảm sát thương nhận vào.",
        stat_bonus={"final_dmg_reduce": 0.10},
        emoji="🛡️",
    ),
    EffectMeta(
        key="BuffBangGiap",
        vi="Băng Giáp", en="Ice Armor",
        kind=EffectKind.BUFF,
        description_vi="Băng giáp bao phủ, giảm đáng kể sát thương nhận vào.",
        stat_bonus={"final_dmg_reduce": 0.20},
        emoji="🧊",
    ),
    EffectMeta(
        key="BuffThuyKinh",
        vi="Thủy Kính Thân", en="Water Mirror Body",
        kind=EffectKind.BUFF,
        description_vi="Thân như gương nước, giảm đáng kể sát thương nhận vào.",
        stat_bonus={"final_dmg_reduce": 0.15},
        emoji="💧",
    ),
    EffectMeta(
        key="BuffHanKhi",
        vi="Hàn Khí Tỏa Thân", en="Cold Aura",
        kind=EffectKind.BUFF,
        description_vi="Hàn khí tỏa ra, giảm sát thương nhận và làm chậm 4 lượt mọi kẻ địch bị đánh trúng.",
        stat_bonus={"final_dmg_reduce": 0.10},
        # 4-turn slow — non-heavy CC bracket (2 low / 4 high). The slow
        # doesn't shut the target down (they still act, just slower) so the
        # longer high-realm cap is fine.
        aura_on_hit=("DebuffLamCham", 1.0, 4),
        emoji="❄️",
    ),
    EffectMeta(
        key="BuffLoiThan",
        vi="Lôi Thần Giáng", en="Thunder God Strike",
        kind=EffectKind.BUFF,
        description_vi="Lôi thần gia hộ, tăng tỉ lệ bạo kích và đánh trúng có xác suất gây Tê Liệt 2 lượt.",
        stat_bonus={"crit_rating": 100},
        aura_on_hit=("DebuffTeLiet", 0.20, 2),
        emoji="⚡",
    ),
    EffectMeta(
        key="BuffTocLoi",
        vi="Tốc Lôi", en="Speed Lightning",
        kind=EffectKind.BUFF,
        description_vi="Tốc độ như sét đánh, tăng mạnh tốc độ hành động.",
        stat_bonus={"spd_pct": 0.50},
        emoji="⚡",
    ),
    EffectMeta(
        key="BuffNguPhong",
        vi="Ngự Phong", en="Wind Mastery",
        kind=EffectKind.BUFF,
        description_vi="Điều khiển gió, tăng cao khả năng né tránh.",
        stat_bonus={"evasion_rating": 200},
        emoji="🌬️",
    ),
    EffectMeta(
        key="BuffPhongVu",
        vi="Phong Vũ Tương Hòa", en="Wind Rain Harmony",
        kind=EffectKind.BUFF,
        description_vi="Phong vũ hòa quyện, tăng cả né tránh lẫn bạo kích.",
        stat_bonus={"evasion_rating": 100, "crit_rating": 100},
        emoji="🌧️",
    ),
    EffectMeta(
        key="BuffSinhCo",
        vi="Sinh Cơ Sung Mãn", en="Vitality Overflow",
        kind=EffectKind.BUFF,
        description_vi="Sinh cơ dồi dào, hồi phục HP mỗi lượt.",
        stat_bonus={"hp_regen_pct": 0.05},
        emoji="💚",
    ),
    EffectMeta(
        key="BuffCanCo",
        vi="Căn Cơ Bất Động", en="Immovable Foundation",
        kind=EffectKind.BUFF,
        description_vi="Căn cơ vững chắc như núi, giảm sát thương nhận vào.",
        stat_bonus={"final_dmg_reduce": 0.15},
        emoji="🪨",
    ),
    EffectMeta(
        key="BuffKimCuong",
        vi="Kim Cương Thể", en="Diamond Body",
        kind=EffectKind.BUFF,
        description_vi="Thân như kim cương, giảm mạnh sát thương nhận vào.",
        stat_bonus={"final_dmg_reduce": 0.20},
        emoji="💎",
    ),
    EffectMeta(
        key="BuffHoangKim",
        vi="Hoàng Kim Hộ", en="Golden Guard",
        kind=EffectKind.BUFF,
        description_vi="Khiên vàng bảo hộ, giảm sát thương và tăng kháng cự toàn nguyên tố.",
        stat_bonus={"final_dmg_reduce": 0.15, "res_all": 0.10},
        emoji="🟡",
    ),
    EffectMeta(
        key="BuffDaiDia",
        vi="Đại Địa Thần Hộ", en="Great Earth Divine Guard",
        kind=EffectKind.BUFF,
        description_vi="Đại địa thần hộ trì, giảm tối đa sát thương nhận vào.",
        stat_bonus={"final_dmg_reduce": 0.25},
        emoji="🌍",
    ),
    EffectMeta(
        key="BuffTrongTo",
        vi="Trọng Thổ", en="Heavy Earth",
        kind=EffectKind.BUFF,
        description_vi="Thổ khí nặng trịch, tăng phòng thủ và kháng cự.",
        stat_bonus={"final_dmg_reduce": 0.10, "res_all": 0.05},
        emoji="🪨",
    ),
    EffectMeta(
        key="BuffBatTu",
        vi="Bất Tử", en="Immortality",
        kind=EffectKind.BUFF,
        description_vi="Nhận lấy bất tử nhất thời, ngăn cái chết một lần.",
        stat_bonus={},
        emoji="💫",
    ),
    EffectMeta(
        key="BuffTangToc",
        vi="Tăng Tốc", en="Haste",
        kind=EffectKind.BUFF,
        description_vi="Tốc độ hành động tăng mạnh.",
        stat_bonus={"spd_pct": 0.30},
        emoji="⚡",
    ),
    EffectMeta(
        key="BuffHoPhap",
        vi="Hộ Pháp", en="Dharma Protection",
        kind=EffectKind.BUFF,
        description_vi="Hộ pháp bảo vệ toàn diện, giảm sát thương và tăng kháng bạo kích.",
        stat_bonus={"final_dmg_reduce": 0.25, "crit_res_rating": 100},
        emoji="🔮",
    ),
    EffectMeta(
        key="BuffHuKhong",
        vi="Hư Không Thân", en="Void Body",
        kind=EffectKind.BUFF,
        description_vi="Thân nhập hư không, né tránh cực cao.",
        stat_bonus={"evasion_rating": 250},
        emoji="👻",
    ),
    EffectMeta(
        key="BuffHuVo",
        vi="Hư Vô", en="Void Annulment",
        kind=EffectKind.BUFF,
        description_vi="Hư vô thân ảnh — Né Tránh tăng vọt sau khi phản kích. Magnitude is supplied per-cast via ``effect_overrides`` (e.g. evasion_rating: 2000).",
        emoji="🌌",
        stealable=False,
        cleansable=False,
    ),
    EffectMeta(
        key="AuraHuyDiet",
        vi="Hủy Diệt", en="Annihilation Aura",
        kind=EffectKind.BUFF,
        description_vi="Hào quang Hủy Diệt — đối xứng cộng sát thương đánh ra và sát thương phải nhận. Magnitude is supplied per-cast via ``effect_overrides`` (``final_dmg_bonus`` for outgoing, ``final_dmg_taken_bonus`` for incoming).",
        emoji="☄️",
        stealable=False,
        cleansable=False,
    ),
    EffectMeta(
        key="BuffDoanTuyet",
        vi="Đoạn Tuyệt", en="Severance",
        kind=EffectKind.BUFF,
        description_vi="Tâm cảnh đoạn tuyệt thất tình — sát thương tăng theo số trạng thái bất lợi đang đè trên đối thủ. Magnitude per-debuff supplied via ``effect_overrides`` key ``dmg_per_debuff_pct`` (read by ``build_attack_stats``).",
        emoji="🌒",
        stealable=False,
        cleansable=False,
    ),
    EffectMeta(
        key="BuffThienMa",
        vi="Thiên Ma", en="Heavenly Demon Mode",
        kind=EffectKind.BUFF,
        description_vi="Hóa thân Thiên Ma — tốc độ, sát thương Âm và tốc hồi chiêu đều bùng nổ. Khi tan biến tự động chuyển sang Thiên Ma Hậu Di Chứng (yếu thân).",
        stat_bonus={"spd_pct": 0.50, "dmg_bonus_am": 0.50, "cooldown_reduce": 0.50},
        on_expire_apply=("DebuffThienMaPost", {"duration": 2}),
        emoji="👹",
        stealable=False,
        cleansable=False,
    ),
    # ── Lục Dục (Six-Desires) cycle buffs ────────────────────────────────────
    # Each desire stacks one stat. Six accumulate over 3 turns (2 per turn);
    # at full stack the BuffLucDucCongMinh amp adds +20% of each desire's
    # contribution. The Lục Dục Thiên Ma Vũ passive then expires the whole
    # set for 2 quiet turns before restarting the cycle. Driven by the cycle
    # hook in CombatSession._tick_luc_duc_thien_ma_vu, not by aura plumbing.
    EffectMeta(
        key="BuffLucDucSac",
        vi="Sắc Dục", en="Desire of Sight",
        kind=EffectKind.BUFF,
        description_vi="Sắc — mắt thấy đạo, tăng tỉ lệ bạo kích.",
        stat_bonus={"crit_rating": 500},
        emoji="👁️",
    ),
    EffectMeta(
        key="BuffLucDucThanh",
        vi="Thanh Dục", en="Desire of Sound",
        kind=EffectKind.BUFF,
        description_vi="Thanh — tai nghe gió động, tăng tốc độ.",
        stat_bonus={"spd_pct": 0.3},
        emoji="👂",
    ),
    EffectMeta(
        key="BuffLucDucHuong",
        vi="Hương Dục", en="Desire of Smell",
        kind=EffectKind.BUFF,
        description_vi="Hương — mũi đánh hơi sát khí, tăng né tránh.",
        stat_bonus={"evasion_rating": 500},
        emoji="👃",
    ),
    EffectMeta(
        key="BuffLucDucVi",
        vi="Vị Dục", en="Desire of Taste",
        kind=EffectKind.BUFF,
        description_vi="Vị — lưỡi nếm tinh huyết, hồi sinh lực mỗi lượt.",
        stat_bonus={"hp_regen_pct": 0.1},
        emoji="👅",
    ),
    EffectMeta(
        key="BuffLucDucXuc",
        vi="Xúc Dục", en="Desire of Touch",
        kind=EffectKind.BUFF,
        description_vi="Xúc — thân tiếp đại đạo, tăng sát thương cuối.",
        stat_bonus={"final_dmg_bonus": 0.1},
        emoji="✋",
    ),
    EffectMeta(
        key="BuffLucDucPhap",
        vi="Pháp Dục", en="Desire of Thought",
        kind=EffectKind.BUFF,
        description_vi="Pháp — ý nắm Âm Khí, tăng sát thương Âm.",
        stat_bonus={"dmg_bonus_am": 0.20},
        emoji="🧠",
    ),
    EffectMeta(
        key="BuffLucDucCongMinh",
        vi="Lục Dục Cộng Minh", en="Six-Desires Resonance",
        kind=EffectKind.BUFF,
        description_vi="Lục dục cộng hưởng — toàn bộ Lục Dục đang đè trên thân được khuếch đại 20%.",
        stat_bonus={
            "crit_rating": 40, "spd_pct": 0.03, "evasion_rating": 40,
            "hp_regen_pct": 0.01, "final_dmg_bonus": 0.03, "dmg_bonus_am": 0.04,
        },
        stealable=False,
        cleansable=False,
        emoji="🌌",
    ),
    EffectMeta(
        key="BuffVoTuongThienMa",
        vi="Vô Tướng Thiên Ma", en="Formless Heavenly Demon",
        kind=EffectKind.BUFF,
        description_vi=(
            "Vô tướng vô hình — 40% sát thương phải nhận được chuyển hóa "
            "thành Âm trước khi đè lên thân, **+50% Kháng Âm** kèm "
            "**+15% Cap Kháng Âm** (giới hạn nâng lên 90%) để đảm bảo "
            "buff có đủ room ngấm vào res sàn."
        ),
        # ``am_max_resist_bonus`` lifts the player's am cap from 0.75 to
        # 0.90 for the buff's duration — read by ``effective_res_cap`` so
        # the +50% res actually has room past the default soft cap.
        stat_bonus={
            "damage_convert_am": 0.40,
            "res_am": 0.50,
            "am_max_resist_bonus": 0.15,
        },
        stealable=False,
        cleansable=False,
        emoji="🌑",
    ),
    EffectMeta(
        key="BuffMaKhiHoThe",
        vi="Ma Khí Hộ Thể", en="Demon-Aura Bodyguard",
        kind=EffectKind.BUFF,
        description_vi="Ma khí cuộn quanh thân — mỗi đòn Âm đánh ra chuyển hóa một phần sát thương thành khiên cho người niệm. Magnitude per cast read from ``stat_bonus.am_hit_to_shield_pct`` (folded into shield gain in cast_skill's on-hit hook).",
        stat_bonus={"am_hit_to_shield_pct": 0.30},
        stealable=False,
        cleansable=False,
        emoji="🩻",
    ),
    EffectMeta(
        key="BuffChanMaChiTam",
        vi="Chân Ma Chi Tâm", en="True Demon Heart",
        kind=EffectKind.BUFF,
        description_vi="Tâm hóa Chân Ma — kháng debuff +30% và mọi đòn của người niệm có thêm 10% xác suất gây trạng thái bất lợi.",
        stat_bonus={"debuff_immune_pct": 0.30, "debuff_apply_bonus": 0.10},
        emoji="🪬",
        stealable=False,
        cleansable=False,
    ),
    EffectMeta(
        key="BuffQuyAnhMeTung",
        vi="Quỷ Ảnh Mê Tung", en="Demon-Shadow Phantom Step",
        kind=EffectKind.BUFF,
        description_vi="Mỗi lần né thành công cộng dồn 1 tầng (tối đa 3): +10% Tốc và Né Tránh tăng theo tầng. Per-stack values driven by ``scaling_rules`` keyed on ``combatant.quy_anh_stacks``.",
        stat_bonus={
            "quy_anh_per_stack_evasion": 200,
            "quy_anh_per_stack_spd_pct": 0.10,
            "quy_anh_max_stacks": 3,
        },
        scaling_rules=(
            {"key": "quy_anh_per_stack_evasion", "source": "stack:quy_anh", "output": "evasion_rating"},
            {"key": "quy_anh_per_stack_spd_pct", "source": "stack:quy_anh", "output": "spd_pct"},
        ),
        emoji="👤",
        stealable=False,
        cleansable=False,
    ),
    EffectMeta(
        key="BuffMaLongXuatUyen",
        vi="Ma Long Xuất Uyên", en="Demon-Dragon Emerges",
        kind=EffectKind.BUFF,
        description_vi="Mỗi lần né thành công, hoá Ma Long phản kích — gây sát thương Âm nhỏ + đính Cắt Đứt Linh Khí lên đối thủ. Counter-strike config carried in stat_bonus; popped by ``get_combat_modifiers`` and consumed by the on-evade hook in ``cast_skill``.",
        stat_bonus={
            "ma_long_counter_base_dmg": 600,
            "ma_long_counter_matk_pct": 0.40,
            "ma_long_counter_debuff_chance": 0.60,
        },
        emoji="🐉",
        stealable=False,
        cleansable=False,
    ),
]

# ── Debuff / CC definitions (19) ─────────────────────────────────────────────

_DEBUFFS_CC: list[EffectMeta] = [
    EffectMeta(
        key="DebuffThieuDot",
        vi="Thiêu Đốt", en="Burning",
        kind=EffectKind.DEBUFF,
        description_vi="Lửa đốt cháy cơ thể, gây sát thương mỗi lượt.",
        # Stack-based: tick = burn_stacks × burn_per_stack_pct × max(atk, matk) × DOT_POWER_COEF.
        # ``dot_pct`` is intentionally 0 — the runtime reads the per-stack value off the combatant.
        stack_kind="burn",
        dot_element="hoa",
        stack_cap=5,
        per_stack_pct=0.008,
        emoji="🔥",
    ),
    EffectMeta(
        key="DebuffNghiepHoaHongLien",
        vi="Nghiệp Hỏa Hồng Liên", en="Red Lotus Karma Fire",
        kind=EffectKind.DEBUFF,
        description_vi=(
            "Hồng Liên Nghiệp Hỏa đóng dấu nghiệp lực — **không thể giải "
            "trừ**. Mỗi khi địch nhân nhận thêm bất kỳ debuff/CC/DoT nào, "
            "in 1 tầng **Nghiệp Hỏa** (3% HP tối đa/lượt, hệ Hỏa) và có "
            "30% cơ hội đốt cháy 1 trạng thái tốt ngẫu nhiên."
        ),
        # Marker only — no stat_bonus, no DoT of its own. The reactive
        # logic lives in casting._fire_nghiep_hoa_hong_lien_reaction
        # and runs after every successful inflict_debuff on the holder.
        cleansable=False,
        emoji="🔴",
    ),
    EffectMeta(
        key="DebuffNghiepHoa",
        vi="Nghiệp Hỏa", en="Karma Fire",
        kind=EffectKind.DEBUFF,
        description_vi=(
            "Lửa nghiệp lực thiêu đốt — mỗi tầng gây 3% HP tối đa làm "
            "sát thương Hỏa mỗi lượt. Tích từ Nghiệp Hỏa Hồng Liên: mỗi "
            "debuff mới in 1 tầng. Tan biến khi Hồng Liên kết thúc."
        ),
        # Stack-based hoa DoT. Tick reads ``nghiep_hoa_stacks ×
        # nghiep_hoa_per_stack_pct`` via the dot.py kind table. Boss cap
        # set generously — uncapped, the 99-stack ceiling × 3 % hp_max
        # against a multi-million HP boss would still trivialize fights.
        stack_kind="nghiep_hoa",
        dot_element="hoa",
        stack_cap=99,
        per_stack_pct=0.03,
        boss_dot_cap_matk_scale=8.0,
        emoji="🔥",
    ),
    EffectMeta(
        key="DebuffHoaVan",
        vi="Hỏa Vân", en="Fire Cloud Mark",
        kind=EffectKind.DEBUFF,
        description_vi=(
            "Hỏa Vân kiếm khí bám trên thân — mỗi lần né tránh (cả hai bên) "
            "chồng 1 tầng. **Đủ 5 tầng** sẽ kích **Hỏa Vân Sậu Thiên Kiếm** "
            "(tự động, luôn bạo kích, tiêu hết tầng)."
        ),
        stack_cap=5,
        emoji="🗡️",
    ),
    EffectMeta(
        key="DebuffPhuongHoa",
        vi="Phượng Hỏa", en="Phoenix Fire",
        kind=EffectKind.DEBUFF,
        description_vi=(
            "Chân hỏa Phượng Hoàng đeo bám thân thể địch — mỗi tầng gây 2% "
            "HP tối đa làm sát thương Hỏa mỗi lượt và giảm 10% hồi máu "
            "nhận vào từ mọi nguồn (tối đa 3 tầng)."
        ),
        stack_kind="phuong_hoa",
        dot_element="hoa",
        stack_cap=3,
        per_stack_pct=0.02,
        # Per-stack heal-reduction scaling — ``heal_taken_reduce`` is consumed
        # by ``_apply_heal`` (capped at 0.90 globally).
        stat_bonus={"phuong_hoa_per_stack_heal_reduce": 0.10},
        scaling_rules=(
            {
                "key": "phuong_hoa_per_stack_heal_reduce",
                "source": "stack:phuong_hoa",
                "output": "heal_taken_reduce",
            },
        ),
        emoji="🦅",
    ),
    EffectMeta(
        key="DebuffUMinh",
        vi="U Minh", en="Underworld Mark",
        kind=EffectKind.DEBUFF,
        description_vi=(
            "Quỷ hỏa U Minh thiêu đốt linh lực — không gây sát thương HP, "
            "nhưng mỗi tầng bào mòn 5% MP tối đa của địch mỗi lượt. "
            "Tan biến khi hết thời lượng (tối đa 3 tầng)."
        ),
        # MP-burn-only debuff: no dot_pct, no stack_kind (so it doesn't enter
        # the HP DoT loop). The per-turn MP drain is handled directly in
        # CombatSession._process_periodic from the holder's u_minh_stacks.
        # Stack cap rides on ``EffectMeta.stack_cap`` (read via
        # ``effective_stack_cap``); no Combatant field needed.
        stack_cap=3,
        dot_element="hoa",
        emoji="👻",
    ),
    EffectMeta(
        key="DebuffChanHoa",
        vi="Tam Muội Chân Hỏa", en="Three-Layer True Fire",
        kind=EffectKind.DEBUFF,
        description_vi=(
            "Lửa Tam Muội thiêu đốt từ bên trong — mỗi tầng gây 2% HP tối "
            "đa làm sát thương Hỏa mỗi lượt, đồng thời khuếch đại MỌI sát "
            "thương DoT hệ Hỏa khác trên thân +4% / tầng. Tối đa 5 tầng. "
            "Đối với thế giới boss / boss đặc biệt: tổn thương mỗi lượt "
            "giới hạn 6× matk người niệm."
        ),
        # Stack-based hoa DoT — tick magnitude reads chan_hoa_stacks ×
        # chan_hoa_per_stack_pct via the dot.py kind table. The fire-amp
        # rider lives in dot._dot_amp's ``meta.dot_element == "hoa"`` gate
        # so it boosts every fire-element DoT on the holder, not just its
        # own ticks. ``boss_dot_cap_matk_scale`` clamps the per-tick damage
        # against world bosses / stat-mutation-immune bosses to 6× the
        # applier's matk so the spell stays meaningful without being the
        # sole win condition.
        stack_kind="chan_hoa",
        dot_element="hoa",
        stack_cap=5,
        per_stack_pct=0.02,
        emoji="🔥",
    ),
    EffectMeta(
        key="DebuffTeLiet",
        vi="Tê Liệt", en="Paralysis",
        kind=EffectKind.CC,
        description_vi="Cơ thể tê liệt, 50% mất lượt.",
        emoji="⚡",
    ),
    EffectMeta(
        key="DebuffDotChay",
        vi="Đốt Cháy Nội Tạng", en="Internal Burning",
        kind=EffectKind.DEBUFF,
        description_vi="Lửa đốt nội tạng, gây sát thương nặng mỗi lượt.",
        dot_pct=0.08,
        dot_element="hoa",
        emoji="💥",
    ),
    EffectMeta(
        key="DebuffHoaXuyenThau",
        vi="Hỏa Xuyên Thấu", en="Fire Penetration",
        kind=EffectKind.DEBUFF,
        description_vi="Lá chắn hỏa khí bị xuyên thủng, giảm mạnh kháng Hỏa.",
        stat_bonus={"res_hoa": -0.15},
        emoji="🔻",
    ),
    EffectMeta(
        key="DebuffMocXuyenThau",
        vi="Mộc Xuyên Thấu", en="Wood Penetration",
        kind=EffectKind.DEBUFF,
        description_vi="Rễ độc xâm nhập cơ thể, giảm mạnh kháng Mộc.",
        stat_bonus={"res_moc": -0.15},
        emoji="🌱",
    ),
    EffectMeta(
        key="DebuffThuyXuyenThau",
        vi="Thủy Xuyên Thấu", en="Water Penetration",
        kind=EffectKind.DEBUFF,
        description_vi="Khiên thủy khí bị xuyên thủng, giảm mạnh kháng Thủy.",
        stat_bonus={"res_thuy": -0.15},
        emoji="💧",
    ),
    EffectMeta(
        key="DebuffCuuKhuc",
        vi="Cửu Khúc Ấn", en="Nine-Bend Mark",
        kind=EffectKind.DEBUFF,
        description_vi=(
            "Bị in dấu Cửu Khúc Hoàng Hà — mỗi đòn niệm gây sát thương "
            "của thí chủ chồng thêm 1 tầng (tối đa 9). **3 tầng**: -atk "
            "và -matk; **6 tầng**: thêm -kháng Thủy; **9 tầng**: mỗi đòn "
            "tấn công đều khởi động Thủy Long Đạn theo dư uy của trận "
            "(40% phổ thông, 100% ở 10 ngọc)."
        ),
        stack_cap=9,
        # Magnitudes are snapshotted onto the holder's ``cuu_khuc_*_active``
        # fields by ``inflict_debuff`` (so the formation tier of the *applier*
        # drives the strength). Scaling rules read those fields directly via
        # ``key: "field:..."`` and route them through the gated expansion —
        # ≥3 stacks for atk/matk shred, ≥6 stacks for thuy res shred.
        # ``multiplier: -1.0`` flips sign because the field values are stored
        # as positive percentages but applied as reductions.
        scaling_rules=(
            {
                "key": "field:cuu_khuc_atk_reduce_active",
                "source": "constant",
                "gate_source": "stack:cuu_khuc",
                "min": 3,
                "multiplier": -1.0,
                "output": "atk_pct",
            },
            {
                "key": "field:cuu_khuc_atk_reduce_active",
                "source": "constant",
                "gate_source": "stack:cuu_khuc",
                "min": 3,
                "multiplier": -1.0,
                "output": "matk_pct",
            },
            {
                "key": "field:cuu_khuc_res_shred_active",
                "source": "constant",
                "gate_source": "stack:cuu_khuc",
                "min": 6,
                "multiplier": -1.0,
                "output": "res_thuy",
            },
        ),
        emoji="🌊",
    ),
    EffectMeta(
        key="DebuffNhuocThuyAn",
        vi="Nhược Thủy Ấn", en="Weak Water Mark",
        kind=EffectKind.DEBUFF,
        description_vi=(
            "Bị in dấu Nhược Thủy: mỗi tầng giảm 4% Kháng Thủy. "
            "Khi chồng đủ 5 tầng, ấn ký bùng nổ — gây sát thương bằng "
            "10% lượng HP đã mất của chính mục tiêu, sau đó reset về 0."
        ),
        # Per-stack penalty expanded via ``scaling_rules`` against
        # ``combatant.thuy_mark_stacks``. ``stack_cap`` is the canonical
        # cap (read via ``effective_stack_cap``).
        # ``thuy_mark_detonate_lost_hp_pct`` is config-only (consumed by
        # casting.py's detonation hook) and still gets popped at the
        # bottom of get_combat_modifiers to keep it out of the live stat dict.
        stack_cap=5,
        stat_bonus={
            "thuy_mark_per_stack_res": -0.04,
            "thuy_mark_detonate_lost_hp_pct": 0.10,
        },
        scaling_rules=(
            {"key": "thuy_mark_per_stack_res", "source": "stack:thuy_mark", "output": "res_thuy"},
        ),
        emoji="🌊",
    ),
    EffectMeta(
        key="DebuffDocTo",
        vi="Độc Tố", en="Poison",
        kind=EffectKind.DEBUFF,
        description_vi="Độc tố ăn mòn cơ thể, gây sát thương mỗi lượt.",
        # Stack-based: tick = poison_stacks × poison_per_stack_pct × max(atk, matk) × DOT_POWER_COEF.
        stack_kind="poison",
        dot_element="moc",
        stack_cap=5,
        per_stack_pct=0.008,
        emoji="☠️",
    ),
    EffectMeta(
        key="DebuffBaoMon",
        vi="Bào Mòn", en="Corrosion",
        kind=EffectKind.DEBUFF,
        description_vi="Bào mòn kháng cự, giảm khả năng chịu đòn.",
        stat_bonus={"res_all": -0.05},
        emoji="🧪",
    ),
    EffectMeta(
        key="DebuffTroBuoc",
        vi="Trói Buộc", en="Bind",
        kind=EffectKind.DEBUFF,
        description_vi="Bị trói buộc, giảm mạnh tốc độ và không thể tháo chạy.",
        stat_bonus={"spd_pct": -0.50},
        emoji="⛓️",
    ),
    EffectMeta(
        key="DebuffLunDat",
        vi="Lún Đất", en="Quicksand",
        kind=EffectKind.DEBUFF,
        description_vi="Lún xuống đất, giảm tốc độ.",
        stat_bonus={"spd_pct": -0.30},
        emoji="🌱",
    ),
    EffectMeta(
        key="EffectNgungDong",
        vi="Ngưng Đọng", en="Stagnation",
        kind=EffectKind.DEBUFF,
        description_vi="Khí trường ngưng đọng, giảm tốc độ.",
        stat_bonus={"spd_pct": -0.25},
        emoji="💨",
    ),
    EffectMeta(
        key="DebuffLamCham",
        vi="Làm Chậm", en="Slow",
        kind=EffectKind.DEBUFF,
        description_vi="Tốc độ bị giảm.",
        stat_bonus={"spd_pct": -0.25},
        emoji="🐢",
    ),
    EffectMeta(
        key="DebuffDongBang",
        vi="Đóng Băng", en="Frozen",
        kind=EffectKind.CC,
        description_vi="Bị đông cứng, mất lượt hành động.",
        skips_turn=True,
        emoji="🧊",
    ),
    EffectMeta(
        key="DebuffChayMau",
        vi="Chảy Máu", en="Bleed",
        kind=EffectKind.DEBUFF,
        description_vi="Máu chảy không ngừng, gây sát thương mỗi lượt.",
        # Stack-based: tick = bleed_stacks × bleed_per_stack_pct × max(atk, matk) × DOT_POWER_COEF.
        stack_kind="bleed",
        dot_element="kim",
        stack_cap=5,
        per_stack_pct=0.008,
        emoji="🩸",
    ),
    EffectMeta(
        key="DebuffPhaGiap",
        vi="Phá Giáp", en="Armor Break",
        kind=EffectKind.DEBUFF,
        description_vi="Giáp phòng thủ bị phá vỡ, giảm khả năng chịu đòn.",
        stat_bonus={"final_dmg_reduce": -0.15},
        emoji="🔨",
    ),
    EffectMeta(
        key="DebuffXeRach",
        vi="Xé Rách", en="Lacerate",
        kind=EffectKind.DEBUFF,
        description_vi="Xé nát kháng cự, giảm mạnh kháng nguyên tố.",
        stat_bonus={"res_all": -0.08},
        emoji="🗡️",
    ),
    EffectMeta(
        key="DebuffCuonBay",
        vi="Cuốn Bay", en="Knock Up",
        kind=EffectKind.CC,
        description_vi="Bị cuốn bay lên, mất lượt hành động.",
        skips_turn=True,
        emoji="💨",
    ),
    EffectMeta(
        key="DebuffCatDut",
        vi="Cắt Đứt Linh Khí", en="Qi Severance",
        kind=EffectKind.DEBUFF,
        description_vi="Linh khí bị cắt đứt, giảm hiệu quả hồi sinh lực.",
        stat_bonus={"hp_regen_pct": -0.3},
        emoji="✂️",
    ),
    EffectMeta(
        key="CCMuted",
        vi="Câm Lặng", en="Silence",
        kind=EffectKind.CC,
        description_vi="Bị câm lặng, không thể sử dụng kỹ năng.",
        prevents_skills=True,
        emoji="🔇",
    ),
    EffectMeta(
        key="CCStun",
        vi="Choáng", en="Stun",
        kind=EffectKind.CC,
        description_vi="Bị choáng, mất lượt hành động.",
        skips_turn=True,
        emoji="💫",
    ),
    EffectMeta(
        key="CCInterrupt",
        vi="Ngắt Kỹ Năng", en="Interrupt",
        kind=EffectKind.CC,
        description_vi="Kỹ năng bị ngắt, lượt này không thể sử dụng kỹ năng.",
        prevents_skills=True,
        emoji="⛔",
    ),
    EffectMeta(
        key="CCLockBreak",
        vi="Khóa Đột Phá", en="Breakthrough Lock",
        kind=EffectKind.CC,
        description_vi="Đột phá bị khóa bởi trạng thái khống chế.",
        emoji="🔒",
    ),
    EffectMeta(
        key="DebuffSetDanh",
        vi="Sét Đánh", en="Lightning Strike",
        kind=EffectKind.DEBUFF,
        description_vi="Bị sét đánh, chịu thêm sát thương sét mỗi lượt.",
        dot_pct=0.05,
        dot_element="loi",
        emoji="⚡",
    ),
    EffectMeta(
        key="DebuffSocDien",
        vi="Sốc Điện", en="Electric Shock",
        kind=EffectKind.DEBUFF,
        description_vi="Thần kinh tê rần — mỗi tầng Sốc Điện khiến mục tiêu chịu thêm sát thương Lôi từ đòn đánh.",
        # NOT a DoT — shock stacks amp incoming Lôi hits via combatant
        # ``shock_per_stack_pct`` rather than ticking. ``stack_kind`` is
        # intentionally unset so get_periodic_damage doesn't try to call
        # calculate_dot_damage on it. The cap/per-stack values seed via
        # the kind→effect-key map in helpers.py, not via stack_kind.
        stack_cap=5,
        per_stack_pct=0.04,
        emoji="⚡",
    ),
    EffectMeta(
        key="DebuffAnPhong",
        vi="Ấn Phong", en="Wind Mark",
        kind=EffectKind.DEBUFF,
        description_vi="Ấn ký gió bám lên mục tiêu — né tránh suy giảm, dễ bị bạo kích và chịu sát thương bạo khổng lồ từ người đánh dấu.",
        stat_bonus={"evasion_rating": -150},
        emoji="🌀",
    ),
    EffectMeta(
        key="DebuffLoaMat",
        vi="Lóa Mắt", en="Blinded",
        kind=EffectKind.DEBUFF,
        description_vi="Tầm nhìn bị Âm khí che mờ — mỗi đòn đánh đều có khả năng đánh trượt.",
        emoji="🌫️",
    ),
    EffectMeta(
        key="DebuffTanDiet",
        vi="Tận Diệt", en="Annihilation",
        kind=EffectKind.DEBUFF,
        description_vi="Sinh cơ bị tận diệt — HP tối đa và hồi HP đều suy giảm vĩnh viễn trong trận. Magnitude is supplied per-cast via ``effect_overrides`` (``hp_max_pct`` shrinks max HP one-shot on first apply; ``hp_regen_pct`` aggregates while the effect is active).",
        cleansable=False,
        emoji="☠️",
    ),
    EffectMeta(
        key="DebuffVoDao",
        vi="Vô Đạo", en="Way Severed",
        kind=EffectKind.DEBUFF,
        description_vi="Đạo cơ bị cắt đứt — toàn bộ máu hồi nhận vào giảm 90% trong 3 lượt.",
        # ``heal_taken_reduce`` is read by ``CombatSession._apply_heal``; default
        # 0.9 here, but skills can override per-cast via ``effect_overrides``.
        stat_bonus={"heal_taken_reduce": 0.9},
        emoji="🩸",
    ),
    EffectMeta(
        key="DebuffSuyKhi",
        vi="Suy Khí", en="Qi Drain",
        kind=EffectKind.DEBUFF,
        description_vi="Khí huyết suy kiệt — sức tấn công vật lý sụt giảm.",
        stat_bonus={"atk_pct": -0.20},
        emoji="🩸",
    ),
    EffectMeta(
        key="DebuffPhapNhuoc",
        vi="Pháp Nhược", en="Spell Weakened",
        kind=EffectKind.DEBUFF,
        description_vi="Linh lực rối loạn — sức tấn công pháp thuật sụt giảm.",
        stat_bonus={"matk_pct": -0.20},
        emoji="🌫️",
    ),
    EffectMeta(
        key="DebuffLinhLucKiet",
        vi="Linh Lực Kiệt", en="Spiritual Exhaustion",
        kind=EffectKind.DEBUFF,
        description_vi="Kinh mạch khô kiệt — tốc độ hồi linh lực sụt giảm.",
        stat_bonus={"mp_regen_pct": -0.50},
        emoji="💧",
    ),
    EffectMeta(
        key="DebuffAmThucKy",
        vi="Âm Thực Ký", en="Shadow-Devour Mark",
        kind=EffectKind.DEBUFF,
        description_vi="Quỷ khí khắc lên hồn — kháng Âm sụt mạnh, dấu ấn được làm mới mỗi lần tiểu quỷ chạm tới.",
        stat_bonus={"res_am": -0.25},
        emoji="👁️",
    ),
    EffectMeta(
        key="DebuffPhongDoMa",
        vi="Phong Đô Ma Khí", en="Fengdu Demonic Mist",
        kind=EffectKind.DEBUFF,
        description_vi="Sương mù Phong Đô bao trùm — kháng Âm sụt 5% (trước khi cộng dồn ngọc khảm).",
        stat_bonus={"res_am": -0.05},        
        cleansable=False,
        emoji="🪦",
    ),
    EffectMeta(
        key="DebuffXichLuyenToaHon",
        vi="Xích Luyện Tỏa Hồn", en="Red-Refining Soul-Lock",
        kind=EffectKind.DEBUFF,
        description_vi=(
            "Xích Luyện ngọn lửa tỏa hồn — né tránh, tốc hồi linh lực, tốc hồi "
            "sinh lực đều sụt 10% (giá trị cơ bản, cộng dồn theo ngưỡng ngọc "
            "khảm Xích Luyện Tỏa Hồn Trận — tối đa 50% mỗi chỉ số)."
        ),
        stat_bonus={
            "evasion_rating_pct": -0.10,
            "mp_regen_pct": -0.10,
            "hp_regen_pct": -0.10,
        },
        cleansable=False,
        emoji="🔥",
    ),
    EffectMeta(
        key="BuffPhatQuangPhoChieu",
        vi="Phật Quang Phổ Chiếu", en="Buddha Light Universal Shine",
        kind=EffectKind.BUFF,
        description_vi=(
            "Phật quang phổ chiếu khắp thân — mọi nguồn hồi máu nhận được "
            "tăng thêm 30% trong 3 lượt."
        ),
        stat_bonus={"heal_taken_bonus": 0.30},
        emoji="🌟",
    ),
    EffectMeta(
        key="BuffThamPhanChiNo",
        vi="Thẩm Phán Chi Nộ", en="Wrath of Judgment",
        kind=EffectKind.BUFF,
        description_vi=(
            "Cơn thịnh nộ phán quyết bùng cháy — sức tấn công pháp thuật "
            "(MATK) tăng 20% trong 3 lượt."
        ),
        stat_bonus={"matk_pct": 0.20},
        emoji="⚖️",
    ),
    EffectMeta(
        key="BuffCamLoLongLuc",
        vi="Cam Lộ Long Lực", en="Sweet Dew Dragon Power",
        kind=EffectKind.BUFF,
        description_vi=(
            "Cam lộ tẩy uế hóa thành long lực — sát thương Thủy +20% trong "
            "1 lượt sau khi Thanh Tẩy thành công."
        ),
        # dmg_bonus_thuy is read by build_attack_stats via the standard
        # actor_mods.get(f"dmg_bonus_{elem}") fold (combat_hit.py:96).
        stat_bonus={"dmg_bonus_thuy": 0.20},
        stealable=False,
        emoji="🐉",
    ),
    EffectMeta(
        key="BuffCamLoTinhHoa",
        vi="Cam Lộ Tịnh Hóa", en="Sweet Dew Purification",
        kind=EffectKind.BUFF,
        description_vi=(
            "Cam lộ rưới xuống tẩy uế thân — mỗi lượt có 50% cơ hội Thanh "
            "Tẩy 1 trạng thái xấu, và mỗi lần Thanh Tẩy thành công còn hồi "
            "5% HP tối đa và nhận **Cam Lộ Long Lực** (+20% sát thương Thủy "
            "trong 1 lượt). Kéo dài 4 lượt."
        ),
        # cleanse_on_turn_pct + cleanse_heal_pct ride the standard try_cleanse
        # buff-layer fold (lc_effects/quang.py) — works on non-Quang holders
        # via that path's buff-bonus override branch. Buffs default to
        # ``cleansable=False`` (see EffectMeta __post_init__), which is what
        # we want here: the picker shouldn't self-strip the aura granting
        # the cleanse.
        stat_bonus={
            "cleanse_on_turn_pct": 0.50,
            "cleanse_heal_pct": 0.05,
        },
        stealable=False,
        emoji="💧",
    ),
    EffectMeta(
        key="BuffPhaMaChanNgon",
        vi="Phá Ma Chân Ngôn", en="Demon-Breaking True Mantra",
        kind=EffectKind.BUFF,
        description_vi=(
            "Chân ngôn phá ma vang vọng — mỗi lượt có 40% cơ hội Thanh Tẩy "
            "1 trạng thái xấu, và mỗi lần Thanh Tẩy thành công còn hồi 5% HP "
            "tối đa. Hiệu ứng kéo dài 4 lượt."
        ),
        stat_bonus={
            "cleanse_on_turn_pct": 0.40,
            "cleanse_heal_pct": 0.05,
        },
        emoji="🔔",
    ),
    EffectMeta(
        key="BuffLuuQuangHuyenAnh",
        vi="Lưu Quang Huyễn Ảnh", en="Flowing Light Phantom",
        kind=EffectKind.BUFF,
        description_vi=(
            "Hóa thân thành ảo ảnh dòng quang — mỗi lần chịu sát thương cộng "
            "thêm 10% tốc độ (tối đa +30%), và mỗi lần né tránh thành công lập "
            "tức phản công bằng **Cực Quang Trảm**. Hiệu ứng kéo dài 5 lượt."
        ),
        # spd_pct stamped onto effect_overrides per-stack by the take_damage
        # hook in cast_skill — ramps 0.10 → 0.20 → 0.30. Empty meta default
        # so cast-time application is just a dormant marker until the first
        # incoming hit lights it up. The on-evade chain target lives on the
        # granting skill's JSON (``effect_overrides.BuffLuuQuangHuyenAnh
        # .proc_on_holder_evade_cast``), so the engine reads it from the
        # holder's per-instance override rather than hardcoded here.
        stat_bonus={},
        emoji="✨",
    ),
    EffectMeta(
        key="BuffQuangMinhTungHoanhAura",
        vi="Quang Minh Tung Hoành Bộ — Hào Quang", en="Bright Light Free-Step Aura",
        kind=EffectKind.BUFF,
        description_vi=(
            "Hào quang bị động — mỗi 1 điểm Tốc cộng thêm 10 Khiên tối đa. "
            "Tự kích hoạt khi vào trận, kéo dài cả trận đấu."
        ),
        # shield_max_per_spd is read by Combatant.shield_cap() via
        # get_combat_modifiers — recomputed each shield-cap call so any
        # in-combat spd buffs (e.g. the active counterpart) immediately
        # widen the shield ceiling.
        stat_bonus={"shield_max_per_spd": 10.0},
        cleansable=False,
        stealable=False,
        emoji="🌅",
    ),
    EffectMeta(
        key="BuffQuangMinhTungHoanh",
        vi="Quang Minh Tung Hoành Bộ", en="Bright Light Free-Step",
        kind=EffectKind.BUFF,
        description_vi=(
            "Cước pháp tung hoành — +20% Tốc và +0.5% hồi Khiên/lượt trong 4 lượt."
        ),
        stat_bonus={"spd_pct": 0.20, "shield_regen_pct": 0.005},
        emoji="✨",
    ),
    EffectMeta(
        key="BuffDaiThienSuAura",
        vi="Đại Thiên Sứ — Hào Quang", en="Great Angel Aura",
        kind=EffectKind.BUFF,
        description_vi=(
            "Đại Thiên Sứ tỏa hào quang — +10% kháng tất cả nguyên tố và "
            "+10% hiệu ứng hồi máu nhận được. Tồn tại khi Đại Thiên Sứ "
            "còn hiện diện."
        ),
        stat_bonus={"res_all": 0.10, "heal_taken_bonus": 0.10},
        cleansable=False,
        stealable=False,
        emoji="🪽",
    ),
    EffectMeta(
        key="BuffHaiThiThanLauAura",
        vi="Hải Thị Thận Lâu — Hào Quang", en="Sea-Mirage Tower Aura",
        kind=EffectKind.BUFF,
        description_vi=(
            "Hào quang bị động — thân pháp ảo cảnh: mỗi 1 điểm Tốc cao hơn "
            "địch nhân cộng thêm 20 Né Tránh khi đối thủ tấn công vào thân. "
            "Tự kích hoạt khi vào trận, kéo dài cả trận đấu."
        ),
        # ``evasion_rating_per_spd_diff`` follows the generic stat-diff
        # pattern (see src/game/engine/stat_diff.py) — the helper folds
        # ``max(0, defender_eff_spd - attacker_eff_spd) * 20`` into
        # ``target_mods["evasion_rating"]`` right before build_defense_stats
        # consumes it. Active counterpart's spd_pct widens the gap, stacking
        # synergistically with the passive evasion gain.
        stat_bonus={"evasion_rating_per_spd_diff": 20.0},
        cleansable=False,
        stealable=False,
        emoji="🌊",
    ),
    EffectMeta(
        key="BuffHaiThiThanLau",
        vi="Hải Thị Thận Lâu", en="Sea-Mirage Tower",
        kind=EffectKind.BUFF,
        description_vi=(
            "Bộ pháp Hải Thị — +20% Tốc, đồng thời mỗi đòn niệm gây sát "
            "thương có 60% cơ hội Làm Chậm địch nhân -20% Tốc. Kéo dài 4 lượt."
        ),
        # spd_pct rides the standard regen path; ``hai_thi_slow_chance`` and
        # ``hai_thi_slow_magnitude`` are config keys read by the on-hit hook
        # in cast_skill — popped from get_combat_modifiers below.
        stat_bonus={
            "spd_pct": 0.20,
            "hai_thi_slow_chance": 0.60,
            "hai_thi_slow_magnitude": -0.20,
        },
        cleansable=False,
        stealable=False,
        emoji="🏯",
    ),
    EffectMeta(
        key="BuffLangBaViBoAura",
        vi="Lăng Ba Vi Bộ — Hào Quang", en="Wave-Stepping Microsteps Aura",
        kind=EffectKind.BUFF,
        description_vi=(
            "Hào quang bị động — bộ pháp Lăng Ba: lướt trên sóng nhẹ như "
            "không trọng lượng. **+500 Né Tránh** và **+20% Tốc** vĩnh "
            "viễn (cả trận đấu)."
        ),
        stat_bonus={"evasion_rating": 500.0, "spd_pct": 0.20},
        cleansable=False,
        stealable=False,
        emoji="🌊",
    ),
    EffectMeta(
        key="BuffThuyThuongPhieuAura",
        vi="Thủy Thượng Phiêu — Hào Quang", en="Water-Walking Drift Aura",
        kind=EffectKind.BUFF,
        description_vi=(
            "Hào quang bị động — bộ pháp Thủy Thượng Phiêu: lướt trên "
            "mặt nước, vĩnh viễn **+20% Tốc** (cả trận đấu)."
        ),
        stat_bonus={"spd_pct": 0.20},
        cleansable=False,
        stealable=False,
        emoji="🌊",
    ),
    EffectMeta(
        key="BuffThuyThuongPhieu",
        vi="Thủy Thượng Phiêu", en="Water-Walking Drift",
        kind=EffectKind.BUFF,
        description_vi=(
            "Trạng thái Thủy Thượng Phiêu — đọc thế trận mà tùy biến: "
            "**khi địch nhân nhanh hơn**, gia tốc bản thân **+20% Tốc** "
            "để đuổi kịp; **khi địch nhân chậm hơn**, dồn lực vào đòn "
            "niệm **+20% Sát Thương Thủy**. Kéo dài 3 lượt."
        ),
        # Comparison-gated flat magnitudes — the ``_if_<source>_higher`` /
        # ``_if_<source>_lower`` family in stat_diff.py routes these to the
        # real ``spd_pct`` / ``dmg_bonus_thuy`` keys based on the live
        # spd matchup against the opponent at cast time.
        stat_bonus={
            "spd_pct_if_spd_lower": 0.20,
            "dmg_bonus_thuy_if_spd_higher": 0.20,
        },
        cleansable=False,
        stealable=False,
        emoji="💨",
    ),
    EffectMeta(
        key="BuffTuyetDieuVoAnh",
        vi="Tuyệt Diệu Vô Ảnh", en="Sublime Shadowless",
        kind=EffectKind.BUFF,
        description_vi=(
            "Trạng thái Tuyệt Diệu Vô Ảnh — thân pháp hư ảo, không một "
            "thủ đoạn giảm Tốc nào có thể chạm tới. **Miễn dịch mọi "
            "trạng thái giảm Tốc** trong 3 lượt."
        ),
        # ``slow_immune`` is a config flag read by ``inflict_debuff`` to
        # gate any incoming debuff whose effective stat_bonus carries a
        # negative ``spd_pct``. Popped from ``get_combat_modifiers`` so it
        # never leaks into the live stat dict.
        stat_bonus={"slow_immune": 1.0},
        cleansable=False,
        stealable=False,
        emoji="👣",
    ),
    EffectMeta(
        key="BuffTuLuongBatThienCan",
        vi="Tứ Lạng Bạt Thiên Cân", en="Four Liang Move Thousand Catties",
        kind=EffectKind.BUFF,
        description_vi=(
            "Lấy nhu thắng cương — khi HP > 50% chuyển sang thế công, +20% "
            "sát thương Thủy; khi HP < 50% chuyển sang thế thủ, +20% giảm "
            "sát thương phải nhận. Hoán đổi tự động theo HP mỗi đòn. "
            "Kéo dài 4 lượt."
        ),
        # Threshold-gated flat magnitudes — ``source: "constant"`` keeps
        # the bonus flat (×1) while ``gate_source: "hp_pct"`` checks the
        # holder's live HP%. Strict greater/less expressed via min: 0.5001
        # / max: 0.4999 so exactly 50% HP yields neither (a one-pixel
        # sliver, matching the original semantics).
        stat_bonus={"tu_luong_thuy_amp": 0.20, "tu_luong_dr": 0.20},
        scaling_rules=(
            {
                "key": "tu_luong_thuy_amp",
                "source": "constant",
                "gate_source": "hp_pct",
                "min": 0.5001,
                "output": "dmg_bonus_thuy",
            },
            {
                "key": "tu_luong_dr",
                "source": "constant",
                "gate_source": "hp_pct",
                "max": 0.4999,
                "output": "final_dmg_reduce",
            },
        ),
        cleansable=False,
        stealable=False,
        emoji="☯️",
    ),
    EffectMeta(
        key="BuffKinhHoaThuyNguyet",
        vi="Kính Hoa Thủy Nguyệt", en="Mirror Flower Water Moon",
        kind=EffectKind.BUFF,
        description_vi=(
            "Ảo cảnh kính hoa — mỗi lượt trước khi hành động có 50% cơ hội "
            "đẩy ngược một trạng thái xấu trên thân về phía địch nhân (giữ "
            "nguyên thời gian + cường độ). Trạng thái dạng tầng (Cháy/Chảy "
            "Máu/Sốc/Độc) không thể chuyển. Kéo dài 3 lượt."
        ),
        # debuff_transfer_on_turn_pct is read by ``_try_transfer_debuffs``
        # in session.py; popped from get_combat_modifiers below so it doesn't
        # masquerade as a real stat.
        stat_bonus={"debuff_transfer_on_turn_pct": 0.50},
        cleansable=False,
        stealable=False,
        emoji="🪞",
    ),
    EffectMeta(
        key="BuffThuyMacThienHoa",
        vi="Thủy Mặc Thiên Hoa", en="Water-Ink Heaven Flower",
        kind=EffectKind.BUFF,
        description_vi=(
            "Thủy Mặc — 25% sát thương phải nhận sẽ chuyển hóa thành mất "
            "linh lực (MP) thay vì máu. Phần MP không đủ chi trả vẫn rơi "
            "lại HP như thường. Đồng thời +2% hồi linh lực mỗi lượt."
        ),
        # ``dmg_to_mp_pct`` is consumed by ``Combatant.take_damage`` directly
        # via get_combat_modifiers; popped from the aggregated mod dict by the
        # cleanup at the bottom of get_combat_modifiers so it doesn't pollute
        # the stat namespace. ``mp_regen_pct`` rides the regular regen path.
        stat_bonus={"dmg_to_mp_pct": 0.25, "mp_regen_pct": 0.02},
        cleansable=False,
        stealable=False,
        emoji="🌊",
    ),
    EffectMeta(
        key="BuffThuyVi",
        vi="Thủy Vi", en="Subtle Water",
        kind=EffectKind.BUFF,
        description_vi=(
            "Thủy khí tinh tế thấm vào kỹ năng — N đòn niệm tiếp theo xuyên "
            "thẳng X% Hộ Thuẫn của địch nhân. Tự tan khi cạn linh khí."
        ),
        # Charges + magnitude live in the per-instance override
        # (``thuy_vi_charges`` + ``thuy_vi_bypass_pct``); this meta carries no
        # auto-aggregated stat. Both keys are popped in get_combat_modifiers
        # so they don't masquerade as real stat keys.
        stat_bonus={},
        cleansable=False,
        stealable=False,
        emoji="💧",
    ),
    EffectMeta(
        key="BuffThanhQuangThuan",
        vi="Thánh Quang Thuẫn", en="Holy Light Shield",
        kind=EffectKind.BUFF,
        description_vi=(
            "Thuẫn quang thánh khiết bao quanh thân — miễn dịch trạng thái xấu "
            "+40%, mỗi lượt có 25% cơ hội Thanh Tẩy 1 trạng thái xấu, và giảm "
            "10% sát thương phải nhận trong 3 lượt."
        ),
        stat_bonus={
            "debuff_immune_pct": 0.40,
            "cleanse_on_turn_pct": 0.25,
            "final_dmg_reduce": 0.10,
        },
        emoji="🛡️",
    ),
    EffectMeta(
        key="BuffThienSuHoMenh",
        vi="Thiên Sứ Hộ Mệnh", en="Angel Guardian",
        kind=EffectKind.BUFF,
        description_vi=(
            "Hào quang thiên sứ phù hộ — tăng kháng tất cả nguyên tố, HP tối đa, "
            "và giảm sát thương phải nhận. Khi nhận đòn chí mạng, hào quang vỡ "
            "tan và hồi sinh chủ nhân với 50% HP (1 lần/trận). Không thể bị cướp."
        ),
        stat_bonus={
            "res_all": 0.15,
            "hp_max_pct": 0.20,
            "final_dmg_reduce": 0.15,
            "revive_hp_pct": 0.50,
        },
        stealable=False,
        emoji="👼",
    ),
    EffectMeta(
        key="DebuffLucHonChu",
        vi="Lục Hồn Chú", en="Six-Soul Curse",
        kind=EffectKind.DEBUFF,
        description_vi="Sáu hồn ma quấn xác — kháng Âm và sức chịu DoT bị phá vỡ. Mỗi lượt: tổn HP theo Âm Khí của người niệm chú (xuyên qua khiên 50%) và bào mòn linh lực địch.",
        dot_caster_hp_pct=0.06,
        dot_caster_matk_scale=5.5,
        dot_element="am",
        stat_bonus={"res_am": -0.20, "dot_taken_bonus": 0.20},
        dot_shield_drain_pct=0.5,
        dot_mp_drain_pct=0.05,
        emoji="🕯️",
    ),
    EffectMeta(
        key="DebuffThienMaPost",
        vi="Thiên Ma Hậu Di Chứng", en="Demon-Mode Backlash",
        kind=EffectKind.DEBUFF,
        description_vi="Thân thể quá tải sau cơn cuồng vũ Thiên Ma — chịu thêm sát thương trong 2 lượt, sau đó tự động trở lại Thiên Ma. ``cleansable=False`` so the cycle can't be Thanh Tẩy'd off.",
        stat_bonus={"final_dmg_reduce": -0.30},
        on_expire_apply=("BuffThienMa", {"duration": 2}),
        cleansable=False,
        emoji="🩸",
    ),
]

_UTIL: list[EffectMeta] = [
    EffectMeta(
        key="HpRegen",
        vi="Hồi Sinh Lực", en="HP Regen",
        kind=EffectKind.BUFF,
        description_vi="Hồi tức thì 10% sinh lực tối đa khi sử dụng.",
        instant_heal_pct=0.10,
        emoji="💚",
    ),
    EffectMeta(
        key="MpRegen",
        vi="Hồi Linh Lực", en="MP Regen",
        kind=EffectKind.BUFF,
        description_vi="Hồi tức thì 10% linh lực tối đa khi sử dụng.",
        instant_mp_pct=0.10,
        emoji="💙",
    ),
]

# ── Build registry ────────────────────────────────────────────────────────────

EFFECTS: dict[str, EffectMeta] = {m.key: m for m in _BUFFS + _DEBUFFS_CC + _UTIL}

# ── Default durations ─────────────────────────────────────────────────────────

_DEFAULT_DURATIONS: dict[str, int] = {
    # Buffs — typically 3–4 turns
    EffectKey.BUFF_KIEM_KHI: 3, EffectKey.BUFF_KIEM_Y: 4, EffectKey.BUFF_VO_NGA_KIEM_TAM: 3,
    EffectKey.BUFF_NHIET_TINH: 3, EffectKey.BUFF_HOA_THAN: 3, EffectKey.BUFF_LIET_DIEM: 3,
    EffectKey.BUFF_BANG_GIAP: 4, EffectKey.BUFF_THUY_KINH: 3, EffectKey.BUFF_HAN_KHI: 3,
    EffectKey.BUFF_LOI_THAN: 3, EffectKey.BUFF_TOC_LOI: 2, EffectKey.BUFF_NGU_PHONG: 3,
    EffectKey.BUFF_PHONG_VU: 3, EffectKey.BUFF_SINH_CO: 4, EffectKey.BUFF_CAN_CO: 3,
    EffectKey.BUFF_KIM_CUONG: 3, EffectKey.BUFF_HOANG_KIM: 3, EffectKey.BUFF_DAI_DIA: 3,
    EffectKey.BUFF_TRONG_TO: 4, EffectKey.BUFF_BAT_TU: 1, EffectKey.BUFF_TANG_TOC: 3,
    EffectKey.BUFF_HO_PHAP: 4, EffectKey.BUFF_HU_KHONG: 2,
    EffectKey.BUFF_DOAN_TUYET: 4,
    # Debuffs — typically 2–3 turns
    EffectKey.DEBUFF_THIEU_DOT: 3, EffectKey.DEBUFF_TE_LIET: 2, EffectKey.DEBUFF_DOT_CHAY: 3,
    EffectKey.DEBUFF_HOA_XUYEN_THAU: 3,
    EffectKey.DEBUFF_MOC_XUYEN_THAU: 3,
    EffectKey.DEBUFF_THUY_XUYEN_THAU: 3,
    EffectKey.DEBUFF_DOC_TO: 3, EffectKey.DEBUFF_BAO_MON: 3, EffectKey.DEBUFF_TRO_BUOC: 2,
    EffectKey.DEBUFF_LUN_DAT: 2, EffectKey.EFFECT_NGUNG_DONG: 2, EffectKey.DEBUFF_LAM_CHAM: 2,
    EffectKey.DEBUFF_DONG_BANG: 2, EffectKey.DEBUFF_CHAY_MAU: 3, EffectKey.DEBUFF_PHA_GIAP: 3,
    EffectKey.DEBUFF_XE_RACH: 2, EffectKey.DEBUFF_CUON_BAY: 1, EffectKey.DEBUFF_CAT_DUT: 3,
    EffectKey.CC_MUTED: 2, EffectKey.CC_STUN: 1, EffectKey.CC_INTERRUPT: 1,
    EffectKey.CC_LOCK_BREAK: 3, EffectKey.DEBUFF_SET_DANH: 2,
    EffectKey.DEBUFF_SOC_DIEN: 3,
    EffectKey.DEBUFF_AN_PHONG: 3,
    EffectKey.DEBUFF_LOA_MAT: 2,
    EffectKey.DEBUFF_VO_DAO: 3,
    EffectKey.DEBUFF_SUY_KHI: 3,
    EffectKey.DEBUFF_PHAP_NHUOC: 3,
    EffectKey.DEBUFF_LINH_LUC_KIET: 3,
    EffectKey.DEBUFF_AM_THUC_KY: 3,
    EffectKey.DEBUFF_PHONG_DO_MA: 2,
    EffectKey.DEBUFF_XICH_LUYEN_TOA_HON: 2,
    EffectKey.BUFF_THIEN_SU_HO_MENH: 99,
    EffectKey.BUFF_THANH_QUANG_THUAN: 3,
    EffectKey.BUFF_PHAT_QUANG_PHO_CHIEU: 3,
    EffectKey.BUFF_THAM_PHAN_CHI_NO: 3,
    EffectKey.BUFF_PHA_MA_CHAN_NGON: 4,
    EffectKey.BUFF_LUU_QUANG_HUYEN_ANH: 5,
    EffectKey.BUFF_QMTH_AURA: 99,
    EffectKey.BUFF_QMTH_ACTIVE: 4,
    EffectKey.BUFF_DAI_THIEN_SU_AURA: 1,
    # Buff is charge-driven (drops at 0 charges), but turn-tick decay is the
    # natural hard cap if the player never lands a damage cast — 99 turns is
    # effectively "until the fight ends or charges run out".
    EffectKey.BUFF_THUY_VI: 99,
    EffectKey.BUFF_THUY_MAC_THIEN_HOA: 4,
    EffectKey.BUFF_KINH_HOA_THUY_NGUYET: 3,
    EffectKey.BUFF_TU_LUONG_BAT_THIEN_CAN: 4,
    EffectKey.BUFF_HAI_THI_THAN_LAU_AURA: 99,
    EffectKey.BUFF_HAI_THI_THAN_LAU: 4,
    EffectKey.BUFF_CAM_LO_TINH_HOA: 4,
    EffectKey.BUFF_CAM_LO_LONG_LUC: 1,
    EffectKey.DEBUFF_LUC_HON_CHU: 4,
    EffectKey.DEBUFF_U_MINH: 3,
    EffectKey.BUFF_PHUONG_HOANG_CHAN_HOA: 4,
    EffectKey.DEBUFF_PHUONG_HOA: 3,
    EffectKey.BUFF_LUU_LY_TINH_HOA: 4,
    EffectKey.BUFF_CUU_DUONG: 4,
    EffectKey.BUFF_BAT_DIET_HOA_CHUNG: 2,
    EffectKey.BUFF_LUU_TINH_CAN_NGUYET: 99,
    EffectKey.DEBUFF_HOA_VAN: 4,
    EffectKey.BUFF_PHUONG_HOANG_TRIEN_SI: 4,
    EffectKey.BUFF_THIEN_MA: 2,
    EffectKey.DEBUFF_THIEN_MA_POST: 2,
    # Lục Dục desires hold for 99 turns by default — the cycle hook in
    # ``_tick_luc_duc_thien_ma_vu`` clears them manually when the amp resolves,
    # so the long duration just guarantees natural-tick decay never beats the
    # cycle to it.
    EffectKey.BUFF_LUC_DUC_SAC: 99,
    EffectKey.BUFF_LUC_DUC_THANH: 99,
    EffectKey.BUFF_LUC_DUC_HUONG: 99,
    EffectKey.BUFF_LUC_DUC_VI: 99,
    EffectKey.BUFF_LUC_DUC_XUC: 99,
    EffectKey.BUFF_LUC_DUC_PHAP: 99,
    EffectKey.BUFF_LUC_DUC_CONG_MINH: 99,
    EffectKey.BUFF_VO_TUONG_THIEN_MA: 4,
    EffectKey.BUFF_MA_KHI_HO_THE: 99,
    EffectKey.BUFF_CHAN_MA_CHI_TAM: 99,
    EffectKey.BUFF_QUY_ANH_ME_TUNG: 99,
    EffectKey.BUFF_MA_LONG_XUAT_UYEN: 99,
}

# Default per-attack miss chance for DebuffLoaMat (Blind). Mirrors the
# DebuffTeLiet 50% skip pattern — attacker is checked, not the target.
BLIND_MISS_CHANCE: float = 0.50


def default_duration(effect_key: str) -> int:
    """Return the default turn duration for an effect."""
    return _DEFAULT_DURATIONS.get(effect_key, 3)


# ── Stat computation helpers ──────────────────────────────────────────────────

def effective_stack_cap(combatant: "Combatant", effect_key: str) -> int:
    """Return the live stack cap for an effect on this combatant.

    Three sources fold in. The first two max-merge to form a "base cap";
    the third is additive on top so flat bonuses from gear/constitutions/
    Linh Căn/buffs stack predictably.

      1. ``EffectMeta.stack_cap`` — designer default in effects.py.
      2. Per-instance override on the holder:
         ``effect_overrides[<effect>].stack_cap`` — set by the skill JSON
         that applied the effect. Max-merged with (1) so a skill can only
         raise the floor, not shrink it.
      3. **Additive bonuses** from two sources:
         a) ``combatant.stack_cap_bonuses[<effect>]`` — flat integer added
            by gear / constitutions / Linh Căn at character-build time.
            Designed to be summed in ``character_stats.py`` from equip
            stats like ``cuu_khuc_stack_cap_bonus`` before combat starts.
         b) Active effect ``stat_bonus`` entries keyed
            ``"stack_cap_bonus:<effect>"`` — lets a buff grant a cap
            bonus while held. Per-skill ``stat_bonus`` overrides on the
            granting buff still win via the standard override flow.

    Burn/bleed/shock/poison are NOT routed through this helper for their
    base+gear cap math — they keep their dedicated ``<kind>_stack_cap``
    Combatant fields because the gear path was already wired before this
    helper existed. Calling ``effective_stack_cap`` on those keys still
    returns a useful value (meta + override + bonuses) but the gear path
    will not feed into it; new gear targeting them should keep writing
    to the existing ``<kind>_stack_cap`` field for now.
    """
    meta = EFFECTS.get(effect_key)
    base = int(meta.stack_cap) if meta is not None else 0

    # Per-instance override on this effect's own entry (max-merge).
    override = combatant.effect_overrides.get(effect_key) or {}
    over_cap = int(override.get("stack_cap", 0) or 0)
    cap = max(base, over_cap)

    # Gear / constitution / Linh Căn flat bonus.
    bonus_dict = getattr(combatant, "stack_cap_bonuses", None)
    if bonus_dict:
        cap += int(bonus_dict.get(effect_key, 0))

    # Active-buff cap bonus — any active effect can grant ``+N stack cap``
    # by carrying ``stat_bonus["stack_cap_bonus:<effect_key>"] = N``. Per-
    # instance overrides on the granting effect win per-stat, mirroring
    # the standard ``get_combat_modifiers`` aggregation.
    bonus_key = f"stack_cap_bonus:{effect_key}"
    for active_key in combatant.effects:
        active_meta = EFFECTS.get(active_key)
        if active_meta is None:
            continue
        active_override = combatant.effect_overrides.get(active_key) or {}
        active_override_stats = active_override.get("stat_bonus") or {}
        if bonus_key in active_override_stats:
            cap += int(active_override_stats[bonus_key])
        elif bonus_key in active_meta.stat_bonus:
            cap += int(active_meta.stat_bonus[bonus_key])

    return cap


def effective_res_cap(combatant: "Combatant", element: str) -> float:
    """Return the live max-resistance cap for ``element`` on this combatant.

    Resolution:
      * Enemies (``combatant.key != "player"``) ALWAYS get
        ``MAX_ELEMENTAL_RES`` so JSON-tuned high res profiles
        (linh-căn apex 0.85 res, etc.) aren't silently nerfed.
      * Player: starts at ``RES_SOFT_CAP`` (0.75) and lifts one-for-one
        for every point of ``<element>_max_resist_bonus`` aggregated
        across the holder's gear/constitution/Linh Căn dict +
        active-buff ``stat_bonus`` contributions. Capped at
        ``MAX_ELEMENTAL_RES`` (0.90).

    Lets stats like ``hoa_max_resist_bonus: 0.15`` declare "+15% to the
    player's hoa cap" without bloating Combatant with one field per element.
    """
    from src.game.constants.balance import MAX_ELEMENTAL_RES, RES_SOFT_CAP
    if getattr(combatant, "key", None) != "player":
        return MAX_ELEMENTAL_RES
    bonus = float(combatant.element_max_resist_bonus.get(element, 0.0))
    bonus += float(
        get_combat_modifiers(combatant).get(f"{element}_max_resist_bonus", 0.0)
    )
    return min(MAX_ELEMENTAL_RES, RES_SOFT_CAP + bonus)


def count_elemental_dots(combatant: "Combatant", element: str) -> int:
    """Return the number of distinct active DoT effects on ``combatant``
    whose ``dot_element`` matches ``element``.

    Same gate as ``get_periodic_damage`` — counts only effects with an
    actual DoT damage path (``dot_pct > 0``, ``stack_kind`` set, or
    caster-stat-driven DoT). Non-tick effects with ``dot_element`` set
    (e.g. ``DebuffHoaXuyenThau``-style res-shred markers without an
    element tag) don't count.

    Used by per-cast damage amps that scale with fire-DoT count and by
    cleanse / proc hooks that fire once per distinct DoT kind.
    """
    return sum(
        1 for k in combatant.effects
        if (m := EFFECTS.get(k)) is not None
        and m.dot_element == element
        and (
            m.dot_pct > 0
            or m.stack_kind
            or m.dot_caster_hp_pct > 0
            or m.dot_caster_matk_scale > 0
        )
    )


def _resolve_scaling_source(combatant: "Combatant", source: str) -> float:
    """Read a scaling-rule source value off ``combatant``.

    Unknown sources return 0.0 so a typo in the data quietly disables the
    rule rather than crashing combat. Stack sources use the ``<name>_stacks``
    convention (e.g. ``"stack:phuong_hoa"`` → ``combatant.phuong_hoa_stacks``).

    The ``"constant"`` source always returns 1.0 — useful for threshold-
    gated flat bonuses where the magnitude is fixed and a separate
    ``gate_source`` carries the trigger condition.
    """
    if source == "constant":
        return 1.0
    if source == "hp_pct":
        hp_max = max(1, getattr(combatant, "hp_max", 1))
        return float(getattr(combatant, "hp", 0)) / hp_max
    if source == "hp_missing_pct":
        hp_max = max(1, getattr(combatant, "hp_max", 1))
        return max(0.0, 1.0 - float(getattr(combatant, "hp", 0)) / hp_max)
    if source == "mp_pct":
        mp_max = max(1, getattr(combatant, "mp_max", 1))
        return float(getattr(combatant, "mp", 0)) / mp_max
    if source == "mp_missing_pct":
        mp_max = max(1, getattr(combatant, "mp_max", 1))
        return max(0.0, 1.0 - float(getattr(combatant, "mp", 0)) / mp_max)
    if source.startswith("stack:"):
        attr = source[6:] + "_stacks"
        return float(getattr(combatant, attr, 0))
    return 0.0


def _apply_scaling_rules(combatant: "Combatant", result: dict) -> None:
    """Expand every active effect's ``scaling_rules`` into ``result``.

    For each rule on each active effect:
      1. Resolve the per-unit magnitude. When ``rule['key']`` starts with
         ``"field:"`` the magnitude is read directly off the combatant
         (e.g. snapshotted applier values like
         ``cuu_khuc_atk_reduce_active``); otherwise it's popped from
         ``result`` (a stat_bonus placeholder, with per-skill overrides
         already folded in by the main aggregation loop).
      2. Resolve the source value off the holder (for scaling).
      3. Resolve the gate value — defaults to the source, or reads
         ``gate_source`` when set (lets a flat-on-threshold rule scale
         by ``"constant"`` while gating on, say, ``"hp_pct"``).
      4. Apply min/max gates — rule skipped if gate value out of range.
      5. Apply bucketing — when ``bucket > 0``, ``units = floor(source / bucket)``;
         otherwise ``units = source`` (good for integer stack sources).
      6. Add ``per_unit × units × multiplier`` to ``result[output]``.
         ``multiplier`` (default 1.0) lets a rule flip sign without
         requiring the source magnitude to be negative — useful when
         the magnitude comes from a naturally-positive field but the
         output is a reduction (e.g. ``atk_pct -= cuu_khuc_atk_reduce``).
    """
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if not meta or not meta.scaling_rules:
            continue
        for rule in meta.scaling_rules:
            key = rule["key"]
            if key.startswith("field:"):
                per_unit = float(getattr(combatant, key[6:], 0.0))
            else:
                per_unit = float(result.pop(key, 0.0))
            if per_unit == 0:
                continue
            source_value = _resolve_scaling_source(combatant, rule["source"])
            gate_value = (
                _resolve_scaling_source(combatant, rule["gate_source"])
                if rule.get("gate_source") else source_value
            )
            lo, hi = rule.get("min"), rule.get("max")
            if lo is not None and gate_value < lo:
                continue
            if hi is not None and gate_value > hi:
                continue
            bucket = float(rule.get("bucket", 0.0))
            units = int(source_value / bucket) if bucket > 0 else source_value
            if units <= 0:
                continue
            multiplier = float(rule.get("multiplier", 1.0))
            out_key = rule["output"]
            result[out_key] = (
                result.get(out_key, 0.0) + per_unit * units * multiplier
            )


def get_combat_modifiers(combatant: "Combatant") -> dict[str, float]:
    """Aggregate all active effect stat bonuses/penalties on a combatant.

    Per-instance overrides (``combatant.effect_overrides[key]['stat_bonus']``)
    win on a per-stat basis: if a heavy skill stamped DebuffXeRach with
    ``res_all: -0.20``, that supersedes the meta default ``-0.08``. Stats
    not present in the override fall back to the meta value, so a skill
    can override one stat without nuking the rest.

    Summon auras (see ``skill_extras.aura_stat_bonus``) are folded in here
    too: each active summon contributes its own copy of its ``aura_buff``,
    so multi-summon strategies stack additively even when every summon
    shares the same aura key.

    Returns a dict with signed float values per stat key:
      final_dmg_bonus, final_dmg_reduce, crit_rating, crit_dmg_rating,
      evasion_rating, crit_res_rating, spd_pct, hp_regen_pct, res_all
    """
    result: dict[str, float] = {}
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if not meta:
            continue
        override = combatant.effect_overrides.get(effect_key) or {}
        override_stats = override.get("stat_bonus") or {}
        for stat, val in meta.stat_bonus.items():
            effective = override_stats.get(stat, val)
            result[stat] = result.get(stat, 0.0) + effective
        # Stats present only in the override (not in the meta) still apply.
        for stat, val in override_stats.items():
            if stat not in meta.stat_bonus:
                result[stat] = result.get(stat, 0.0) + val

    # Lazy import to break the cycle: skill_extras → combatant → effects.
    if combatant.summons:
        from src.game.systems.combat.skill_extras import aura_stat_bonus
        for stat, val in aura_stat_bonus(combatant).items():
            result[stat] = result.get(stat, 0.0) + val
    # Generic ``scaling_rules`` expansion — runs after the main stat_bonus
    # aggregation so each rule can read its per-unit magnitude (the
    # ``key`` placeholder) out of ``result`` with all per-skill overrides
    # already folded in. The placeholder key is popped after scaling so it
    # doesn't leak as a "real" stat. New effects should prefer this path
    # over hand-coding a new ``result.pop(...)`` block below.
    _apply_scaling_rules(combatant, result)

    # Config-only keys — stamped in ``stat_bonus`` so designers can tune
    # without touching engine constants, but consumed by hooks elsewhere
    # (casting / on-evade / inflict_debuff). Pop them here so they never
    # masquerade as real stats. Scaling-rule placeholders are popped by
    # ``_apply_scaling_rules``; this list is only for non-scaling config.
    for cfg_key in (
        # Quỷ Ảnh Mê Tung — stack cap (read by the on-evade hook).
        "quy_anh_max_stacks",
        # Nhược Thủy Ấn — detonation config (read by casting.py).
        "thuy_mark_detonate_lost_hp_pct",
        # Tuyệt Diệu Vô Ảnh — slow-immune gate flag (read by inflict_debuff).
        "slow_immune",
        # Ma Long Xuất Uyên — counter-strike config (read by cast_skill).
        "ma_long_counter_base_dmg",
        "ma_long_counter_matk_pct",
        "ma_long_counter_debuff_chance",
        # Thủy Vi — shield-bypass charge counters (read by cast_skill).
        "thuy_vi_charges",
        "thuy_vi_bypass_pct",
        # Lưu Ly Tịnh Hỏa — per-roll cleanse chance (read by the periodic
        # hook in CombatSession._process_luu_ly_tinh_hoa).
        "luu_ly_cleanse_chance",
        # Lưu Tinh Cản Nguyệt — refresh-hook config (read by
        # CombatSession._refresh_luu_tinh_can_nguyet at start of each turn).
        "_lt_base_spd_pct",
        "_lt_per_dot_evasion",
        "_lt_per_dot_spd_pct",
    ):
        result.pop(cfg_key, None)
    return result


def get_periodic_damage(
    combatant: "Combatant", rng: random.Random | None = None,
) -> list[tuple[str, int, bool]]:
    """Return list of (effect_key, damage, is_crit) for all active DoT effects.

    Policy: filters out non-DoT effects and poison on poison-immune holders,
    then delegates the per-tick math to
    ``src.game.engine.damage.dot.calculate_dot_damage``.

    Per-instance overrides (``effect_overrides[key]['dot_pct'/'dot_element']``)
    are folded into the meta passed downstream so a skill that stamps
    DebuffThieuDot with ``dot_pct=0.08`` ticks for 2× the meta default.
    """
    from src.game.engine.damage.dot import calculate_dot_damage

    rng = rng or random.Random()
    results: list[tuple[str, int, bool]] = []
    for effect_key in list(combatant.effects.keys()):
        meta = EFFECTS.get(effect_key)
        if not meta:
            continue
        override = combatant.effect_overrides.get(effect_key) or {}
        effective_meta = _meta_with_override(meta, override)
        # Gate accepts classic %HP DoTs (``dot_pct > 0``), stack-based DoTs
        # (``stack_kind`` set, dot_pct stays 0 in the data because the tick
        # comes from per-stack counters), and caster-stat-driven DoTs
        # (``dot_caster_hp_pct`` / ``dot_caster_matk_scale`` set — Lục Hồn
        # Chú-class curses where the formula reads the applier's stats).
        if (
            effective_meta.dot_pct <= 0
            and not effective_meta.stack_kind
            and effective_meta.dot_caster_hp_pct <= 0
            and effective_meta.dot_caster_matk_scale <= 0
        ):
            continue
        if effect_key == EffectKey.DEBUFF_DOC_TO and combatant.poison_immunity:
            continue
        dmg, is_crit = calculate_dot_damage(combatant, effect_key, effective_meta, rng)
        results.append((effect_key, dmg, is_crit))
    return results


def _meta_with_override(meta: EffectMeta, override: dict) -> EffectMeta:
    """Return a copy of ``meta`` with override values applied.

    Only ``dot_pct`` and ``dot_element`` are spliced here — stat_bonus
    overrides are handled in ``get_combat_modifiers`` so they don't have
    to allocate a new EffectMeta on every modifier query.
    """
    if not override or not any(k in override for k in ("dot_pct", "dot_element")):
        return meta
    from dataclasses import replace
    return replace(
        meta,
        dot_pct=float(override.get("dot_pct", meta.dot_pct)),
        dot_element=override.get("dot_element", meta.dot_element),
    )


def check_cc_skip_turn(
    combatant: "Combatant", rng: random.Random
) -> str | None:
    """Return the CC effect key that causes the combatant to skip their turn.

    Returns None if the combatant can act normally.
    DebuffTeLiet (paralysis) has a 50% chance to skip.
    All other skips_turn effects are deterministic.
    """
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if not meta:
            continue
        if meta.skips_turn:
            return effect_key
        if effect_key == EffectKey.DEBUFF_TE_LIET and rng.random() < 0.50:
            return effect_key
    return None


def check_prevents_skills(combatant: "Combatant") -> str | None:
    """Return the CC effect key that prevents skill use, or None."""
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if meta and meta.prevents_skills:
            return effect_key
    return None


def check_attack_miss(combatant: "Combatant", rng: random.Random) -> bool:
    """Return True if the attacker's strike misses due to DebuffLoaMat.

    Mirrors the DebuffTeLiet pattern (probabilistic skip), but applied to
    the attack swing instead of the turn — the attacker still pays MP/CD,
    the swing simply fails to land. Single roll per swing.
    """
    if EffectKey.DEBUFF_LOA_MAT in combatant.effects:
        return rng.random() < BLIND_MISS_CHANCE
    return False


def format_active_effects(combatant: "Combatant") -> str:
    """Format active effects for display in Discord embeds."""
    if not combatant.effects:
        return "—"
    parts: list[str] = []
    for key, turns in combatant.effects.items():
        meta = EFFECTS.get(key)
        name = meta.vi if meta else key
        emoji = meta.emoji if meta else "❓"
        parts.append(f"{emoji}{name}({turns}t)")
    return " ".join(parts)


# Linh Căn combat procs have moved to src.game.engine.linh_can_effects
# (per-element modules + package-level orchestrators). Combat imports them
# directly from there — nothing in this file references them anymore.
