"""Combatant dataclass — live combat state for a player or enemy."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# Stack kind → EffectMeta key used to look up the cap via
# ``effective_stack_cap`` (which folds in EffectMeta defaults, per-instance
# ``effect_overrides[<key>].stack_cap``, and ``stack_cap_bonuses`` from
# gear/constitutions/linh_can). Kinds whose cap comes from a different
# source (mana → ``self.mana_stack_cap``; lietdiem_van_hoa →
# ``self.lietdiem_van_hoa_cap``) are absent here and short-circuit inside
# ``Combatant.add_stack``. Adding a new stack kind is one entry here plus a
# Combatant ``_stacks`` field — no helper method needed.
_STACK_EFFECT_KEY: dict[str, str] = {
    "burn":       "DebuffThieuDot",
    "bleed":      "DebuffChayMau",
    "shock":      "DebuffSocDien",
    "poison":     "DebuffDocTo",
    "chan_hoa":   "DebuffChanHoa",
    "nghiep_hoa": "DebuffNghiepHoa",
    "u_minh":     "DebuffUMinh",
    "hoa_van":    "DebuffHoaVan",
    "phuong_hoa": "DebuffPhuongHoa",
    "thuy_mark":  "DebuffNhuocThuyAn",
    "cuu_khuc":   "DebuffCuuKhuc",
    "sat_an":     "DebuffSatAn",
    "phong_nhan_thuc": "DebuffPhongNhanThuc",
    "tran_son_ha":     "DebuffTranSonHa",
    "loi_kiep_an":     "DebuffLoiKiepAn",
    "sat_khi":         "BuffSatKhi",
    "bach_kim":        "BuffBachKimPhongVu",
    "shadow":          "BuffMaKhi",
}


# Legacy effect-key → Combatant fields zeroed when that effect expires (the
# pre-declarative stack / charge counters). New stacking statuses use
# ``effect_stacks`` + ``EffectMeta.stackable`` instead and need NO row here;
# migrate an entry onto that path to delete its row. ``tick_effects`` walks
# this table instead of a hand-written ``if`` per effect.
_LEGACY_EXPIRE_RESETS: dict[str, tuple[str, ...]] = {
    "DebuffNghiepHoa":          ("nghiep_hoa_stacks",),
    "DebuffNghiepHoaHongLien":  ("nghiep_hoa_stacks",),
    "DebuffCuuKhuc":            ("cuu_khuc_atk_reduce_active",
                                 "cuu_khuc_res_shred_active"),
    "BuffPhuDao":               ("phu_dao_altitude",),
    "BuffVoTuongPhong":         ("vo_tuong_phong_charges",),
}


class _StackProxy:
    """Descriptor backing a legacy ``<kind>_stacks`` attribute with the unified
    ``effect_stacks`` store, keyed by the effect that owns the stacks.

    Lets every existing ``combatant.<kind>_stacks`` read/write site keep working
    verbatim while the storage moves into ``effect_stacks`` — so the count is
    capped, scaled (``stat:``/``stack:`` rules), and cleared on the owning
    effect's expiry through one generic path, no bespoke field needed. A zero
    write removes the key to keep ``effect_stacks`` sparse (so ``stacks_of`` and
    membership checks stay clean).
    """
    __slots__ = ("_key", "_store")

    def __init__(self, key: str, store: str = "effect_stacks") -> None:
        self._key = key
        self._store = store

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return getattr(obj, self._store).get(self._key, 0)

    def __set__(self, obj, value) -> None:
        d = getattr(obj, self._store)
        v = int(value)
        if v:
            d[self._key] = v
        else:
            d.pop(self._key, None)


@dataclass
class Combatant:
    """Live combatant state during combat — player or enemy."""
    key: str
    name: str
    hp: int
    hp_max: int
    mp: int
    mp_max: int
    spd: int
    element: Optional[str]
    resistances: dict[str, float] = field(default_factory=dict)
    # Attack and defense stats — computed from cultivation path progression
    atk: int = 0       # Physical attack power (Luyện Thể path gives most ATK)
    matk: int = 0      # Magic attack power (Luyện Khí path gives most MATK)
    def_stat: int = 0  # Physical defense (Luyện Thể path gives most DEF)
    crit_rating: int = 0
    crit_dmg_rating: int = 0
    evasion_rating: int = 0
    crit_res_rating: int = 0
    # Counter to defender evasion — see engine/damage/evasion.check_evasion.
    accuracy_rating: int = 0
    final_dmg_bonus: float = 0.0
    # Damage reduction on received hits (from formation/constitution)
    final_dmg_reduce: float = 0.0
    # Per-turn HP regen as fraction of hp_max (e.g. 0.02 = 2%/turn)
    hp_regen_pct: float = 0.0
    # Per-turn flat HP regen, stacks with hp_regen_pct
    hp_regen_flat: int = 0
    # Per-turn MP regen as fraction of mp_max (e.g. 0.03 = 3%/turn)
    mp_regen_pct: float = 0.0
    # Per-turn flat MP regen, stacks with mp_regen_pct
    mp_regen_flat: int = 0
    # Healing effectiveness multiplier (from Quang Linh Căn passive)
    heal_pct: float = 0.0
    # Cooldown reduction multiplier (e.g. 0.15 → CD * 0.85)
    cooldown_reduce: float = 0.0
    # Fractional CDR carry. Each ``set_cooldown`` accumulates the fractional
    # turn savings (``turns * cooldown_reduce``) here and only refunds whole
    # turns when the accumulator crosses 1.0. Keeps savings linear over time
    # — a 12% CDR truly saves ~12% of total cooldown turns instead of being
    # gated by integer-truncation step thresholds. Persists for the fight.
    cdr_credit: float = 0.0
    # On-hit proc chances (from threshold bonuses)
    burn_on_hit_pct: float = 0.0
    slow_on_hit_pct: float = 0.0
    paralysis_on_crit: bool = False
    # Cast-trigger chance for the freeze proc. Mirror proc on reflect scales
    # by ``_FREEZE_MIRROR_BOOST`` (in procs.py) — reflected freeze fires
    # harder than the original cast.
    freeze_on_skill_chance: float = 0.0
    poison_immunity: bool = False
    # Flat chance (0.0–1.0) to resist any incoming debuff or CC proc.
    # e.g. 0.20 → 20% of debuffs that would land are blocked.
    # Stacks additively from formation, constitution, and linh_can bonuses.
    debuff_immune_pct: float = 0.0
    # Total immunity to hard CC (stun/freeze/silence/interrupt/knock-up).
    # Used by world bosses — soft debuffs (slow, DoT, armor break) still apply.
    immune_hard_cc: bool = False
    # True for persistent world-boss Combatants. Prevents any mechanic from
    # mutating the boss's ``hp_max`` (e.g. Âm Hồn Phệ soul-drain) so the
    # shared HP pool stays authoritative and can't be trivialized by a
    # single attack's local-sim mutations.
    is_world_boss: bool = False
    # Enemy rank string from the enemy JSON ("pho_thong"/"cuong_gia"/"hung_manh"
    # /"tinh_anh"/"dai_nang"/"chi_ton"/beast specials). Empty for players and
    # summons — rank-gated mechanics (Thiên Kiếp execute) treat "" as exempt,
    # so players can never be executed in the arena.
    rank: str = ""
    # Blocks any mechanic that permanently mutates the target's stats —
    # currently Âm Hồn Phệ soul-drain (hp_max shrink) and Đạo Pháp Thôn Phệ
    # stat-steal (atk/matk/def_stat siphon). World bosses get this implicitly
    # via ``is_world_boss``; chi-tôn dungeon bosses (Chung Yên) opt in via
    # JSON to keep their cosmic-tier resistance from being whittled down.
    immune_stat_mutation: bool = False
    # Immunity / special flags
    active_flags: dict[str, bool] = field(default_factory=dict)
    # Active effects: effect_key → turns_remaining
    effects: dict[str, int] = field(default_factory=dict)
    # Per-instance magnitude overrides applied on top of the EffectMeta
    # defaults. Keyed by effect_key; the inner dict mirrors the subset of
    # EffectMeta fields a skill may override (stat_bonus, dot_pct,
    # dot_element). Read by ``effects.get_combat_modifiers`` and the DoT
    # tick path. Cleared automatically when the effect expires.
    effect_overrides: dict[str, dict] = field(default_factory=dict)
    # Declarative stack counts for ``stackable`` effects, keyed by effect key.
    # The single home for stacks of any effect that opts into generic stacking
    # (``EffectMeta.stackable``): ``apply_effect`` banks here (capped at
    # ``effective_stack_cap``) and ``tick_effects`` clears the entry on expiry —
    # no bespoke ``<kind>_stacks`` field required.
    effect_stacks: dict[str, int] = field(default_factory=dict)
    # Free-floating runtime stack counters — NOT bound to a held effect's
    # lifetime (own bespoke reset logic). Same descriptor mechanism, a
    # separate store so the effect-expiry sweep never touches them.
    stack_counters: dict[str, int] = field(default_factory=dict)
    # Skill cooldowns: skill_key → turns_remaining
    cooldowns: dict[str, int] = field(default_factory=dict)
    skill_keys: list[str] = field(default_factory=list)
    # Per-fight cast counter — drives the ``usage_limit`` field on skill
    # JSONs. Incremented in ``cast_skill`` for top-level casts only
    # (multi-hit follow-ups and chained skills don't bump the counter).
    skill_usage_count: dict[str, int] = field(default_factory=dict)
    # Per-fight non-DoT hit counter on this combatant — drives the
    # ``proc_on_hits_taken`` field on skill JSONs (e.g. Hư Vô fires every 4
    # incoming hits). Incremented inside ``take_damage`` whenever a non-DoT
    # call is made; DoT ticks are ignored so passive bleed/burn/poison can't
    # game the counter.
    hits_taken: int = 0
    # Per-round damage-taken accumulator. Reset to 0 at the start of every
    # ``CombatSession.step()``; ``Combatant.take_damage`` increments it by
    # the HP-applied amount on every non-DoT and DoT call. Drives
    # ``proc_on_heavy_hit_pct`` skills that fire when the holder's cumulative
    # damage in the round crosses a fraction of their ``hp_max``.
    damage_taken_this_turn: int = 0
    # Bất Diệt Hỏa Chủng — two-tick retaliation counter. Set to 2 when the
    # heavy-hit passive triggers; ``_process_periodic`` decrements it each
    # round and fires the delayed retaliation (burst + enemy stun) when it
    # transitions from 1 to 0. The skill key driving the retaliation lives
    # on the holder's ``skill_keys`` — the periodic hook looks it up there
    # to read ``retaliate_dmg_pct_max_hp`` / ``retaliate_stun_turns``.
    bat_diet_retaliate_pending: int = 0
    # Per-fight evade counter on this combatant — drives the
    # ``proc_on_target_evades`` field on skill JSONs (e.g. Bắc Minh Hữu Ngư
    # fires when the opponent has dodged 3 incoming hits). Incremented in
    # ``cast_skill`` whenever an attack on this combatant resolves as evaded.
    evades_count: int = 0
    # Formation skills — one entry per active formation slot. Fire in PARALLEL
    # each turn after the main skill (Trận Tu multi-formation simultaneity):
    # every formation that's off cooldown + can afford MP pings the target
    # independently, so 3 active formations = up to 4 attacks per turn
    # (main + 3 formations). Stored separately from ``skill_keys`` so they
    # don't compete with the player's chosen rotation.
    formation_skill_keys: list[str] = field(default_factory=list)
    # Linh Căn keys (spiritual roots) + per-element progression level (1..9).
    # ``linh_can`` stays as a bare list so existing membership checks
    # (``"kim" in actor.linh_can``) keep working; ``linh_can_levels`` is what
    # the effect modules read for proc-chance / potency scaling.
    linh_can: list[str] = field(default_factory=list)
    linh_can_levels: dict[str, int] = field(default_factory=dict)
    # Per-skill mastery levels: skill_key → level (1..25). Empty for
    # enemies/bosses → power_mult resolves to 1.0 (inert).
    skill_mastery: dict[str, int] = field(default_factory=dict)
    # Thổ Hộ Thể shield (absorbs damage)
    shield: int = 0
    ho_the_used: bool = False

    # ── Fire DoT build support ────────────────────────────────────────────────
    # Burn stacks: multiple stacks pile on the same target, each contributing
    # per-stack damage to the DebuffThieuDot tick. Stacks decay when the burn
    # effect fully expires. Stack cap is read from ``EffectMeta.stack_cap``
    # (default 5) via ``effective_stack_cap`` — gear / constitution / Linh
    # Căn bonuses (``dot_stack_cap_bonus: {burn: N}``) flow through
    # ``stack_cap_bonuses["DebuffThieuDot"]``.
    burn_stacks = _StackProxy("DebuffThieuDot")
    # Bonus damage multiplier vs targets that currently have burn stacks.
    # e.g. 0.25 → +25% final damage if target has any burn stack.
    bonus_dmg_vs_burn: float = 0.0
    # Whether DoTs ticking on the opposing combatant can roll crits.
    # Set on the attacker; combat engine reads the attacker's flag each tick.
    dot_can_crit: bool = False
    # Per-stack burn damage fraction of hp_max (applied by get_periodic_damage).
    burn_per_stack_pct: float = 0.005

    # Tam Muội Chân Hỏa — Daoist "true fire" stack. Each stack ticks 2 % hp_max
    # as fire DoT (driven by ``stack_kind="chan_hoa"`` in dot.py) AND adds a
    # flat per-stack amp to *every* fire-element DoT on the holder via
    # ``_dot_amp``. Stack cap rides on ``EffectMeta.stack_cap`` (read via
    # ``effective_stack_cap``); per-stack pct/amp flow attacker → target via
    # ``_propagate_stack_build`` so attacker gear/build still influences the tick.
    chan_hoa_stacks = _StackProxy("DebuffChanHoa")
    chan_hoa_per_stack_pct: float = 0.02
    chan_hoa_per_stack_fire_amp: float = 0.04

    # Hồng Liên Nghiệp Hỏa — karma-fire stacks. While the holder carries
    # ``DebuffNghiepHoaHongLien``, every incoming debuff/CC pushes a stack
    # onto ``DebuffNghiepHoa`` (3 % hp_max fire DoT per stack). Stack cap
    # rides on ``EffectMeta.stack_cap`` (99 as a safety rail).
    nghiep_hoa_stacks = _StackProxy("nghiep_hoa", store="stack_counters")
    nghiep_hoa_per_stack_pct: float = 0.03

    # U Minh Quỷ Hỏa — underworld ghost-fire mark. Pure MP-burn DoT: each
    # stack drains ``u_minh_per_stack_mp_pct × mp_max`` from the holder per
    # turn for as long as DebuffUMinh is active. No HP damage. Stacks clear
    # when the debuff expires. Cap from ``EffectMeta.stack_cap``.
    u_minh_stacks = _StackProxy("DebuffUMinh")
    u_minh_per_stack_mp_pct: float = 0.05

    # Hỏa Vân — fire-cloud mark. Stack counter consumed by the
    # ``SkillAtkHoaVanSauThienKiem_R7`` finisher (auto-cast on 5 stacks).
    # No DoT damage; stacks just track combo state. Cap from
    # ``EffectMeta.stack_cap``; clears when ``DebuffHoaVan`` expires.
    hoa_van_stacks = _StackProxy("DebuffHoaVan")

    # Phượng Hỏa — phoenix-fire mark reflected onto attackers by the
    # BuffPhuongHoangChanHoa defensive aura. Each stack ticks a fire DoT
    # (``phuong_hoa_per_stack_pct × hp_max``) and shaves 10% off the holder's
    # incoming healing (folded via the per-stack heal-reduce placeholder in
    # get_combat_modifiers). Stacks clear when DebuffPhuongHoa expires.
    # Cap from ``EffectMeta.stack_cap``.
    phuong_hoa_stacks = _StackProxy("DebuffPhuongHoa")
    phuong_hoa_per_stack_pct: float = 0.02

    # Lưu Ly Tịnh Hỏa — cleanse counter. Each successful cleanse pulse from
    # the BuffLuuLyTinhHoa aura (one roll per distinct fire DoT kind on the
    # opponent) bumps this counter, which feeds the per-stack crit_res
    # scaling rule. The cap rides on ``EffectMeta.stack_cap`` (read via
    # ``effective_stack_cap``) since there's no gear/build hook scaling it.
    # Counter resets to 0 when the buff expires.
    luu_ly_tinh_hoa_stacks = _StackProxy("BuffLuuLyTinhHoa")

    # Generic flat stack-cap bonuses, keyed by effect_key. Gear / constitution
    # / Linh Căn / formation effects all write here at character-build time
    # via ``character_stats.py`` — ``effective_stack_cap`` sums the entry on
    # top of the meta cap + per-instance override. Designed for the migrated
    # caps (chan_hoa/nghiep_hoa/u_minh/phuong_hoa/thuy_mark/cuu_khuc/luu_ly)
    # that don't carry a dedicated ``<kind>_stack_cap`` field. Burn/bleed/
    # shock/poison keep their own ``<kind>_stack_cap`` field instead.
    stack_cap_bonuses: dict[str, int] = field(default_factory=dict)
    # Solar aura — every turn, deal hp_max × pct as fire damage to opponent.
    # Independent of skill actions and DoTs; benefits from final_dmg_bonus,
    # dot_dmg_bonus_by_kind["burn"], bonus_dmg_vs_burn, and shreds opponent's
    # hoa resist.
    solar_aura_pct: float = 0.0
    # Wither aura — every turn, deal hp_max × pct as moc damage to opponent
    # and leech the dealt amount back as healing on the holder. Benefits from
    # final_dmg_bonus + dot_dmg_bonus, shreds moc resistance.
    wither_aura_pct: float = 0.0
    # Niết Bàn Trùng Sinh — once-per-combat revive. When the holder would die
    # the first time, restore HP to ``phoenix_revive_pct × hp_max`` (e.g. 0.70
    # = 70%) and grant a permanent (rest-of-combat) buff:
    #   atk/matk/def_stat × (1 + phoenix_revive_buff_pct)
    #   final_dmg_bonus  += phoenix_revive_buff_pct
    #   final_dmg_reduce += phoenix_revive_buff_pct (capped at MAX_FINAL_DMG_REDUCE)
    # The flag ``phoenix_revive_used`` is flipped on trigger and prevents
    # second revives from any other source within the same fight.
    phoenix_revive_pct: float = 0.0
    phoenix_revive_buff_pct: float = 0.0
    phoenix_revive_used: bool = False

    # Bách Thể Chú Linh — Hóa Hình (all nine parts sharing one bloodline).
    # Build-time config from ``body_parts.hoa_hinh_form``: once per battle,
    # dropping below the trigger HP% transforms the holder — heals a chunk
    # and stamps ``hoa_hinh_buff_key`` (see combat/hoa_hinh.py).
    hoa_hinh_buff_key: str = ""
    hoa_hinh_beast_vi: str = ""
    hoa_hinh_used: bool = False  # runtime once-per-battle latch
    # Chân Mệnh Lôi Phù once-per-fight flag — flipped on the Lôi passive
    # destiny revival (``_try_chan_menh_loi_phu``). Independent from phoenix /
    # buff revives so a player carrying multiple revive sources gets the full
    # cascade in priority order (phoenix → buff revive → Lôi Phù).
    chan_menh_loi_phu_used: bool = False
    # Thôn Thiên Ma Khí — at combat start, drain ``pct`` of the opponent's
    # core combat stats (atk/matk/def/spd) and add the same amount to the
    # holder. Applied once per fight; flag below tracks consumption so the
    # transfer doesn't compound on every step() call.
    stat_drain_aura_pct: float = 0.0
    stat_drain_aura_applied: bool = False
    # Lục Dục Thiên Ma Vũ — auto-cycling six-desires passive. Counter ticks
    # down during the post-amp expired phase; while > 0 the cycle hook skips
    # gain/amp logic. Driven by ``SkillAmLucDucThienMaVu_R9`` ownership in the
    # holder's skill_keys; the cycle hook lives in
    # ``CombatSession._tick_luc_duc_thien_ma_vu``.
    luc_duc_expire_turns_left: int = 0
    # Quỷ Ảnh Mê Tung — every successful evade bumps this counter (cap 3),
    # multiplying the holder's per-stack evasion + spd contribution from the
    # ``BuffQuyAnhMeTung`` marker. ``get_combat_modifiers`` expands the
    # per-stack stat_bonus values into concrete ``evasion_rating`` /
    # ``spd_pct`` contributions when this counter is > 0.
    quy_anh_stacks = _StackProxy("quy_anh", store="stack_counters")
    # Thánh Tuyền Thể — only ``damage_defer_pct`` of incoming damage is
    # deferred; the rest (``1 - damage_defer_pct``) is taken immediately.
    # The deferred portion is split into ``damage_defer_turns`` chunks queued
    # in ``deferred_damage_queue`` and paid out one per turn-end (FIFO).
    # Both fields must be > 0 for the mechanic to engage. Total damage is
    # preserved — only its timing changes. Multiple hits append to the queue.
    damage_defer_turns: int = 0
    damage_defer_pct: float = 0.0
    deferred_damage_queue: list[int] = field(default_factory=list)
    # Hào Quang Củng Cố (Fortify Aura) — Hoang Cổ Thánh Thể Chain 9 payoff.
    # ``fortify_per_turn_pct`` × ``fortify_stacks`` is added to BOTH
    # ``final_dmg_bonus`` and ``final_dmg_reduce`` while in combat.
    # ``fortify_stacks`` ticks up by 1 each periodic-phase, capped at
    # ``fortify_stack_cap``. After non-DoT damage lands, ``fortify_braced_turns``
    # is set so the holder gets an extra ``fortify_post_hit_dr_pct`` of damage
    # reduction for the next incoming hit's resolution turn.
    fortify_per_turn_pct: float = 0.0
    fortify_stack_cap: int = 0
    fortify_post_hit_dr_pct: float = 0.0
    fortify_stacks = _StackProxy("fortify", store="stack_counters")
    fortify_braced_turns: int = 0
    # Loot economy passives — additive on top of the session's baseline
    # ``loot_qty_multiplier`` / ``loot_luck_pct`` (elite roll, dungeon grade).
    # Both fields are read by ``CombatSession._roll_loot``.
    loot_qty_bonus: float = 0.0
    loot_luck_bonus: float = 0.0
    # Hậu Thổ Phong Ma Trận — formation gem-ladder lane percents (permanent
    # contribution, layered on top of the buff's stat_bonus lanes which the
    # rider also reads). See ``_hau_tho_tank_conversion_rider``.
    bonus_base_dmg_per_self_hp_pct: float = 0.0
    bonus_base_dmg_per_self_shield_pct: float = 0.0
    bonus_base_dmg_per_self_def_pct: float = 0.0
    # Thổ Nguyên Hộ Pháp Trận — armor-extension lane (permanent contribution
    # from the formation's gem ladder, layered with the buff's stat_bonus
    # of the same key). Read by ``build_defense_stats``.
    def_applies_to_elemental_pct: float = 0.0
    # Generic per-element bonus dicts. The unified ``element_pen`` /
    # ``element_dmg_bonus`` / ``damage_taken_convert_pct`` / ``dmg_taken``
    # shape replaces the historical ad-hoc per-element scalar fields with
    # element-keyed dicts that any constitution / equipment can target:
    #
    #   damage_taken_convert_pct["hoa"] = 0.40
    #     → 40% of damage taken is reclassified as hoa damage and mitigated
    #       by the holder's own hoa resistance (Đế Liệt Diệm pattern).
    #
    #   element_dmg_bonus["hoa"] = 0.20
    #     → +20% final damage on outgoing hits whose skill element is hoa.
    #
    # Multiple equipped sources merge additively per element via the dict
    # merge in ``_merge_bonus_dict``.
    damage_taken_convert_pct: dict[str, float] = field(default_factory=dict)
    element_dmg_bonus: dict[str, float] = field(default_factory=dict)
    # Per-element soft-cap lifter for resistance. Without an entry here, the
    # player's effective ``res_<elem>`` caps at ``RES_SOFT_CAP`` (0.75). Each
    # 0.01 raises the cap one-for-one, up to ``MAX_ELEMENTAL_RES`` (0.90).
    # Read via ``effective_res_cap``; gear / constitution / Linh Căn / buff
    # stat_bonus all contribute additively (gear via this dict, buffs via
    # ``<element>_max_resist_bonus`` stat_bonus keys folded by
    # ``get_combat_modifiers``). Enemies bypass this — their cap stays at
    # ``MAX_ELEMENTAL_RES`` (or whatever their ``res_cap_pct`` JSON declares).
    element_max_resist_bonus: dict[str, float] = field(default_factory=dict)
    # Per-element MP cost multiplier — extra MP cost (additive on top of base
    # 1.0×) charged when casting a skill of the matching element. E.g.
    # ``element_mp_cost_mult["thuy"] = 2.0`` makes every thuy cast pay
    # 3× MP (1.0 base + 2.0 extra). Because the damage formula is
    # ``DMG = base + mp_cost``, the extra MP also amplifies damage
    # proportionally — used by Thiên Nhất Sinh Thủy Trận. Multiple sources
    # (formation base + threshold tiers) compound additively via
    # ``_merge_bonus_dict``. Missing element ⇒ no scaling (1.0× cost).
    element_mp_cost_mult: dict[str, float] = field(default_factory=dict)
    # Per-element penetration — passive attacker-side stat that lowers the
    # target's effective res when this combatant attacks. Read by
    # build_defense_stats and by the Linh Căn / formation / burst paths via
    # element_pen.get(elem, 0.0). Multiple sources merge additively via
    # _merge_bonus_dict, so multi-element pen composes naturally.
    # NOTE: the *debuff* counterpart is ``DebuffXuyenThau<Elem>`` (turn-duration
    # shred applied to the target via apply_effect), which stacks additively
    # with this passive pen and with DebuffXeRach (res_all).
    element_pen: dict[str, float] = field(default_factory=dict)

    # ── Kim (Bleed) build support ─────────────────────────────────────────────
    # Bleed stacks: like burn, but physical (kim element) and slows healing.
    # Cap via ``EffectMeta.stack_cap`` + ``stack_cap_bonuses["DebuffChayMau"]``.
    bleed_stacks = _StackProxy("DebuffChayMau")
    # Per-stack bleed damage fraction of hp_max.
    bleed_per_stack_pct: float = 0.005
    # When holder has bleed stacks, incoming heals are multiplied by (1 - bleed_heal_reduce).
    # e.g. 0.40 means heals on a bleeding target are reduced by 40%.
    bleed_heal_reduce: float = 0.0
    # On-hit: chance actor applies a bleed stack.
    bleed_on_hit_pct: float = 0.0
    # Conditional crit amps vs targets in specific debuff states — single
    # nested dict (mirrors element_pen / dmg_taken). Outer key = state
    # ("bleed", "marked", "drained"); inner = {"rating": int, "dmg": int}.
    # Read at hit-time in damage/combat_hit.py.
    crit_amp_vs: dict[str, dict[str, int]] = field(default_factory=dict)
    # Sát Thương Chuẩn — % of the hit's *damage* applied as unblockable bonus on
    # each successful hit. Aggregated across constitution, unique gear,
    # gems, skills, and Linh Căn passives. Capped at TRUE_DMG_PCT_CAP in
    # ``engine/damage/true_damage.py``.
    true_dmg_pct: float = 0.0
    # Generic life-steal: heal actor for X% of damage dealt on each direct
    # hit (skill or follow-up multistrike). Routed through ``_apply_heal``.
    life_steal_pct: float = 0.0
    # Hybrid scaling: ``crit_dmg_rating_to_dmg_pct × crit_dmg_rating`` is
    # added as flat damage to the base roll in ``engine/damage/base.py``.
    # Lets crit-damage-stacking constitutions (Phá Thiên Thần Thể) get
    # both the crit-spike multiplier AND a reliable per-hit floor.
    crit_dmg_rating_to_dmg_pct: float = 0.0

    # ── Poison stacks (Mộc / Âm DoT) ─────────────────────────────────────────
    # Mirror of burn/bleed/shock. Each application of DebuffDocTo adds one
    # stack; the DoT tick scales with stacks × ``poison_per_stack_pct`` ×
    # max(atk, matk) × DOT_POWER_COEF. Stacks reset to 0 when the
    # DebuffDocTo effect fully expires. Cap via ``EffectMeta.stack_cap`` +
    # ``stack_cap_bonuses["DebuffDocTo"]``.
    poison_stacks = _StackProxy("DebuffDocTo")
    poison_per_stack_pct: float = 0.008

    # ── Mộc (Wood / Poison Leech) build ──────────────────────────────────────
    # When any DoT ticks on the opposing combatant, this combatant leeches
    # this fraction of the tick damage as HP + MP.
    dot_leech_pct: float = 0.0
    # Damage-from-heal: every heal this combatant receives queues
    # heal × damage_from_heal_pct bonus damage on their next hit.
    damage_from_heal_pct: float = 0.0
    queued_heal_dmg: int = 0
    # Damage scaling with hp_max: flat damage bonus = hp_max × this per attack.
    damage_bonus_from_hp_pct: float = 0.0

    # ── Thủy (Water / Mana) build ────────────────────────────────────────────
    # Fraction of incoming damage reflected back to the attacker after HP loss.
    reflect_pct: float = 0.0
    # When True, the defender's own on-hit procs (freeze, slow, etc.)
    # fire on the attacker as part of the reflected hit — the mirror carries
    # not just damage but the defender's build effects.
    reflect_applies_effects: bool = False
    # Flat damage bonus per attack = mp_max × this. Grows damage with mana pool.
    damage_bonus_from_mp_pct: float = 0.0
    # On-hit: leech this fraction of damage dealt as MP for the attacker.
    mp_leech_pct: float = 0.0
    # Mana-stack accumulator. Gained per attack/skill cast; each stack adds
    # mana_stack_dmg_bonus × final_dmg_bonus, or can be burst-consumed.
    mana_stacks = _StackProxy("mana", store="stack_counters")
    mana_stack_cap: int = 10
    mana_stack_per_attack: int = 0      # passive: stacks gained each turn
    mana_stack_dmg_bonus: float = 0.0   # per-stack final-damage bonus

    # ── Energy Shield (Thổ + new equipment/constitution paths) ─────────────
    # PoE-style Energy Shield: ``shield`` absorbs incoming damage BEFORE HP
    # (Combatant.take_damage handles this when ``is_dot=False``). DoT damage
    # bypasses the shield — direct damage doesn't.
    #
    # Shield cap is computed by ``shield_cap()`` from three contributions:
    #   shield_max_base   — flat baseline from equipment implicit (e.g. shield base)
    #   shield_max_flat   — additive flat from affixes/constitutions
    #   shield_max_pct    — multiplicative on the base+flat sum
    #
    # ``shield_regen_pct`` is a fraction of the holder's own ``shield_cap()``
    # restored each periodic phase. Scaling off shield_cap (not hp_max)
    # keeps regen proportional to actual shield investment.
    # ``shield_regen_flat`` adds on top.
    # Turn-based combat regenerates every turn; ``shield_recharge_pause`` is
    # preserved as a knob for build-specific delays but defaults to 0.
    shield_regen_pct: float = 0.0
    shield_regen_flat: int = 0
    shield_max_base: int = 0
    shield_max_flat: int = 0
    shield_max_pct: float = 0.0
    # HP→Shield conversion is resolved at compute_combat_stats time (the
    # holder's hp_max is already shrunk and shield_max_base bumped before the
    # Combatant is built). The field is kept on Combatant so introspection /
    # status embeds can show "30% HP converted to Shield".
    hp_to_shield_pct: float = 0.0
    # Per-attack MATK / ATK bonus = current_shield × this fraction. Read in
    # build_attack_stats so depleted shield = depleted punch. Symmetric pair
    # so mage and physical builds can both pivot shield into offense.
    matk_from_shield_pct: float = 0.0
    atk_from_shield_pct: float = 0.0
    # Endure — Đế Sinh Mộc Thể's "Cội Nguồn Bất Tận". When a hit would drop
    # HP to 0, the engine clamps HP to ``hp_max × endure_threshold_pct`` and
    # engages a cooldown. ``endure_remaining`` is the runtime turn counter,
    # decremented in CombatSession._process_periodic.
    endure_threshold_pct: float = 0.0
    endure_cooldown: int = 0
    endure_remaining: int = 0
    # Transient flag set inside ``take_damage`` when Endure clamps HP to the
    # survival floor. CombatSession reads + logs + resets it during periodic
    # processing so the player sees a clear "Endure triggered" line.
    endure_just_triggered: bool = False
    # Cleanse Heal — Đế Bạch Liên Thể's "Liên Hoa Tịnh Hóa". Each successful
    # cleanse restores ``hp_max × cleanse_heal_pct`` HP. Hooks into the same
    # quang.try_cleanse path as barrier_on_cleanse.
    cleanse_heal_pct: float = 0.0
    # Cleanse Retaliate — Đế Tịnh Quang Thể's "Tịnh Hóa Phản Đòn". Each
    # successful cleanse fires ``matk × this_pct`` Quang-flavored damage at
    # the cleanser's opponent. Resolved in CombatSession after try_cleanse.
    cleanse_retaliate_dmg_pct: float = 0.0
    # Kill Streak — Đế Sát Kim Thể's "Sát Khí Đại Thành". Each enemy killed
    # grants permanent +``kill_buff_per_kill_pct`` final_dmg_bonus, capped at
    # ``kill_buff_cap`` stacks. ``kill_streak_stacks`` is the runtime count
    # incremented in CombatSession._victory(). Persists across waves of a
    # single dungeon entry; resets when player_c is rebuilt.
    kill_buff_per_kill_pct: float = 0.0
    kill_buff_cap: int = 0
    kill_streak_stacks = _StackProxy("kill_streak", store="stack_counters")
    # Multi-Strike — per-attack chance to land an additional hit at reduced
    # damage. Resolved in casting.py after the main hit; the second strike
    # reuses the same skill's ``take_damage`` path so DR/shield/resists all
    # apply naturally. Theme: lightning combos, sword storm, wind cascade.
    multi_strike_pct: float = 0.0
    multi_strike_dmg_pct: float = 0.50
    # Extra echoes for ``per_hit_followup`` formation skills. Threshold-only
    # bonus (e.g. Thập Nhị Đô Thiên Thần Sát's 10-gem tier).
    formation_echo_bonus: int = 0
    # Multiplier on a ``per_hit_followup`` formation skill's ``base_dmg``.
    # Sourced from gem thresholds on the matching formation; gated to
    # passive echo skills only.
    formation_skill_dmg_bonus: float = 0.0
    # Bumps effective summon ``limit`` (Thiên Giới Thẩm Phán tier-10 lets
    # 2 Đại Thiên Sứ coexist instead of 1). Read by maybe_spawn_summon.
    summon_limit_bonus: int = 0
    shield_recharge_delay: int = 0
    shield_recharge_pause: int = 0
    # Every hit adds flat damage = current shield × this fraction.
    damage_bonus_from_shield_pct: float = 0.0
    # Thorn: fraction of incoming damage reflected as physical thorn damage.
    # Differs from reflect_pct by always dealing physical kim-style damage
    # regardless of defender element.
    thorn_pct: float = 0.0
    # Shield takes the reflected damage before thorn bleeds into HP.
    thorn_from_shield: bool = False
    # On-hit: chance to stun the target (any hit, not just bạo kích).
    stun_on_hit_pct: float = 0.0

    # ── Kim (Metal / Killing-Aura) build — Thiên Cương Phá Sát Thể ────────────
    # Sát Khí stacks — one added per non-Kiếm-Lãng hit while the holder carries
    # ``BuffSatKhi`` (capped at 5 via ``_STACK_EFFECT_KEY``). Each stack folds
    # +crit_rating / +crit_dmg_rating in via the buff's scaling_rules. Persists
    # for the fight (the buff has duration 999 → never decays mid-combat).
    sat_khi_stacks = _StackProxy("sat_khi", store="stack_counters")
    # L3 Kim Phá Ngọc Toái — base + high-stack on-hit Phá Giáp chances. The
    # high value applies once ``sat_khi_stacks >= 3``. Both 0 → inert (enemies,
    # flag-off, non-Kim bodies).
    kim_pha_giap_on_hit_chance: float = 0.0
    kim_pha_giap_high_sat_khi_chance: float = 0.0
    # L9 Sát Khí Đại Thành — the Kiếm Lãng splash payoff config. Fires only at
    # exactly 5 Sát Khí stacks while ``BuffSatKhiDaiThanh`` is held. Splash
    # chance = min(cap, base + crit_coeff × effective crit chance). All-zero /
    # False defaults keep the gate a no-op everywhere else.
    kim_sword_splash_at_max_sat_khi: bool = False
    kim_sword_splash_base_chance: float = 0.0
    kim_sword_splash_crit_coeff: float = 0.0
    kim_sword_splash_chance_cap: float = 0.0

    # ── Kim (Metal / Evasive Crit-Bleeder) build — Thái Bạch Canh Kim Thể ─────
    # Bạch Kim Phong Vũ stacks — +1 each PERIODIC tick while the opponent is
    # bleeding, capped at 5 via ``_STACK_EFFECT_KEY``. Each stack folds +atk_pct
    # / +bleed_dmg_bonus via the buff's scaling_rules. Never decays.
    bach_kim_stacks = _StackProxy("bach_kim", store="stack_counters")
    # L3 Thái Bạch Túy Tiên — one-shot "+N hit-count on the next cast". Seeded
    # at battle start by the effect-stamp seam; consumed (reset to 0) on the
    # first top-level cast. 0 → inert.
    next_skill_hit_count_bonus: int = 0
    # L9 Huyết Lạp Thái Bạch — anti-bleed crit amps applied when the target is
    # bleeding (threaded into the crit step via AttackStats), plus the
    # every-N-acted-turns guaranteed-crit cadence. All-zero → inert.
    bleed_hunter_crit_chance_bonus: float = 0.0
    bleed_hunter_crit_dmg_bonus: float = 0.0
    bleed_hunter_periodic_interval: int = 0
    bleed_hunter_turn_counter: int = 0
    bleed_hunter_crit_armed: bool = False

    # ── Ám (Shadow / Demon-Trance Mage) build — Huyền Âm Thiên Ma Thể ─────────
    # Ma Khí stacks — +1 per non-stat-steal hit while the holder carries
    # ``BuffMaKhi`` (capped at 6 via ``_STACK_EFFECT_KEY``). At max the L1 proc
    # fires Hồn Phệ + Thực Hồn then resets the counter. Never decays passively.
    shadow_stacks = _StackProxy("shadow", store="stack_counters")
    # L1 gate — set by the body's flat ``shadow_stack_on_hit``. False → the
    # POST_HIT shadow sweep is a no-op (enemies, flag-off, non-Ám bodies).
    shadow_stack_on_hit: bool = False
    # L6 Thiên Ma Đồng Hóa — while ``BuffNhapMa`` is active, +final_dmg and an
    # on-hit Đạo Pháp Thôn Phệ (stat-steal). 0.0 → inert (only L6+ sets it).
    nhap_ma_dmg_bonus: float = 0.0
    # L9 Ma Đạo Hóa Thần — auto-Nhập-Ma cadence: every ``nhap_ma_interval``
    # acted turns, self-apply ``BuffNhapMa`` for ``nhap_ma_duration`` turns.
    # All-zero → the PRE_TURN hook never fires.
    nhap_ma_turn_counter: int = 0
    nhap_ma_interval: int = 0
    nhap_ma_duration: int = 0

    # ── Hỏa (Fire / Phoenix Tank-Mage) build — Chân Dương Bất Diệt Thể ────────
    # L1 burning crit-ramp counter — +1 each PERIODIC tick while the opponent
    # burns, reset to 0 when their burn drops. Read by ``BuffHoaKhiTuongSinh``'s
    # scaling rule (``stack:burning_crit`` → +20 crit_rating/stack, cap +200 =
    # 10 stacks). Manually capped in the periodic hook (not via add_stack), so
    # no ``_STACK_EFFECT_KEY`` entry. Never decays except on the burn-drop reset.
    burning_crit_stacks = _StackProxy("burning_crit", store="stack_counters")
    # L9 Phượng Hoàng Trọng Sinh — 3-charge upgraded revive config. The
    # priority-5 ON_REVIVE hook fires while ``hoa_revive_upgraded`` and charges
    # remain; each revive restores ``hoa_revive_hp_pct_l9`` of hp_max. All-zero
    # / False → the hook is inert and the generic revive seams handle the death.
    hoa_revive_charges: int = 0
    hoa_revive_upgraded: bool = False
    hoa_revive_hp_pct_l9: float = 0.0
    # L6 Chân Hỏa Phần Thiên — per-hit chance to amp damage vs a burning target.
    # Both 0.0 → inert (the cast-assembly roll never fires).
    hoa_burning_amp_chance: float = 0.0
    hoa_burning_amp_pct: float = 0.0

    # ── Thủy (Water / Tidal Counter-Puncher) build — Huyền Thủy Trường Sinh ───
    # The body's OWN Tide reservoir — deliberately ISOLATED from the skill
    # ``SkillAtkThuyTrieuTichLang``'s ``thuy_tide`` pool above so a player who
    # owns both never cross-feeds one into the other. Banks a fraction of damage
    # TAKEN (L1), spent by Glacial Shatter (L6) and the Tidal Flood (L9).
    thuy_intake_reservoir: int = 0
    # L1 Nạp Triều Khố — intake fraction of damage taken + reservoir cap scale.
    thuy_tide_intake_pct: float = 0.0
    thuy_reservoir_cap_matk_scale: float = 0.0
    # L3 Hàn Thủy Đóng Băng — on-hit-taken chance to freeze the attacker.
    thuy_retaliate_freeze_chance: float = 0.0
    # L6 Băng Toái Quyết — fraction of the reservoir released as a Thủy shatter
    # strike when hitting a frozen target.
    thuy_shatter_tide_pct: float = 0.0
    # L9 Hồi Triều Nộ Hải — periodic tidal-flood config. The PERIODIC hook fires
    # every ``thuy_tidal_flood_interval`` rounds, releasing
    # ``thuy_tidal_release_pct × depth`` of the reservoir (depth grows with the
    # round count, capped) and keeping ``thuy_tidal_refill_pct``. All-zero /
    # False → the hook is inert.
    thuy_tidal_flood_enabled: bool = False
    thuy_tidal_flood_interval: int = 0
    thuy_tidal_release_pct: float = 0.0
    thuy_tidal_depth_per_turn: float = 0.0
    thuy_tidal_depth_mult_cap: float = 0.0
    thuy_tidal_refill_pct: float = 0.0

    # ── Mộc (Wood / Poison / Eternal Spring) build — Trường Xuân Linh Mộc ─────
    # L1 Linh Mộc Chi Độc — on-hit poison chance. Rides the generic on-hit proc
    # table exactly like burn_on_hit_pct / bleed_on_hit_pct. 0.0 → never procs.
    poison_on_hit_pct: float = 0.0
    # L3 Mộc Vương Thống Lĩnh — flat final-dmg bonus vs a Làm Chậm target
    # (mirror of bonus_dmg_vs_burn). 0.0 → inert.
    moc_vs_slowed_dmg_bonus: float = 0.0
    # L6 Trường Xuân Hồi Nguyên — heal hp_max × per-debuff each periodic phase,
    # one tick per active enemy debuff up to ``moc_regen_debuff_cap``. Both 0 →
    # the PERIODIC hook is inert.
    moc_regen_per_enemy_debuff: float = 0.0
    moc_regen_debuff_cap: int = 0
    # L9 Trường Xuân Bất Tử — guaranteed poison on every attack (on top of the
    # L1 proc) + the Undying Spring cheat-death. The ON_REVIVE hook fires while
    # ``moc_undying_spring_enabled`` AND the cooldown is ready AND healing isn't
    # choked off (heal_taken_reduce < ``moc_undying_heal_reduce_gate``); it
    # leaves the body at ``moc_undying_min_hp`` and re-arms after
    # ``moc_undying_cooldown_turns`` of the body's own turns. All-zero / False →
    # every arm is inert.
    moc_guaranteed_poison_on_attack: bool = False
    moc_guaranteed_poison_stacks: int = 0
    moc_undying_spring_enabled: bool = False
    moc_undying_cooldown_turns: int = 0
    moc_undying_min_hp: int = 1
    moc_undying_heal_reduce_gate: float = 0.0
    # Runtime-only cooldown counter (NOT a config key, NOT in CombatStats): set
    # to ``moc_undying_cooldown_turns`` when the revive fires, decremented each
    # PRE_TURN by the truong_xuan aura.
    moc_undying_cd: int = 0

    # ── Thổ (Kim Cang Bất Hoại) build ────────────────────────────────────────
    # L1 Đại Địa Căn Cơ — flag: bank a Địa Mạch stack each time the shield
    # regen tick actually grants HP to the shield. False → the regen hook
    # skips the increment entirely (every other body, flag-off path).
    dia_mach_per_regen: bool = False
    # Runtime counter for Địa Mạch stacks (NOT a config key, NOT in
    # CombatStats / _CONSTITUTION_FLAG_FIELDS). Hard-capped at 6 by the regen
    # hook. Each stack contributes +4% shield_max_pct + +0.8% shield_regen_pct
    # via BuffDaiDiaCanCo's scaling_rules (source: "stat:dia_mach_stacks").
    dia_mach_stacks = _StackProxy("dia_mach", store="stack_counters")
    # L3 Kim Thân Hộ Pháp — base physical-negate chance (rolls when shield is
    # at or below the gate). 0.0 → inert (enemies, flag-off, non-Tho bodies).
    tho_phys_immune_chance: float = 0.0
    # L3 elevated negate chance when shield fraction > gate.
    tho_phys_immune_high_shield_chance: float = 0.0
    # L3 shield-fraction threshold (e.g. 0.50 = 50% of shield_cap).
    tho_phys_immune_shield_gate: float = 0.0
    # L6 Trọng Địa Khống Chế — when True, the PERIODIC hook auto-applies
    # DebuffTroBuoc + DebuffLunDat to the opponent each turn. False → no-op.
    tho_auto_slow_enabled: bool = False
    # L9 Đại Địa Phản Phệ — per-turn damage dealt = actor.shield × this pct.
    # 0.0 → the PERIODIC hook is inert (every non-Tho-L9 build).
    tho_earth_aura_shield_pct: float = 0.0

    # ── Hoàng Cổ Thánh Thể (Universal Saint Body) build ──────────────────────
    # L5 Lân Tủy Thánh Hòa — per-hit chance to self-cleanse one debuff.
    # 0.0 → inert (the on-hit proc block is a no-op).
    saint_qilin_cleanse_chance: float = 0.0
    # L6 Thiên Tinh Quán Đỉnh — every-N-acted-turns guaranteed crit. Mirrors
    # body #2's ``bleed_hunter_periodic_interval`` pattern exactly; a parallel
    # counter + armed flag live below. 0 → the PRE_TURN hook is inert.
    saint_periodic_crit_interval: int = 0
    # L8 Đạo Văn Quy Nhất — fraction of mp_max restored per landed hit.
    # 0.0 → no MP gain on hit.
    saint_mp_on_hit_pct: float = 0.0
    # L9 Thần Tâm Thánh Cốt — realm config. All-False/zero → the PRE_TURN
    # hook never fires (enemies, flag-off, non-saint builds stay inert).
    saint_realm_enabled: bool = False
    saint_realm_interval: int = 0
    saint_realm_duration: int = 0
    # Runtime-only: acted-turn counter for the L6 guaranteed-crit cadence
    # (NOT a config key, NOT in CombatStats / _CONSTITUTION_FLAG_FIELDS).
    saint_crit_turn_counter: int = 0
    # Runtime-only: separate counter for the L9 realm cadence so L6 crit
    # arm and L9 realm fire on independent schedules.
    saint_realm_turn_counter: int = 0
    # Runtime armed flag for the L6 guaranteed crit (consumed on first
    # landed hit, mirrors bleed_hunter_crit_armed).
    saint_crit_armed: bool = False

    # ── Phong (Wind / Evasion / Mark) build ──────────────────────────────────
    # On-hit: chance actor applies Ấn Phong on the target. Once marked the
    # target loses evasion (via the debuff's stat_bonus) and the attacker gains
    # crit + crit-dmg advantage vs them until the mark drops off.
    mark_on_hit_pct: float = 0.0
    # Flat damage bonus = evasion_rating × this fraction (mirror of
    # damage_bonus_from_hp_pct / damage_bonus_from_mp_pct). Converts a
    # defensive stat into offensive power — core Phong playstyle.
    damage_bonus_from_evasion_pct: float = 0.0
    # ``crit_rating_vs_marked`` / ``crit_dmg_vs_marked`` moved into the
    # consolidated ``crit_amp_vs`` dict above.

    # ── Phong (Phi Thiên Lăng Vân) build ─────────────────────────────────────
    # L1 Phong Thể Tiêu Dao — every 300 evasion_rating converts to +phong dmg.
    # 0.0 → inert (every non-Phong-body and flag-off path).
    phong_eva_phong_dmg_per_300: float = 0.0
    # L1 Phong Vân stack gate — enables +1 stack on each successful dodge.
    # False → the on-evade block skips the increment entirely.
    phong_van_dodge_stack: bool = False
    # Runtime counter for Phong Vân stacks (NOT a config key, NOT in
    # CombatStats / _CONSTITUTION_FLAG_FIELDS). Hard-capped at 6 by the
    # on-evade hook; decremented by 2 on each non-DoT hit taken.
    phong_van_stacks = _StackProxy("phong_van", store="stack_counters")
    # L6 Phi Thiên Hư Ảnh — arm a guaranteed crit after each successful dodge.
    # False → the on-evade block skips the arm entirely.
    phong_dodge_arms_crit: bool = False
    # L6 — when the armed crit lands, apply DebuffAnPhong to the target.
    phong_dodge_crit_applies_an_phong: bool = False
    # Runtime armed flag for the L6 guaranteed crit (consumed on first
    # landed damaging hit, mirrors saint_crit_armed / bleed_hunter_crit_armed).
    # NOT a config key, NOT in CombatStats.
    phong_crit_armed: bool = False
    # L9 Thiên Phong Vô Ảnh — every N top-level casts, the next cast bypasses
    # evasion entirely. 0 → the cadence hook is inert.
    phong_unevadable_interval: int = 0
    # Runtime armed flag — set when the counter modulo triggers; consumed on
    # the first subsequent top-level cast. NOT a config key.
    phong_unevadable_armed: bool = False
    # Runtime cast counter for the L9 unevadable cadence. NOT a config key.
    phong_skill_cast_counter: int = 0
    # L9 — per-crit chance to inflict DebuffCuonBay on the target.
    # 0.0 → inert.
    phong_cuon_bay_on_crit_chance: float = 0.0

    # ── Lôi (Thiên Lôi Cường) build — shock/speed nuker ──────────────────────
    # L1: per-crit chance to inflict DebuffTeLiet (paralysis). 0.0 → inert.
    loi_te_liet_on_crit_chance: float = 0.0
    # L1 Lôi Điện Tích Trữ — each Lôi DoT tick the applier causes banks a charge
    # (cap 10); at 10 the applier auto-fires a 70%-matk TRUE burst + resets.
    loi_charge_enabled: bool = False
    loi_charge: int = 0  # runtime counter (NOT a config key)
    # L3 Lôi Khí Bạo Phát — +per_10 final-dmg for every 10 effective SPD over the
    # target, capped at loi_spd_advantage_cap. Both 0 → inert.
    loi_spd_advantage_per_10: float = 0.0
    loi_spd_advantage_cap: float = 0.0
    # L6 Điện Quang Phản Ứng — on crit OR dodge, auto-fire a bonus shock attack.
    loi_reflex_bonus_attack: bool = False
    # L9 Lôi Điện Hóa Thần — deal +this fraction of Lôi-element damage (skill hits
    # + DoT ticks) as TRUE damage (pierces resistance). 0.0 → inert.
    loi_bonus_true_dmg_pct: float = 0.0

    # ── Tịnh Quang Hộ Pháp Thể (Quang guardian) build ────────────────────────
    # L1 Thánh Quang — each landed blind banks a stack (cap 5); BuffHoPhapThanhQuang's
    # scaling_rules turn the stacks into +blind chance + DR. Both inert by default.
    quang_blind_stack: bool = False
    thanh_quang_stacks = _StackProxy("thanh_quang", store="stack_counters")  # runtime counter (NOT a config key)
    # L3 Tịnh Quang Tẩy Trần — every N acted turns, self-cleanse M debuffs.
    quang_self_cleanse_interval: int = 0
    quang_self_cleanse_count: int = 0
    quang_cleanse_turn_counter: int = 0  # runtime (NOT a config key)
    # L6 Hộ Pháp Thiên Giáp — spawn a permanent Holy Guardian summon dealing
    # this fraction of matk/turn (Quang) + granting BuffHoPhapKimCuong. 0 → none.
    quang_guardian_summon_matk_pct: float = 0.0
    # L9 Thiên Quang Thẩm Phán — on hitting a buffed enemy, chance to strip a
    # buff + apply Phá Giáp. 0.0 → inert.
    quang_judgment_strip_chance: float = 0.0
    quang_judgment_applies_pha_giap: bool = False
    # L9 buff: each successful strip deals a 200% atk + 200% matk burst AND
    # accumulates +5% final_dmg here (cap +50%). Runtime-only; read in combat_hit.
    quang_judgment_dmg_bonus: float = 0.0

    # ── Hỗn Nguyên Vô Cực Thể (Universal omni-element amplifier) ──────────────
    # ``omni_sum_element_dmg`` makes build_attack_stats fold the WHOLE
    # ``element_dmg_bonus`` dict into the skill's OWN-element bonus — Vạn Nguyên
    # Quy Nhất (a fire skill is paid the sum of every element's bonus, counted as
    # fire damage). ``omni_res_ignore_chance`` is the per-cast chance to treat the
    # target's elemental resistance as 0% (pen_pct → 1.0 in casting.py).
    omni_sum_element_dmg: bool = False
    omni_res_ignore_chance: float = 0.0

    # ── Thiên Địa Nhân Hòa Thể (Universal Hòa Khí stack-scaler) ───────────────
    # Hòa Khí climbs +``harmony_stack_per_turn`` each periodic phase up to
    # ``harmony_stack_cap`` and never decays (periodic/nhan_hoa.py). The L1 ramp
    # (+2% all stats/stack) and the L3/L9 thresholds read ``harmony_stacks`` via
    # scaling_rules ``stat:harmony_stacks``. L6 radiates a backlash aura
    # (``harmony_backlash_pct_per_stack`` × stacks × (atk+matk)) once stacks
    # reach ``harmony_backlash_min_stacks``; L9 cleanses ``harmony_l9_cleanse``
    # debuffs/turn at max stacks.
    harmony_stack_per_turn: int = 0
    harmony_stack_cap: int = 0
    harmony_backlash_pct_per_stack: float = 0.0
    harmony_backlash_min_stacks: int = 0
    harmony_l9_cleanse: int = 0
    harmony_stacks = _StackProxy("harmony", store="stack_counters")  # runtime (NOT a config key)

    # ── Bắc Minh Băng Phách Thể (Thủy ice/freeze/MP-drain disruptor) ──────────
    # All on-hit logic (bidirectional slow+hit-shave, freeze, MP-drain→heal,
    # heal-reduce, Hàn Khí burst) lives in run_on_hit_procs. ``han_khi_stacks``
    # banks +1 per successful MP drain (cap ``bm_han_khi_cap``); at cap the next
    # attack fires the Cực Hàn burst (3-turn freeze + dmg = bm_burst_drain_pct ×
    # ``han_khi_mp_drained_total``, the running tally of all MP drained this fight).
    bm_cold_aura_enabled: bool = False
    bm_mp_drain_pct: float = 0.0
    bm_mp_drain_heal_pct: float = 0.0
    bm_han_khi_cap: int = 0
    bm_heal_reduce_chance: float = 0.0
    bm_heal_reduce_vs_frozen_chance: float = 0.0
    bm_burst_freeze_turns: int = 0
    bm_burst_drain_pct: float = 0.0
    han_khi_stacks = _StackProxy("han_khi", store="stack_counters")  # runtime (NOT a config key)
    han_khi_mp_drained_total: int = 0  # runtime (NOT a config key)

    # ── Huyền Minh Nhược Thể (Thủy anti-physical attrition disruptor) ──────────
    # L1 ``phys_dmg_reduce_pct`` is a real defender-side stat (read in casting.py,
    # physical attack_type only). L3 drain (bidirectional MP→HP siphon, dry-siphon
    # banks Uyên), L6 evasion + Uyên-on-dodge ramp, L9 per-cast guaranteed
    # poison+bleed corrosion + Hủ Thủy Ấn + drown burst all live in
    # run_huyen_minh_procs / casting.py. ``hm_uyen_stacks`` banks +1 per dry-siphon
    # or successful dodge (cap ``hm_uyen_cap``); at cap the next attack fires the
    # drown burst (dmg = hm_drown_burst_drain_pct × ``hm_mp_drained_total``).
    phys_dmg_reduce_pct: float = 0.0
    hm_mp_drain_pct: float = 0.0
    hm_mp_drain_heal_pct: float = 0.0
    hm_hp_siphon_pct: float = 0.0
    hm_uyen_cap: int = 0
    hm_corrode_poison_stacks: int = 0
    hm_corrode_bleed_stacks: int = 0
    hm_drown_burst_drain_pct: float = 0.0
    hm_uyen_stacks = _StackProxy("hm_uyen", store="stack_counters")  # runtime (NOT a config key)
    hm_mp_drained_total: int = 0  # runtime (NOT a config key)

    # ── Thiên Thủy Thánh Thể (Thủy holy-spring sustain tank) ──────────────────
    # L1 regen is pure stats (hp_regen_pct/mp_regen_pct). L3 damage→heal+reflect
    # rides apply_reactive_damage; L6 heal-cleanse + Tịnh Hóa + MP-on-heal rides
    # _apply_heal; L9 ``res_thuy``/``final_dmg_reduce`` are real stats and the
    # Quy Khư abyss-swallow (spend ``tt_tinh_hoa_stacks`` to soak one hit 80%,
    # heal it, reflect, reset) lives in the casting defender block.
    # ``tt_tinh_hoa_stacks`` banks +1 per L6 cleanse (cap ``tt_tinh_hoa_cap``).
    tt_dmg_convert_heal_pct: float = 0.0
    tt_reflect_remainder_pct: float = 0.0
    tt_heal_cleanse_chance: float = 0.0
    tt_tinh_hoa_cap: int = 0
    tt_tinh_hoa_per_stack_cleanse: float = 0.0
    tt_tinh_hoa_mp_on_heal_pct: float = 0.0
    tt_abyss_threshold: int = 0
    tt_abyss_reduce_pct: float = 0.0
    tt_abyss_reflect_pct: float = 0.0
    tt_tinh_hoa_stacks = _StackProxy("tt_tinh_hoa", store="stack_counters")  # runtime (NOT a config key)

    # ── Lưu Ly Thuẫn Thân Thể (universal shield-only aegis body) ──────────────
    # No flesh: hp_max is locked to 1 at build and the would-be HP pool becomes
    # shield. ``shield_only_body`` makes take_damage force ALL damage through the
    # shield (bypass_shield / is_dot can't skip it). L3 ``heal_to_shield_pct``
    # routes heals into shield (_apply_heal); L6 reuses ``damage_bonus_from_shield_pct``;
    # L9 ``aegis_reform_charges`` is a once-per-fight shield reform consumed in
    # take_damage when a hit would breach the broken shield.
    shield_only_body: bool = False
    shield_from_hp_max_pct: float = 0.0
    heal_to_shield_pct: float = 0.0
    # Bách Thể Chú Linh — Ngân Giác Lộc awakening: overflow healing past
    # hp_max converts into shield (consumed in CombatSession._apply_heal).
    overheal_to_shield_pct: float = 0.0
    aegis_reform_charges: int = 0
    aegis_reform_shield_pct: float = 0.0
    aegis_reform_just_triggered: bool = False  # runtime (NOT a config key)

    # ── Vô Cấu Lưu Ly Thể (universal purity tank) ─────────────────────────────
    # L1 ``vc_cc_resist_pct`` (75% chance to shrug CC) + L9
    # ``vc_bat_triem_immune_pct`` (75% chance to shrug every OTHER negative
    # effect) gate incoming applications in ``inflict_interceptors``; the two
    # lanes are disjoint (no double-roll on CC) and CCStun always bypasses
    # both — the body's designed flaw. Each successful block banks +1
    # ``vo_cau_stacks`` (cap ``vo_cau_cap``, set at L9); BuffVanPhapBatTriem
    # scales stacks into ``magic_reflect_pct`` (L6 base 35% → 56% at 7),
    # consumed in ``apply_reactive_damage`` for magical hits only and capped
    # per hit at 10% of the attacker's max HP. ``pill_toxin_immune`` is an
    # out-of-combat flag (alchemy.consume_pill). L3 ``vc_tinh_hoa_resonance``
    # upgrades the Lưu Ly Tịnh Hỏa cleanse aura.
    vc_cc_resist_pct: float = 0.0
    pill_toxin_immune: bool = False
    vc_tinh_hoa_resonance: bool = False
    magic_reflect_pct: float = 0.0
    vc_bat_triem_immune_pct: float = 0.0
    vo_cau_cap: int = 0
    vo_cau_stacks = _StackProxy("vo_cau", store="stack_counters")  # runtime (NOT a config key)

    # ── Cửu U Ma Đế Thể (Ám soul-drain summoner) ──────────────────────────────
    # L1: generic soul_drain/stat_steal lanes + Ma Khí banking (+1 per landed
    # hit, cap ``cu_ma_khi_cap``; BuffCuuUMaKhi scales stacks → matk_pct, and
    # ``cu_drain_amp_per_stack`` amps apply_soul_drain's per-proc drain).
    # L3: Vong Linh follow-up (procs.run_cuu_u_procs — chance, % matk Ám hit
    # + a soul drain). L6: BuffCuuUChiCanh 9-stack fdb gate + U Minh Quỷ Hỏa
    # cast synergy (``cu_uminh_bonus_drains`` immediate drains). L9: build-time
    # Ma Đế evolution (builders — prereq SkillAmChanMaChiTam_R9 equipped) sets
    # ``cu_ma_de_evolved`` (follow-up also stat-steals) + BuffMaDeQuyVuong.
    cu_ma_khi_cap: int = 0
    cu_drain_amp_per_stack: float = 0.0
    cu_vl_follow_up_chance: float = 0.0
    cu_vl_dmg_matk_pct: float = 0.0
    cu_uminh_bonus_drains: int = 0
    cu_ma_de_enabled: bool = False
    cu_ma_de_evolved: bool = False  # runtime (NOT a config key)
    ma_khi_stacks = _StackProxy("ma_khi", store="stack_counters")  # runtime (NOT a config key)

    # ── Thôn Thiên Ma Thể (Ám devourer) ───────────────────────────────────────
    # L1 is meta-layer only (cultivation speed / loot luck — no combat fields).
    # L3 ``ttm_devour_copy``: the donor body's BASE L1 stat_bonuses merge in
    # character_stats and its L1 effects stamp in builders. L6 Hắc Động:
    # ``ttm_absorb_matk_pct`` of damage taken becomes flat temp MATK (capped at
    # ``ttm_absorb_cap_pct`` × starting matk; apply_reactive_damage), and each
    # landed hit rolls ``ttm_strip_mp_chance`` to strip one enemy buff into
    # ``ttm_strip_mp_gain_pct`` × mp_max (run_thon_thien_procs). L9: every
    # ``ttm_devour_interval`` acted turns the aura steals ALL stealable enemy
    # buffs + big stat-steal + capped true damage (auras/thon_thien.py).
    ttm_devour_copy: bool = False
    ttm_absorb_matk_pct: float = 0.0
    ttm_absorb_cap_pct: float = 0.0
    ttm_strip_mp_chance: float = 0.0
    ttm_strip_mp_gain_pct: float = 0.0
    ttm_devour_interval: int = 0
    ttm_matk_absorbed: int = 0        # runtime (NOT a config key)
    ttm_devour_turn_counter: int = 0  # runtime (NOT a config key)

    # ── Thái Dương Đạo Thể (universal solar anti-demon tank) ──────────────────
    # L1 ``td_anti_demon_dmg_pct``: +final dmg vs "yêu ma quỷ quái" — mapped
    # to Ám-element enemies + the Beast* families (combat_hit). L6 Thần Lô:
    # each damaging hit TAKEN banks +1 ``than_lo_stacks`` (cap
    # ``td_than_lo_cap``; apply_reactive_damage); BuffThaiDuongThanLo scales
    # stacks → +3%/stack all core stats, BuffNhatDieuCuuThien adds +1.5% at
    # L9. L9 solar burst: every ``td_solar_interval`` acted turns, capped
    # true dmg = ``td_solar_hp_pct`` × own hp_max + blind (auras/thai_duong).
    td_anti_demon_dmg_pct: float = 0.0
    td_than_lo_per_hit: bool = False
    td_than_lo_cap: int = 0
    td_solar_interval: int = 0
    td_solar_hp_pct: float = 0.0
    td_solar_turn_counter: int = 0  # runtime (NOT a config key)
    than_lo_stacks = _StackProxy("than_lo", store="stack_counters")  # runtime (NOT a config key)

    # ── Thái Âm Đạo Thể (universal yin-moon evasion/freeze) ───────────────────
    # L1 rides generic lanes (mp_regen/evasion/element thuy +
    # damage_bonus_from_evasion_pct). L3 ``freeze_on_skill_chance`` (generic) +
    # ``ta_dmg_vs_frozen_pct`` final dmg vs frozen targets (combat_hit; stacks
    # with the existing frozen→auto-crit). L6 Trảm Đạo cadence: every
    # ``ta_tram_dao_interval`` acted turns strip ``ta_tram_dao_strips`` enemy
    # buffs + stamp DebuffTramDao (auras/thai_am.py). L9: moonlight per-turn
    # heal/freeze + ``ta_kinh_hoa_resonance`` makes the Kính Hoa Thủy Nguyệt
    # debuff-transfer guaranteed while its buff is up.
    ta_dmg_vs_frozen_pct: float = 0.0
    ta_tram_dao_interval: int = 0
    ta_tram_dao_strips: int = 0
    ta_kinh_hoa_resonance: bool = False
    ta_moonlight_heal_pct: float = 0.0
    ta_moonlight_freeze_chance: float = 0.0
    ta_tram_dao_turn_counter: int = 0  # runtime (NOT a config key)

    # ── Liệt Diễm Phần Thiên Thể (Hỏa escalating fire nuker) ──────────────────
    # L1 ramp: periodic increments ``lietdiem_burn_stacks`` (+per_turn, cap), the
    # buff's scaling_rules convert it to matk_pct + crit_rating. L6 absorb: a hoa
    # hit taken banks ``lietdiem_van_hoa_stacks`` (cap), the buff scales it into
    # dmg_bonus_hoa. L9 avatar: the periodic increments ``lietdiem_avatar_counter``
    # and stamps BuffHoaThanHoaThan every ``interval`` turns; L9 also grants
    # dot_can_crit (a real stat, not here).
    lietdiem_burn_per_turn: int = 0
    lietdiem_burn_cap: int = 0
    lietdiem_van_hoa_absorb: bool = False
    lietdiem_van_hoa_cap: int = 0
    lietdiem_avatar_enabled: bool = False
    lietdiem_avatar_interval: int = 0
    lietdiem_avatar_duration: int = 0
    lietdiem_burn_stacks = _StackProxy("lietdiem_burn", store="stack_counters")  # runtime (NOT a config key)
    lietdiem_van_hoa_stacks = _StackProxy("lietdiem_van_hoa", store="stack_counters")  # runtime (NOT a config key)
    lietdiem_avatar_counter: int = 0  # runtime (NOT a config key)

    # ── Niết Bàn Bất Diệt Thể (Hỏa lifesteal-res nirvana berserker) ───────────
    # L1: ``hoa_overcap_to_dmg`` lifts Hỏa res to 90% (via the existing
    # ``hoa_max_resist_bonus`` lane) and spills the overcap into element_dmg_bonus.hoa
    # (overcap block in compute_combat_stats, build-time). L6: a dedicated
    # ON_REVIVE hook (revives.py) reads ``niet_ban_revive_*`` to revive once at
    # ``niet_ban_revive_pct`` (+5%/Nghiệp tier), clear debuffs, and — when
    # ``niet_ban_post_revive_boost`` (L9) — stamp BuffCuuChuyenNietBan. Nghiệp Hỏa:
    # the niet_ban PERIODIC hook counts the holder's fire DoTs ticking on the foe
    # into ``nb_nghiep_progress``; every 10 rolls a ``nb_nghiep_tier`` (cap 3) and
    # monotonically bumps element_dmg_bonus.hoa / dot_dmg_bonus (fields read live
    # by combat_hit / dot.py). ``niet_ban_revive_used`` is the once-per-fight latch.
    hoa_overcap_to_dmg: bool = False
    nb_nghiep_accumulate: bool = False
    nb_nghiep_revive_pct_per_tier: float = 0.0
    niet_ban_revive_enabled: bool = False
    niet_ban_revive_pct: float = 0.0
    niet_ban_revive_clear_debuffs: bool = False
    niet_ban_post_revive_boost: bool = False
    nb_nghiep_tier: int = 0  # runtime (NOT a config key) — 0..3 accumulated tiers
    nb_nghiep_progress: int = 0  # runtime (NOT a config key) — 0..9 micro-progress
    niet_ban_revive_used: bool = False  # runtime (NOT a config key)

    # ── Hậu Thổ Thần Thể (Thổ HP-vampire growth juggernaut) ───────────────────
    # L1 ``run_hau_tho_procs`` (procs.py): each landed hit siphons
    # ``hau_tho_hp_steal_pct`` (+0.5%/Địa Mạch tier) of the foe's CURRENT HP,
    # grows ``hp_max`` + heals by it, and banks it in ``hau_tho_stolen_total``.
    # ``hau_tho_accumulate`` gates the Địa Mạch tiers: every 1% of the foe's
    # max-HP siphoned (tracked in ``hau_tho_steal_progress``) rolls one
    # ``hau_tho_tier`` (cap 10); at tier 10 a one-time +10% hp_max fires
    # (``hau_tho_tier10_applied`` latch). L3 (casting.py) per-cast true damage =
    # max(hp_max×``hau_tho_dmg_from_maxhp_pct``, shield×(``hau_tho_dmg_from_shield_pct``
    # + tier×_HAU_THO_SHIELD_PCT_PER_TIER)), capped per _CONSTITUTION_TRUE_DMG_CAP_PCT.
    # L9 ``hau_tho_rebirth_enabled`` → revives.py survives one lethal
    # hit at HP = ``hau_tho_stolen_total`` (``hau_tho_rebirth_used`` latch).
    hau_tho_hp_steal_pct: float = 0.0
    hau_tho_accumulate: bool = False
    hau_tho_dmg_from_maxhp_pct: float = 0.0
    hau_tho_dmg_from_shield_pct: float = 0.0
    hau_tho_rebirth_enabled: bool = False
    hau_tho_stolen_total: int = 0  # runtime (NOT a config key)
    hau_tho_tier: int = 0  # runtime (NOT a config key) — 0..10 Địa Mạch tiers
    hau_tho_steal_progress: float = 0.0  # runtime (NOT a config key) — frac toward next tier
    hau_tho_rebirth_used: bool = False  # runtime (NOT a config key)
    hau_tho_tier10_applied: bool = False  # runtime (NOT a config key)

    # ── Thánh Sơn Bất Động Thể (Thổ immovable fortress) ───────────────────────
    # L1 Kiên Cố: ``run_thanh_son_procs`` defender-side adds +1 ``thanh_son_kien_co_stacks``
    # per hit taken (cap ``thanh_son_kien_co_cap``); BuffKienCo's scaling_rules turn the
    # counter into live res_all + final_dmg_reduce. L3 (casting.py per-cast): true dmg =
    # shield × (``thanh_son_dmg_from_shield_pct`` + ``thanh_son_l3_full_bonus`` once
    # stacks≥4); attacker-side per-hit Bào Mòn (``thanh_son_bao_mon_chance``) + Choáng
    # (generic ``stun_on_hit_pct`` + ``stun_on_hit_turns`` lane). L9 (take_damage): while
    # ``thanh_son_immovable_enabled`` AND stacks == cap, a would-be-lethal non-DoT hit
    # leaves HP at 1 + restores ``thanh_son_survive_shield_pct`` of shield_cap (sets
    # ``thanh_son_immovable_just_triggered`` for the periodic announcer). Any landed
    # turn-skip CC strips 1 Kiên Cố (in ``apply_effect``, the single stamp site, so
    # direct-apply freeze/stun procs crack too) → drops below cap → mortal again.
    thanh_son_kien_co_on_hit: bool = False
    thanh_son_kien_co_cap: int = 0
    thanh_son_dmg_from_shield_pct: float = 0.0
    thanh_son_l3_full_bonus: float = 0.0
    thanh_son_bao_mon_chance: float = 0.0
    thanh_son_immovable_enabled: bool = False
    thanh_son_survive_shield_pct: float = 0.0
    thanh_son_kien_co_stacks = _StackProxy("thanh_son_kien_co", store="stack_counters")  # runtime (NOT a config key)
    thanh_son_immovable_just_triggered: bool = False  # runtime (NOT a config key)
    thanh_son_kien_co_just_cracked: bool = False  # runtime (NOT a config key)

    # ── Thiên Kiếp Vạn Lôi Thể (Lôi tribulation CC-lockdown executioner) ──────
    # L1 Vạn Lôi accrual: +``tk_stack_on_cast`` per own landed Lôi CAST
    # (casting.py, per-cast) and +``tk_stack_on_struck`` when STRUCK by a Lôi
    # skill (run_thien_kiep_procs, defender side), cap ``tk_van_loi_cap``, never
    # decays; BuffVanLoi's scaling_rules turn ``tk_van_loi_stacks`` into live
    # crit_dmg_rating + spd_pct. L3 per-hit riders (run_thien_kiep_procs):
    # ``tk_soc_dien_chance`` Sốc Điện + ``tk_te_liet_chance`` Tê Liệt, the
    # latter upgraded to a ``tk_stun_chance`` Choáng(``tk_stun_turns``) roll once
    # stacks ≥ ``tk_stun_stack_gate``. L6: ``tk_evasion_shred_pct`` — the
    # ATTACKER-side evasion shred applied in build_defense_stats. L9:
    # ``tk_extra_hits`` sustained hit_count bonus; HP-gated execute vs
    # common-rank foes (``tk_execute_chance`` roll when target ≤
    # ``tk_execute_hp_pct`` of max HP; at ``tk_execute_stack_gate`` stacks the
    # roll is skipped and stacks reset).
    tk_stack_on_cast: int = 0
    tk_stack_on_struck: int = 0
    tk_van_loi_cap: int = 0
    tk_te_liet_chance: float = 0.0
    tk_stun_chance: float = 0.0
    tk_stun_stack_gate: int = 0
    tk_stun_turns: int = 0
    tk_evasion_shred_pct: float = 0.0
    tk_extra_hits: int = 0
    tk_execute_chance: float = 0.0
    tk_execute_hp_pct: float = 0.0
    tk_execute_stack_gate: int = 0
    tk_van_loi_stacks = _StackProxy("tk_van_loi", store="stack_counters")  # runtime (NOT a config key)

    # ── Cửu Thiên Huyền Lôi Thể (Lôi signature-art channeler) ─────────────────
    # Everything orbits ONE named skill (casting.py ``_CT_NAMED_SKILL`` =
    # SkillLoiCuuThienNguLoiChanQuyet). L1: per-hit generic ``te_liet_on_hit_pct``
    # Tê Liệt (run_cuu_thien_procs) + named-cast amp ``ct_skill_dmg_amp`` +
    # Năng Lượng (``ct_nang_luong_stacks``: +1/named cast, cap
    # ``ct_nang_luong_cap``, no reset; +``ct_nang_luong_dmg_per_stack`` amp each).
    # L3: successful dodge stamps BuffThiemDienPhanKich (+40% Lôi next turn,
    # gated ``ct_dodge_loi_amp``). L6: per-hit DebuffLoiXuyenThau
    # (generic ``loi_shred_on_hit_pct`` lane) + ``ct_skill_extra_hits`` on the named cast.
    # L9: every ``ct_burst_interval`` acted turns → BuffThanLoiGiangThe for
    # ``ct_burst_duration`` (+1 at ``ct_burst_bonus_turn_gate`` stacks) via
    # auras/cuu_thien.py; window = spd/loi-amp buff + force-crit (combat_hit)
    # + auto Sốc Điện/Sét Đánh (shock/te-liet boosts via the buff stat_bonus).
    ct_skill_dmg_amp: float = 0.0
    ct_nang_luong_cap: int = 0
    ct_nang_luong_dmg_per_stack: float = 0.0
    ct_dodge_loi_amp: float = 0.0
    ct_skill_extra_hits: int = 0
    ct_burst_interval: int = 0
    ct_burst_duration: int = 0
    ct_burst_bonus_turn_gate: int = 0
    ct_nang_luong_stacks = _StackProxy("ct_nang_luong", store="stack_counters")  # runtime (NOT a config key)
    ct_burst_turn_counter: int = 0  # runtime (NOT a config key)

    # ── Tiêu Dao Thần Thể (Phong movement-dancer / dual-form transformer) ─────
    # L1: casting Phù Dao Trực Thượng (casting.py ``_TD_RESONANCE_SKILL``)
    # stamps BuffNguPhongCongHuong (``td_resonance_enabled``); every movement
    # cast banks +1 Tiêu Dao Cảnh (``td_canh_stacks``, cap ``td_canh_cap``, no
    # reset; every ``td_canh_per_turn`` stacks = +1 form turn). L3: movement
    # cast arms ``td_strike_armed`` — the next attack is unevadable +
    # force-crit + pierces ``td_pierce_def_pct`` of def; movement cooldowns
    # shrink ``td_mov_cd_reduce_pct``. L6: ``td_cc_shrug_pct`` chance to act
    # through any turn-skip CC (check_cc_skip_turn sets
    # ``td_cc_just_shrugged`` for the session log). L9: every
    # ``td_form_interval`` acted turns (auras/tieu_dao.py) transform for
    # ``td_form_duration`` (+stack bonus) turns — HP under ``td_con_hp_gate``
    # → BuffHoaCon (take_damage banks HP lost into ``td_con_bank``; expiry
    # releases × ``td_con_release_mult`` as capped true dmg + heals
    # ``td_con_heal_pct``), else BuffHoaBang (+``td_bang_extra_hits`` hits,
    # force-crit via combat_hit, unevadable via casting).
    td_resonance_enabled: bool = False
    td_canh_cap: int = 0
    td_canh_per_turn: int = 0
    td_post_mov_arm: bool = False
    td_pierce_def_pct: float = 0.0
    td_mov_cd_reduce_pct: float = 0.0
    td_cc_shrug_pct: float = 0.0
    td_form_interval: int = 0
    td_form_duration: int = 0
    td_con_hp_gate: float = 0.0
    td_con_release_mult: float = 0.0
    td_con_heal_pct: float = 0.0
    td_bang_extra_hits: int = 0
    td_canh_stacks = _StackProxy("td_canh", store="stack_counters")  # runtime (NOT a config key)
    td_form_turn_counter: int = 0  # runtime (NOT a config key)
    td_strike_armed: bool = False  # runtime (NOT a config key)
    td_con_bank: int = 0  # runtime (NOT a config key)
    td_cc_just_shrugged: str | None = None  # runtime (NOT a config key)

    # ── Cửu Thiên Cương Phong Thể (Phong anti-evasion wind-blade shredder) ────
    # L1's Ấn Phong + Chảy Máu ride the GENERIC on-hit lanes
    # (``mark_on_hit_pct`` / ``bleed_on_hit_pct`` — real stats, no per-body
    # flags); run_cuong_phong_procs only banks 1 Phong Nhận Tích per hit ON
    # THE TARGET (``cp_tich_stacks`` lives on the enemy — per-target by
    # construction, dies with them; cap ``cp_tich_cap``). L3: per-hit
    # DebuffPhongXuyenThau (generic ``phong_shred_on_hit_pct`` lane) + sustained pierce
    # ``cp_pierce_def_pct`` → ``cp_pierce_def_pct_high`` at ≥
    # ``cp_pierce_tich_gate`` Tích (casting, frozen-DefenseStats replace).
    # L6: ``cp_bonus_strike_chance`` per hit to fire SkillPhongBonusStrike
    # (run_on_hit_procs, skill_key-guarded). L9: every ``cp_storm_interval``
    # acted turns → BuffCuongPhongBao for ``cp_storm_duration``
    # (auras/cuong_phong.py): unevadable + force-crit +
    # ``cp_storm_extra_hits`` + ``cp_storm_cuon_bay_chance`` Cuốn Bay/hit. At
    # full Tích the next hit fires Cương Phong Xuyên
    # (``cp_tich_execute_atk_scale`` × ATK via the shared capped true-dmg
    # rider + guaranteed Cuốn Bay; Tích resets).
    cp_tich_cap: int = 0
    cp_pierce_def_pct: float = 0.0
    cp_pierce_def_pct_high: float = 0.0
    cp_pierce_tich_gate: int = 0
    cp_bonus_strike_chance: float = 0.0
    cp_storm_interval: int = 0
    cp_storm_duration: int = 0
    cp_storm_cuon_bay_chance: float = 0.0
    cp_storm_extra_hits: int = 0
    cp_tich_execute_atk_scale: float = 0.0
    cp_tich_stacks = _StackProxy("cp_tich", store="stack_counters")  # runtime (NOT a config key)
    cp_storm_turn_counter: int = 0  # runtime (NOT a config key)

    # ── Generic on-hit lane extensions (shared, like mark/bleed_on_hit_pct) ───
    # ``te_liet_on_hit_pct`` (hard-CC-immune-respecting _ON_HIT_PROCS row) and
    # the two elemental-shred chances are real stats — buff/gear contributions
    # aggregate via get_combat_modifiers in the proc table's chance read.
    # ``stun_on_hit_turns`` overrides the generic stun proc's default duration.
    te_liet_on_hit_pct: float = 0.0
    loi_shred_on_hit_pct: float = 0.0
    phong_shred_on_hit_pct: float = 0.0
    stun_on_hit_turns: int = 0

    # ── Quang Minh Thánh Thể (Quang radiant control-purifier) ─────────────────
    # L1 radiance aura (auras/quang_minh.py): per-turn ``qm_aura_blind_chance``
    # Lóa Mắt + DebuffQuangMinhVuc crit shred; a landed aura blind banks +1
    # Thánh Quang (``qm_thanh_quang_stacks``, cap ``qm_stack_cap``) —
    # BuffThanhQuangTichTu's scaling rules turn the counter into
    # dmg_bonus_quang + qm_strip_vs_blind_chance. L6: per-hit
    # ``qm_strip_vs_blind_chance`` (REAL stat — field + mods) to strip 1 buff
    # from a BLINDED target (run_quang_minh_procs). L9: every
    # ``qm_purify_interval`` acted turns (−1 at ``qm_purify_fast_stack_gate``
    # stacks) → full self-cleanse + strip ``qm_purify_strip_count`` buffs +
    # heal ``qm_purify_heal_pct`` + BuffThanhKhiet (75% debuff-shrug, 2t).
    qm_aura_blind_chance: float = 0.0
    qm_stack_cap: int = 0
    qm_strip_vs_blind_chance: float = 0.0
    qm_purify_interval: int = 0
    qm_purify_strip_count: int = 0
    qm_purify_heal_pct: float = 0.0
    qm_purify_fast_stack_gate: int = 0
    qm_thanh_quang_stacks = _StackProxy("qm_thanh_quang", store="stack_counters")  # runtime (NOT a config key)
    qm_purify_turn_counter: int = 0  # runtime (NOT a config key)

    # ── Quang (Light / Silence / Anti-Heal) build ────────────────────────────
    # On-crit: chance the actor applies CCMuted (silence) to the target. Gated
    # on crit so it rewards the crit-heavy setup Quang uniques push toward.
    silence_on_crit_pct: float = 0.0
    # On-hit: chance the actor applies DebuffCatDut (anti-heal / hp_regen shred)
    # to the target. Integrates into the generic on-hit proc table.
    heal_reduce_on_hit_pct: float = 0.0
    # Pre-turn: extra cleanse roll added on top of the base 15% Quang Linh Căn
    # chance. Sourced from formation gem thresholds and unique equipment.
    cleanse_on_turn_pct: float = 0.0
    # When True, a successful cleanse grants a small MATK-scaled barrier
    # (delta from Thổ Hộ Thể, which is one-shot at low HP — this repeats).
    barrier_on_cleanse: bool = False
    # When True, heals may crit (25% chance × 1.5 mult — mirrors dot_can_crit).
    # Shared by Mộc and Quang builds; granted via formation + late-realm uniques.
    heal_can_crit: bool = False

    # ── Âm (Shadow / Soul-Devour) build ──────────────────────────────────────
    # On-hit: chance the actor drains a slice of target's hp_max for the rest
    # of the fight. Drain permanently shrinks ``target.hp_max`` (and target.hp
    # at the same time if overflow). A fraction of each drain is transferred
    # to the actor as hp_max growth, making the Âm build snowball vs long
    # fights. Per-proc and total-drain caps are enforced in balance.py.
    soul_drain_on_hit_pct: float = 0.0
    # Tracker: cumulative hp_max drained from this combatant this fight.
    # Read only by the engine to clamp further drains at the cap.
    hp_max_drained: int = 0
    # Initial hp_max snapshot — captured on first drain so cap math always
    # references the pre-drain value rather than chasing a shrinking pool.
    hp_max_original: int = 0
    # On-hit: chance actor steals a flat slice of target's atk/matk/def
    # (subtracts from target, adds to actor) for the rest of the fight. Total
    # stolen amount tracked below and capped at STAT_STEAL_CAP_PCT of each
    # source stat's snapshot.
    stat_steal_on_hit_pct: float = 0.0
    stolen_atk: int = 0
    stolen_matk: int = 0
    stolen_def: int = 0
    # Starting-stat snapshots so the cap math references pre-theft values.
    atk_original: int = 0
    matk_original: int = 0
    def_stat_original: int = 0
    # ``crit_rating_vs_drained`` moved into the consolidated ``crit_amp_vs``
    # dict above (along with the bleed/marked variants).
    # On-hit: chance actor applies DebuffLoaMat (Lóa Mắt). The blinded target
    # then has BLIND_MISS_CHANCE per swing to whiff. Goes through the generic
    # on-hit proc table.
    blind_on_hit_pct: float = 0.0

    # ── Lôi (Lightning / Shock) build ────────────────────────────────────────
    # Shock stacks — applied by on-hit procs or loi skills. Each stack makes
    # the holder take an additional ``shock_per_stack_pct`` of any incoming
    # Lôi-element hit as flat final-damage amplification (non-Lôi skills do
    # not trigger the bonus). Cap via ``EffectMeta.stack_cap`` +
    # ``stack_cap_bonuses["DebuffSocDien"]``.
    shock_stacks = _StackProxy("DebuffSocDien")
    # Per-stack final-damage multiplier the attacker adds when a Lôi-element
    # hit lands on a shocked target. e.g. 0.03 → +3% final damage per stack.
    shock_per_stack_pct: float = 0.03
    # Same-element cast streak — element of the LAST top-level skill this
    # combatant cast, and how many same-element casts have chained in a row
    # (INCLUDING the current one). Bumped in ``cast_skill`` for top-level
    # casts only (reactive / 0-mp suppressed casts don't break the chain).
    # The first cast of an element starts the streak at 1; a different
    # element resets it to 1. Read by ``same_element_streak_scaling``
    # (Lôi Tù Cấm Ngục) to scale base_dmg + element_pen + forced Sốc Điện.
    last_cast_element: Optional[str] = None
    same_element_streak: int = 0
    # Truy Kích Liên Vũ (Phong B1) — self-combo escalator. Each consecutive
    # cast of ``SkillAtkPhongTruyKichLuyenVu`` that is NOT broken bumps this
    # toward ``combo_cap``; the ``combo_counter_scaling`` rider reads it for
    # the CURRENT cast's base + final-dmg bonus. Breaks (reset to 0) when the
    # actor casts a different-element skill (cast path) or takes any CC
    # (``inflict_debuff`` path). Empty for enemies → no combo → inert.
    phong_combo: int = 0
    # Trào Tịch Tích Lãng (Thủy B2) — dealt-damage reservoir + cast counter.
    # Each cast of ``SkillAtkThuyTrieuTichLang`` banks a fraction of the
    # damage dealt into ``thuy_tide`` (clamped to a matk-scaled cap) and bumps
    # ``thuy_tide_casts``; every Nth cast the ``tide_charge`` post-cast
    # consumer discharges the reservoir as a Thủy strike and resets both.
    # Both default 0 → enemies (which never cast the skill) stay inert.
    thuy_tide: int = 0
    thuy_tide_casts: int = 0

    # ── Thủy (Nhược Thủy Ấn) mark build ──────────────────────────────────────
    # Nhược Thủy Ấn marks: each stack shreds 4% res_thuy via
    # ``get_combat_modifiers``; at the cap the next application detonates
    # for ``thuy_mark_detonate_lost_hp_pct × (hp_max - hp)`` and resets
    # the counter to 0. Stacks clear when DebuffNhuocThuyAn expires. Cap
    # from ``EffectMeta.stack_cap``.
    thuy_mark_stacks = _StackProxy("DebuffNhuocThuyAn")

    # ── Thủy (Cửu Khúc Hoàng Hà) formation mark build ─────────────────────────
    # Player-side tunables — single dict (mirrors ``toa_hon_amp`` /
    # ``element_pen`` / ``dmg_taken``). Keys: ``per_hit`` (>0 enables the
    # per-skill auto-stamp in ``cast_skill``), ``atk_reduce_pct`` /
    # ``res_shred_pct`` / ``followup_pct`` (threshold magnitudes snapshotted
    # into the target on apply), ``followup_skill_key`` (chain target at full
    # stack). JSON declares the dict; gem thresholds merge additively for
    # numbers and last-write-wins for the string key.
    cuu_khuc: dict = field(default_factory=dict)
    # Vạn Kiếp Lôi Ngục Trận tunables — single dict (mirrors ``cuu_khuc``).
    # ``per_cast_bonus`` (extra Lôi Kiếp Ấn stacks/tick on top of the always-
    # 1 base read in the stack stamper); ``bolt_*`` and ``capstone_*`` are
    # numeric tunables consumed by ``formation_prison.process_loi_kiep_an_tick``
    # to size the milestone Lôi Kiếp Phán bolt and the 10-stack Vạn Kiếp
    # Phán capstone. Gem thresholds merge additively (per ``_merge_bonus_dict``).
    loi_kiep_an: dict = field(default_factory=dict)
    # Thiên Lôi Tru Tà Trận tunables — single dict (mirrors ``loi_kiep_an``).
    # Read by ``formation_prison.process_thien_loi_tru_ta_tick`` to size the
    # Thiên Lôi Phán bolt + Thiên Lôi Đại Phán capstone and to gate the
    # capstone cooldown. Gem-tier deltas merge additively via _merge_bonus_dict.
    thien_loi_tru_ta: dict = field(default_factory=dict)
    # Thái Cực Âm Dương Lôi Đại Trận tunables — single dict (mirrors the
    # other formation dicts). Read by ``process_thai_cuc_tick`` to size
    # the Dương / Âm phase payloads, the Thái Cực Lưỡng Nghi Lôi fusion,
    # and the dual_phase_fire (10-gem) tier flag.
    thai_cuc_am_duong_loi: dict = field(default_factory=dict)
    # Thái Cực phase rotation + per-pole Khí counters. ``taic_phase`` toggles
    # 0→1→0 each tick (0=Dương next, 1=Âm next). Each phase emit increments
    # its own Khí counter (cap 5). When both reach 5 the next tick fires
    # the Thái Cực Lưỡng Nghi Lôi fusion which consumes both back to 0.
    taic_phase: int = 0
    taic_duong_khi: int = 0
    taic_am_khi: int = 0
    # Xích Luyện Tỏa Hồn Trận — single dict of stat_bonus deltas layered on
    # top of DebuffXichLuyenToaHon at apply time. Mirrors ``element_pen`` /
    # ``dmg_taken`` — JSON gem thresholds declare
    # ``toa_hon_amp: {"evasion_rating_pct": -0.05, ...}`` and apply_skill_effects
    # merges each entry into the debuff's stat_bonus. Keys MUST match the
    # debuff's stat keys verbatim.
    toa_hon_amp: dict[str, float] = field(default_factory=dict)
    # Reusable per-element "damage taken" amps the holder contributes to its
    # debuffs (mirrors ``element_pen``). Any source can write to any element
    # via ``dmg_taken: {"hoa": 0.15}`` in JSON / bonus dicts. At apply time
    # the relevant entry is stamped onto the target's stat_bonus under the
    # flat ``<name>_dmg_taken`` key (see ELEMENT_DMG_TAKEN_KEYS in
    # damage/combat_hit.py), then consumed by apply_elemental.
    dmg_taken: dict[str, float] = field(default_factory=dict)
    # Target-side counter + per-stack snapshot of the applier's tier magnitudes.
    # Snapshots are written by ``inflict_debuff`` so the debuff's effect
    # magnitude follows the formation that *applied* it (not the holder's
    # own tunables, which would never trigger). Stacks clear when
    # DebuffCuuKhuc expires. Cap from ``EffectMeta.stack_cap`` (default 9;
    # gates 3/6/9 use the cap as their max).
    cuu_khuc_stacks = _StackProxy("DebuffCuuKhuc")
    cuu_khuc_atk_reduce_active: float = 0.0
    cuu_khuc_res_shred_active: float = 0.0
    # Vạn Kiếp Lôi Ngục Trận — formation prison tax mark. Each round while
    # the formation is active, the owner's ``SkillFrmVanKiepLoiNguc`` auto-fire
    # stamps +1 stack on the opposing target. Per-stack +dmg_taken_bonus_loi
    # / -res_loi expand from the meta's ``scaling_rules`` keyed off
    # ``stack:loi_kiep_an``. Milestone-bolts and the 10-stack Vạn Kiếp Phán
    # capstone read this counter from inside the formation cast.
    loi_kiep_an_stacks = _StackProxy("DebuffLoiKiepAn")
    # Thất Sát Trảm Trận — pure marker counter built by every Kim hit while
    # the formation is active. No DoT, no stat reduction; the threshold (7)
    # gates the formation_skill's true-damage execute via
    # ``consume_target_marks_for_execute``. Cap and meta default duration
    # live on ``EffectMeta(DebuffSatAn)``.
    sat_an_stacks = _StackProxy("sat_an", store="stack_counters")
    # Cuồng Phong Đại Trận — combo + burst counters. ``consecutive_phong_casts``
    # is the resettable combo chain (cap 5, reset on any non-Phong top-level
    # cast or basic attack); the formation's aura buff reads this via
    # ``stat:consecutive_phong_casts`` to scale ``dmg_bonus_phong`` and
    # ``crit_rating`` per stack. ``phong_casts_total`` is the cumulative
    # counter (never resets in-fight); the engine hook in ``cast_skill``
    # fires ``SkillFrmCuongPhongBurst`` on every 5th cumulative Phong cast
    # while the holder carries ``BuffCuongPhongDaiTran``.
    consecutive_phong_casts: int = 0
    phong_casts_total: int = 0
    # Chưởng Tâm Lôi resonator — same pattern as ``consecutive_phong_casts``
    # but for Loi-element casts. Cap 5, resets on any non-Loi top-level cast
    # or basic attack. ``BuffTamLoiCong`` reads this via
    # ``stat:consecutive_loi_casts`` to scale crit_rating and dmg_bonus_loi
    # per stack (Loi-mono rotation reward).
    consecutive_loi_casts: int = 0
    # Vô Tướng Phong (Formless Wind) absorber charges — remaining count of
    # debuff/CC applications the buff will negate. Initialized to
    # ``effect_overrides["BuffVoTuongPhong"]["formless_charges"]`` on apply,
    # decremented each time ``inflict_debuff`` is gated by the buff. Buff
    # auto-expires when this hits 0; also cleared when the duration ticks
    # out naturally via ``tick_effects``.
    vo_tuong_phong_charges: int = 0
    # Phong Nhận Thực (Wind-Blade Erosion) stacks. Read via the data-driven
    # ``scaling_rules`` on ``DebuffPhongNhanThuc`` (source: ``stack:phong_nhan_thuc``)
    # to scale ``heal_taken_reduce`` / ``shield_taken_reduce`` /
    # ``final_dmg_taken_bonus`` linearly per stack. Cap from the meta's
    # ``stack_cap`` (default 7); cleared on debuff expiry. Stack increment
    # lives in ``inflict_debuff`` alongside burn/bleed/shock/poison blocks.
    phong_nhan_thuc_stacks = _StackProxy("DebuffPhongNhanThuc")
    # Trấn Sơn Hà Ấn (Mountain-River Suppression Seal) stacks. Read via
    # the data-driven ``scaling_rules`` on ``DebuffTranSonHa`` (source:
    # ``stack:tran_son_ha``) to scale ``final_dmg_bonus`` by -0.08/stack on
    # the holder — i.e. reduce the target's outgoing damage. Cap from the
    # meta's ``stack_cap`` (default 3); cleared on debuff expiry. Stack
    # increment lives in ``inflict_debuff`` alongside the other ``stack_kind``
    # blocks. The skill ``SkillSonHaAn`` reads
    # ``seal_refresh_dmg_bonus`` on the spec to compound its own strike when
    # the target already carries the seal — see ``casting.cast_skill``.
    tran_son_ha_stacks = _StackProxy("DebuffTranSonHa")
    # Địa Mạch Quy Chân (Earth Vein Return-to-Truth) stacks. Gained when
    # the holder of ``SkillDiaMachQuyChan`` takes damage from an enemy.
    # While stacks > 0, every attack the holder makes gets a flat bonus
    # of ``def_stat × 0.10 × stacks`` added to both ``effective_atk`` and
    # ``effective_matk`` — armor-to-power conversion. Stack cap 5; at cap
    # the holder auto-casts ``SkillDaiDiaMaiTang`` back at the attacker
    # and resets to 0. Increment + auto-cast hook live in
    # ``casting._bump_dia_mach_stack``; conversion read in
    # ``build_attack_stats``.
    # Phù Dao Trực Thượng altitude — gained while ``BuffPhuDao`` is active,
    # consumed by the next attack-skill cast (multiplies its base_dmg and
    # rolls a per-stack DebuffCuonBay rider). Per-cast tunables
    # (``altitude_max`` / ``per_stack_pct`` / ``rider_on_descent`` /
    # ``rider_chance_per_stack`` / ``reset_on_cc``) live on the buff's
    # ``effect_overrides`` entry. Reset on buff expiry, on dive consumption,
    # and when ``reset_on_cc`` fires.
    phu_dao_altitude: int = 0
    # On-hit: chance the actor lands a Sốc Điện stack on the target.
    shock_on_hit_pct: float = 0.0
    # After a normal turn, flat chance to immediately steal an extra turn
    # (independent of the SPD-based extra-turn roll — stacks additively).
    turn_steal_pct: float = 0.0

    # ── DoT damage amplifiers (any build can stack these) ──────────────────
    # Additive multiplier on ALL DoT ticks this combatant's own DoTs cause.
    # e.g. 0.25 → DoTs tick 25% harder.
    dot_dmg_bonus: float = 0.0
    # Per-DoT-kind multipliers stacked on top of ``dot_dmg_bonus``. Keyed by
    # stack_kind (``burn`` / ``bleed`` / ``poison``). Flat JSON keys
    # ``<kind>_dmg_bonus`` stay the data-side convention; translated into
    # this dict in build_combat_stats.
    dot_dmg_bonus_by_kind: dict[str, float] = field(default_factory=dict)
    # Per-attacker DoT-bonus contributions received while holding DoTs.
    # Keyed by attacker.key; values are dicts with {dot_dmg_bonus, burn, bleed,
    # poison, power, scales_hp_pct}. The live target.dot_*_bonus fields are the
    # SUM of all source entries — so multiple attackers' bonuses add together
    # instead of max-merging. ``power`` = max(actor.atk, actor.matk) at apply
    # time; ``scales_hp_pct`` = actor's dot_scales_hp_pct flag.
    dot_bonus_sources: dict = field(default_factory=dict)
    # When True, DoTs ticking on this combatant scale with the target's hp_max
    # (a rare late-game uniques opt-in). When False (default), DoTs scale with
    # the APPLIER's atk/matk power — the standard model. The flag propagates
    # from the attacker at DoT-apply time.
    dot_scales_hp_pct: bool = False

    # ── Phase-Lock invulnerability mode (Thập Nhật Chung Yên pattern) ────────
    # When ``phase_lock_config`` is set on an enemy and its HP drops below the
    # configured threshold for the first time, the enemy enters an
    # invulnerability phase: heals to full, gains MAX final_dmg_reduce + MAX
    # res on every element + shield = hp_max, and skips its own turns. Each
    # round those bonuses decay linearly back to their pre-phase values; if
    # the player can't kill the enemy before the timer expires, the player
    # dies. Config schema:
    #   {"trigger_hp_pct": 0.5, "duration": 10,
    #    "phase_name_vi": "Thập Nhật Chung Yên", "phase_emoji": "☀️"}
    phase_lock_config: dict | None = None
    phase_lock_active: bool = False
    phase_lock_triggered: bool = False
    phase_lock_remaining: int = 0
    # Snapshots so decay/expiry can restore the pre-phase profile cleanly
    # instead of leaving the enemy permanently buffed if the fight ends
    # mid-phase via some other path (player flee, max_turns, etc.).
    phase_lock_orig_dr: float = 0.0
    phase_lock_orig_res: dict[str, float] = field(default_factory=dict)
    phase_lock_orig_shield_base: int = 0
    # Cached starting deltas so per-turn decay subtracts a fixed step instead
    # of recomputing from a moving target.
    phase_lock_dr_step: float = 0.0
    phase_lock_res_step: float = 0.0

    # ── Skill-extras state ───────────────────────────────────────────────────
    # Per-skill cast counter — feeds the ``charge_bonus`` mechanic where a
    # skill detonates extra damage on every Nth cast. Keyed by skill_key.
    skill_cast_counts: dict[str, int] = field(default_factory=dict)
    # Per-skill chain counter — feeds the ``chain_skill.every`` mechanic where
    # a follow-up cast fires only every Nth top-level cast. Kept separate from
    # ``skill_cast_counts`` so a skill can wire both ``charge_bonus`` and
    # ``chain_skill: {every: N}`` without their counters colliding.
    skill_chain_counts: dict[str, int] = field(default_factory=dict)
    # Active summons spawned by ``summon_spec`` skills. Each entry:
    #   {"name": str, "vi": str, "element": str|None,
    #    "dmg": int, "turns": int, "emoji": str}
    # Optional per-summon fields (read by ``tick_summons``):
    #   ``crit_rating`` / ``crit_dmg_rating`` — enable per-tick crit rolls
    #   ``on_hit_debuff: {key, chance, overrides}`` — chance to stamp a debuff
    #   ``expire_finisher`` — {pct, element, emoji, vi}: when the LAST copy of
    #     this summon-group expires, fire ``pct × accumulated_dmg`` as a burst
    # ``_process_periodic`` ticks every entry: deals ``dmg`` to the opponent,
    # decrements ``turns``, and removes expired entries.
    summons: list[dict] = field(default_factory=list)
    # Per-summon-name running total of damage dealt during the current
    # batch's lifetime. Keyed by ``summon["vi"]``. Read by ``tick_summons``
    # to compute the ``expire_finisher`` burst on the LAST copy's expiry,
    # then popped so a re-summon starts fresh.
    summon_dmg_dealt: dict[str, int] = field(default_factory=dict)
    # Cửu Long Thần Hỏa Trận tunables — single dict (mirrors ``cuu_khuc``).
    # JSON gem thresholds contribute ``cuu_long: {dmg_pct_bonus, crit_rating,
    # crit_dmg_rating, stun_chance, finisher_pct}``; read by
    # ``maybe_spawn_summon`` to customise the 9-dragon swarm at spawn time.
    cuu_long: dict = field(default_factory=dict)
    # Vạn Kiếm Quy Tông — Sword-Heart stack counter. Built by consuming a
    # full sword swarm (10 summons of "Vạn Kiếm" → +5 stacks). Each stack
    # adds 5% kim damage in ``build_attack_stats`` (gated on skill element).
    # Capped at 10 by the consume path; resets per fight (Combatant rebuild).
    sword_heart_stacks = _StackProxy("sword_heart", store="stack_counters")
    # Per-Sword-Heart-stack damage reduction — granted by Hộ Thể Kiếm Cương's
    # passive (3% DR per stack). Folds into ``effective_damage_reduction``
    # alongside ``fortify_per_turn_pct``. Default 0 so a character without
    # the passive sees no DR bonus from stacks; flows through equip_stats
    # via ``passive.sword_heart_per_stack_dr`` on the skill JSON.
    sword_heart_per_stack_dr: float = 0.0
    # Damage amplifier for "Vạn Kiếm"-type summons — granted by Kiếm Tâm
    # Thông Minh's passive (+40 % to each sword's per-turn swing AND the
    # Quy Tông consume burst). Applied as a multiplicative bonus in
    # ``maybe_spawn_summon`` (spawn-time damage) and the consume-pass inside
    # ``tick_summons`` (burst damage). Default 0 so a character without
    # the passive sees no boost; flows through equip_stats via
    # ``passive.sword_summon_dmg_amp`` on the skill JSON.
    sword_summon_dmg_amp: float = 0.0

    def is_alive(self) -> bool:
        return self.hp > 0

    def take_damage(
        self, amount: int, is_dot: bool = False, bypass_shield: bool = False,
        is_echo: bool = False,
    ) -> int:
        """Apply ``amount`` damage with PoE-style Energy Shield absorption.

        Pipeline (in order):
          1. Fortify brace prime — any non-DoT hit primes the next-turn
             damage-reduction brace.
          2. Element conversion — ``damage_taken_convert_pct`` of the amount
             is reclassified as elemental damage and mitigated by the holder's
             matching resistance. The post-mitigation chunk rejoins the
             unconverted remainder.
          3. Defer split — when both ``damage_defer_turns`` and
             ``damage_defer_pct`` are > 0, ``damage_defer_pct`` of the
             post-conversion amount is split into N installments queued onto
             ``deferred_damage_queue`` (FIFO). The rest moves to step 4.
          4. Energy Shield absorption (NON-DoT only) — ``shield`` is consumed
             FIRST. Damage that fits inside the shield never touches HP. Any
             leftover spills into HP. DoT damage skips this step entirely
             (matches PoE's chaos damage bypassing ES).
          5. HP damage — the leftover (or full DoT amount) hits ``hp``.

        Any non-DoT damage that landed (whether absorbed by shield or hit HP)
        triggers the recharge delay: ``shield_recharge_pause`` is set to
        ``shield_recharge_delay``. The periodic phase decrements it; while
        > 0, ``shield_regen_pct/flat`` are skipped.

        ``is_dot=True`` marks the call as DoT-tick damage — bypasses the
        Fortify brace prime AND the shield, going straight to HP.

        Returns the amount applied to HP this call (queued chunks + shield
        absorption are excluded).
        """
        if amount <= 0:
            return 0
        # Hit-taken counter for ``proc_on_hits_taken`` skills. Counts only
        # non-DoT calls so passive bleed/burn ticks don't game the trigger;
        # incremented once per call regardless of where damage routes
        # (shield, HP, deferred queue, element conversion).
        if not is_dot:
            self.hits_taken += 1
            # Phi Thiên Lăng Vân — non-DoT hit taken drains 2 Phong Vân stacks.
            if self.phong_van_stacks > 0:
                self.phong_van_stacks = max(0, self.phong_van_stacks - 2)
        # Fortify Aura: any non-DoT incoming damage primes the post-hit brace
        # for the next combat resolution. Set BEFORE conversion/defer so even
        # a fully-deferred installment still arms the brace this turn.
        if not is_dot and self.fortify_post_hit_dr_pct > 0:
            self.fortify_braced_turns = max(self.fortify_braced_turns, 1)

        # Step 1 — element conversion (self-mitigation through holder's res
        # for the named element). Loops over ``damage_taken_convert_pct`` so
        # any element (and any combination) routes through this generic path.
        # Active-effect modifiers fold in via ``damage_convert_<elem>`` and
        # ``res_<elem>`` stat_bonus keys so a buff like Vô Tướng Thiên Ma can
        # pump conversion + resistance for its lifetime.
        from src.game.engine.effects import get_combat_modifiers
        active_mods = get_combat_modifiers(self)
        elements_in_play = set(self.damage_taken_convert_pct)
        elements_in_play.update(
            elem.removeprefix("damage_convert_")
            for elem in active_mods
            if elem.startswith("damage_convert_")
            and active_mods[elem] > 0
        )
        if elements_in_play:
            from src.game.constants.balance import MAX_ELEMENTAL_RES
            effective_convert = {
                elem: max(
                    0.0,
                    self.damage_taken_convert_pct.get(elem, 0.0)
                    + float(active_mods.get(f"damage_convert_{elem}", 0.0)),
                )
                for elem in elements_in_play
            }
            total_pct = sum(p for p in effective_convert.values() if p > 0)
            # Cap aggregate conversion at 100% so the holder always takes at
            # least the unconverted remainder.
            total_pct = min(1.0, total_pct)
            unconverted = int(amount * (1.0 - total_pct))
            new_amount = unconverted
            for elem, pct in effective_convert.items():
                if pct <= 0:
                    continue
                # Each element's slice scales proportionally if total_pct was
                # capped (so {hoa: 0.7, thuy: 0.6} effectively becomes
                # {hoa: 7/13, thuy: 6/13} of the converted total).
                raw_total = sum(effective_convert.values())
                share = pct / raw_total if raw_total > 0 else 0
                converted = int(amount * total_pct * share)
                if converted <= 0:
                    continue
                # Effective resistance = base + active stat_bonus mod, clamped
                # by the holder's per-element cap (soft cap for player + bonus,
                # MAX_ELEMENTAL_RES for enemies).
                from src.game.engine.effects import effective_res_cap
                base_res = self.resistances.get(elem, 0.0)
                mod_res = float(active_mods.get(f"res_{elem}", 0.0))
                elem_res = max(0.0, min(effective_res_cap(self, elem), base_res + mod_res))
                new_amount += max(1, int(converted * (1.0 - elem_res)))
            amount = new_amount

        # Step 2 — deferred-damage split
        n = self.damage_defer_turns
        pct = self.damage_defer_pct
        if n > 0 and pct > 0 and not is_dot:
            deferred_total = int(amount * pct)
            immediate = amount - deferred_total
            if deferred_total > 0:
                chunk = deferred_total // n
                remainder = deferred_total - chunk * n
                for i in range(n):
                    self.deferred_damage_queue.append(chunk + (1 if i < remainder else 0))
            amount = immediate

        # Step 2.5 — HP→MP redirect (Thủy Mặc Thiên Hoa "mana shield"). A
        # fraction of the post-conversion, post-defer amount is paid out of
        # MP instead of HP/shield. Any portion the holder can't pay (mp <
        # required redirect) falls back into ``amount`` and continues through
        # shield + HP normally — a depleted mana pool can't tank infinite
        # damage. Skipped on DoT calls (matches the shield's chaos-bypass
        # semantics — sustained damage drains HP through the buff).
        if not is_dot and amount > 0:
            redirect_pct = float(active_mods.get("dmg_to_mp_pct", 0.0))
            if redirect_pct > 0 and self.mp > 0:
                want = int(amount * redirect_pct)
                paid = min(self.mp, want)
                if paid > 0:
                    self.mp -= paid
                    amount -= paid

        # Step 3 — Energy Shield absorption (PoE-style, non-DoT only). The
        # shield acts as a first-defense pool; damage that fits inside the
        # shield never touches HP. Any non-zero non-DoT damage primes the
        # recharge delay regardless of whether shield was 0 before — taking a
        # hit always pauses regen for the pre-configured window.
        # ``bypass_shield`` skips the absorption step but still pauses regen,
        # so a shielded target can't repair while being shield-pierced.
        # Snapshot pre-absorption amount so the Lôi Thần Khải hook below can
        # account for damage routed through both shield and HP in a single read.
        # Lưu Ly Thuẫn Thân — the aegis body has hp_max 1 and forces ALL damage
        # through the shield: true-damage, shield-pierce, and DoT can't bypass it.
        # So the shield-absorption step runs even for is_dot/bypass_shield calls.
        force_shield = self.shield_only_body
        if force_shield:
            bypass_shield = False
        shield_eligible = (not is_dot) or force_shield
        pre_absorb_amount = amount if shield_eligible else 0
        if shield_eligible and amount > 0:
            if self.shield > 0 and not bypass_shield:
                absorbed = min(self.shield, amount)
                self.shield -= absorbed
                amount -= absorbed
            self.shield_recharge_pause = max(
                self.shield_recharge_pause, self.shield_recharge_delay,
            )
            # Lưu Ly Bất Diệt (L9) — when a hit pierces the shattered shield and
            # would reach the 1-HP flesh, the aegis reforms once per fight:
            # restore part of the cap and negate this blow. Silent (the model has
            # no log handle) — the caller reads ``aegis_reform_just_triggered`` to
            # log, mirroring Endure. Without a charge, the leftover ends the body.
            if (
                force_shield
                and amount > 0
                and self.aegis_reform_charges > 0
                and self.aegis_reform_shield_pct > 0
            ):
                self.aegis_reform_charges -= 1
                self.shield = int(self.shield_cap() * self.aegis_reform_shield_pct)
                self.aegis_reform_just_triggered = True
                amount = 0

        # Step 4 — HP damage (leftover spill or full DoT amount).
        hp_before = self.hp
        self.hp = max(0, self.hp - amount)
        # Accumulate per-round damage taken for ``proc_on_heavy_hit_pct`` skills.
        # Counts HP loss only (shield-absorbed damage doesn't qualify as
        # "HP damage taken in this turn"). Reset to 0 in ``CombatSession.step``.
        self.damage_taken_this_turn += hp_before - self.hp

        # Step 4.4 — Hóa Côn absorption bank (Tiêu Dao Thần L9). While the Côn
        # form is up, every point of HP actually lost (all sources, DoT
        # included — the leviathan swallows the whole storm) is banked; when
        # the form expires, periodic/expiry.py releases the bank as capped
        # true damage + a heal. Inert without the form buff.
        if hp_before > self.hp and self.effects.get("BuffHoaCon", 0) > 0:
            self.td_con_bank += hp_before - self.hp

        # Step 4.5 — Damage→MP conduit (Cửu Thiên Lôi Giáp). A fraction of
        # the HP loss is channeled into MP for the holder. Reads from
        # ``active_mods`` so any future buff contributing the same key
        # stacks naturally. Skipped on DoT so sustained tick damage can't
        # trivialize the mana pool. Keyed on HP loss (not gross incoming)
        # so shield-absorbed hits don't print free MP.
        if not is_dot:
            hp_loss = hp_before - self.hp
            if hp_loss > 0:
                mp_gain_pct = float(active_mods.get("dmg_taken_mp_gain_pct", 0.0))
                if mp_gain_pct > 0 and self.mp < self.mp_max:
                    gain = int(hp_loss * mp_gain_pct)
                    if gain > 0:
                        self.mp = min(self.mp_max, self.mp + gain)

        # Step 4.6 — Defense-aegis stored-charge accumulator. Every active
        # aegis buff with an ``_aegis.store_charge`` block banks a fraction
        # of ``pre_absorb_amount`` (counting BOTH shield-absorbed and HP-loss
        # portions) into its own ``_stored_charge`` override. The discharge
        # hook in ``CombatSession._process_periodic`` reads it back on
        # natural expiry. See ``src.game.systems.combat.defense_aegis``.
        if not is_dot and pre_absorb_amount > 0:
            from src.game.systems.combat.defense_aegis import accumulate_stored_charge
            accumulate_stored_charge(self, pre_absorb_amount)

        # Step 4.7 — Thánh Sơn Bất Động Thể L9 Vạn Vật Quy Trần. While Kiên Cố is
        # FULL, a would-be-lethal NON-DoT hit cannot kill: HP is floored at 1 and a
        # slice of the shield cap is restored. Unlike endure there is NO cooldown —
        # the gate IS the full stack count, so any hard CC (which cracks a Kiên Cố
        # stack at the ``apply_effect`` stamp site, dropping below cap) disarms it
        # until rebuilt. DoTs bypass it (``is_dot``), keeping the wall vulnerable to
        # sustained pressure. Runs before Endure so the body's own last-stand claims
        # the kill. The restore routes through ``add_shield`` so it clamps at
        # shield_cap — repeated triggers (bypass-shield true damage never drains
        # the pool) must not compound shield past the cap.
        if (
            self.hp == 0
            and not is_dot
            and self.thanh_son_immovable_enabled
            and self.thanh_son_kien_co_cap > 0
            and self.thanh_son_kien_co_stacks >= self.thanh_son_kien_co_cap
        ):
            self.hp = 1
            if self.thanh_son_survive_shield_pct > 0:
                self.add_shield(int(self.shield_cap() * self.thanh_son_survive_shield_pct))
            self.thanh_son_immovable_just_triggered = True

        # Step 5 — Endure (Cội Nguồn Bất Tận). When a hit would kill the
        # holder and the cooldown is not engaged, clamp HP to a survival
        # floor instead and engage the cooldown. Repeats every
        # ``endure_cooldown`` turns — different from BuffBatTu (single-use)
        # and phoenix_revive (revive-from-death). DoTs can still kill if
        # they tick the holder while cooldown is active; that's intentional
        # so the build remains vulnerable to sustained pressure.
        if (
            self.hp == 0
            and self.endure_threshold_pct > 0
            and self.endure_remaining == 0
        ):
            self.hp = max(1, int(self.hp_max * self.endure_threshold_pct))
            self.endure_remaining = self.endure_cooldown
            self.endure_just_triggered = True

        # Step 6 — Âm Hồn Khế Ấn damage echo. A holder of ``DebuffAmHonKheAn``
        # echoes a fraction of every damaging hit (HP loss this call) back onto
        # ITSELF as TRUE damage (bypass shield/DR — routed via a recursive
        # ``take_damage(..., is_echo=True)``).
        #
        # RECURSION GUARD: ``is_echo`` short-circuits this block so the echo's
        # own ``take_damage`` never spawns a second echo (echo-of-echo = 0).
        # Gated strictly to non-DoT, non-echo damaging hits whose HP loss
        # ≥ ``min_source_dmg`` so chip/0-dmg taps don't echo.
        #
        # World-boss note: the echo reduces ``self.hp`` through the normal
        # ``take_damage`` HP path, so on a world-boss holder it is naturally
        # included in the cog's ``starting_hp - ending_hp`` contribution total
        # AND subject to the same per-attack ``dmg_cap`` — it can't bypass the
        # boss contribution cap. No separate routing needed.
        if not is_echo and not is_dot:
            hp_loss = hp_before - self.hp
            if hp_loss > 0:
                self._maybe_echo_damage(hp_loss)

        return amount

    def _maybe_echo_damage(self, source_hp_loss: int) -> None:
        """Fire the Âm Hồn Khế Ấn echo if this holder carries the brand.

        Reads ``damage_echo`` config off the brand's per-instance override.
        Echo = ``min(echo_cap_per_hit, int(source_hp_loss × echo_pct))`` dealt
        to this holder as TRUE damage (``is_echo=True`` recursion-guarded).
        """
        if self.effects.get("DebuffAmHonKheAn", 0) <= 0:
            return
        ovr = self.effect_overrides.get("DebuffAmHonKheAn") or {}
        cfg = ovr.get("damage_echo")
        if not isinstance(cfg, dict):
            return
        min_source = int(cfg.get("min_source_dmg", 0))
        if source_hp_loss < min_source:
            return
        echo_pct = float(cfg.get("echo_pct", 0.0))
        if echo_pct <= 0:
            return
        cap = int(cfg.get("echo_cap_per_hit", 0))
        echo = int(source_hp_loss * echo_pct)
        if cap > 0:
            echo = min(cap, echo)
        if echo <= 0:
            return
        # Silent HP mechanic — mirrors the other in-``take_damage`` effects
        # (element conversion, deferred-damage split, HP→MP redirect) which
        # all mutate HP without a log line (the model has no session handle).
        # The brand's effect is observable via the holder's HP drop.
        self.take_damage(echo, bypass_shield=True, is_echo=True)

    def consume_stacks(self, kind: str) -> int:
        """Zero the named stack counter and return how many were consumed.

        ``kind`` is the stack name without the trailing ``_stacks`` suffix —
        e.g. ``"burn"``, ``"bleed"``, ``"chan_hoa"``, ``"thuy_mark"``,
        ``"mana"``. Returns 0 when the field doesn't exist on the combatant
        so callers can dispatch via a stack-name string from skill JSON
        without needing per-kind guards.

        Replaces a fleet of identical ``consume_<kind>_stacks`` helpers that
        all did "zero the counter, return prior count". Skills with extra
        cleanup beyond resetting the counter (e.g. Cửu Khúc snapshot fields)
        should reset those fields explicitly at the call site after invoking
        this — keeping ``consume_stacks`` pure makes it safe to reuse for
        future stack kinds without growing per-kind branches.
        """
        field = f"{kind}_stacks"
        if not hasattr(self, field):
            return 0
        prior = int(getattr(self, field))
        setattr(self, field, 0)
        return prior

    def add_stack(self, kind: str, count: int = 1) -> int:
        """Add stacks to the named counter, clamped to the kind's cap.

        Returns the count actually gained (0 when the counter was already
        at cap, or when the kind isn't recognized — callers can dispatch
        via JSON strings without per-kind guards).

        Cap source by kind:
          * ``mana``         — reads the runtime field ``mana_stack_cap``
            (set by Khí Tu mana-stack constitutions and per-fight aura
            buffs that grow the cap dynamically).
          * everything else  — routes through ``effective_stack_cap`` using
            the ``_STACK_EFFECT_KEY`` map, so EffectMeta defaults,
            per-instance ``effect_overrides[...].stack_cap``, and
            ``stack_cap_bonuses`` from gear all fold in uniformly.

        Replaces a fleet of identical ``add_<kind>_stack`` helpers.
        """
        field = f"{kind}_stacks"
        if not hasattr(self, field):
            return 0
        if kind == "mana":
            cap = self.mana_stack_cap
        elif kind == "lietdiem_van_hoa":
            cap = self.lietdiem_van_hoa_cap  # per-body config field, like mana
        else:
            effect_key = _STACK_EFFECT_KEY.get(kind)
            if effect_key is None:
                return 0
            from src.game.engine.effects import effective_stack_cap
            cap = effective_stack_cap(self, effect_key)
        before = int(getattr(self, field))
        setattr(self, field, min(cap, before + count))
        return int(getattr(self, field)) - before

    def tick_cadence(self, counter_field: str, interval: int) -> bool:
        """Advance a turn/cast cadence counter; return True when it fires.

        Increments ``self.<counter_field>`` by one and returns True on the
        ``interval``-th call (new value a positive multiple of ``interval``).
        A non-positive ``interval`` disables the cadence: the counter is left
        untouched and False returned, so callers can pass a raw config field
        without a separate ``> 0`` guard.

        Consolidates the ``counter += 1; if counter % interval == 0`` idiom
        shared by the per-turn aura/cast cadences (Hoàng Cổ, Thái Bạch, Huyền
        Âm, Tịnh Quang, Liệt Diễm, Phi Thiên).
        """
        if interval <= 0:
            return False
        new_val = int(getattr(self, counter_field)) + 1
        setattr(self, counter_field, new_val)
        return new_val % interval == 0

    def shield_cap(self) -> int:
        """Maximum shield value this combatant can hold.

        Aggregates four sources:
          * ``shield_max_base``        — flat baseline (equipment implicit)
          * ``shield_max_flat``        — additive flat (affixes / constitutions)
          * ``shield_max_per_spd × effective_spd`` — buff-driven flat from
            spd (Quang Minh Tung Hoành Bộ). Effective spd folds in active
            ``spd_pct`` mods so the active buff (+20% spd) compounds the
            passive aura naturally.
          * ``shield_max_pct``         — multiplicative on the base+flat sum.
            Reads the field plus any ``shield_max_pct`` contributed by active
            effects. Skill-level ``passive`` blocks (Kim Chung Tráo etc.) are
            folded into ``shield_max_pct`` at build time via
            ``compute_skill_passive_stats`` so this read is the single source
            of truth without a per-call skill_keys scan.

        Formula: ``(base + flat + per_spd × eff_spd) * (1 + max_pct)``.
        """
        from src.game.engine.effects import get_combat_modifiers
        mods = get_combat_modifiers(self)
        per_spd = float(mods.get("shield_max_per_spd", 0.0))
        effective_spd = max(1, int(round(self.spd * (1.0 + mods.get("spd_pct", 0.0)))))
        spd_flat = int(per_spd * effective_spd)
        flat_total = self.shield_max_base + self.shield_max_flat + spd_flat
        total_pct = self.shield_max_pct + float(mods.get("shield_max_pct", 0.0))
        return max(0, int(flat_total * (1.0 + total_pct)))

    def add_shield(self, amount: int) -> int:
        """Add shield capped at shield_cap. Returns actual gained.

        Effect-driven ``shield_taken_reduce`` (e.g. DebuffPhongNhanThuc)
        reduces the incoming amount before clamping. Mirrors how
        ``heal_taken_reduce`` is applied in ``_apply_heal``; capped at 90%
        so stacked sources can't make shield gain strictly zero.
        """
        if amount <= 0:
            return 0
        # Lazy import to avoid cycle: effects → combatant.
        from src.game.engine.effects import get_combat_modifiers
        reduce = float(get_combat_modifiers(self).get("shield_taken_reduce", 0.0))
        if reduce > 0:
            amount = max(1, int(amount * (1.0 - min(0.90, reduce))))
        cap = self.shield_cap()
        before = self.shield
        self.shield = min(cap, self.shield + amount)
        return self.shield - before

    def consume_shield(self) -> int:
        """Empty the shield pool, return the consumed amount."""
        amt = self.shield
        self.shield = 0
        return amt

    def has_effect(self, effect: str) -> bool:
        return self.effects.get(effect, 0) > 0

    def stacks_of(self, effect: str) -> int:
        """Current stack count for a ``stackable`` effect (0 if none)."""
        return self.effect_stacks.get(effect, 0)

    def apply_effect(
        self, effect: str, duration: int, overrides: dict | None = None,
        stacks: int = 1,
    ) -> None:
        """Apply or refresh an effect with optional per-instance overrides.

        ``overrides`` lets a skill stamp custom magnitudes onto the holder's
        copy of the effect — e.g. a powerful skill can apply DebuffXeRach
        with ``stat_bonus={"res_all": -0.20}`` instead of the meta default
        (-0.08). Recognised keys: ``stat_bonus`` (dict, merged into the
        meta's stat_bonus per-key), ``dot_pct`` (float), ``dot_element``
        (str). When the same effect is reapplied the *stronger* value of
        each numeric field wins, so refreshing a -0.08 res shred with
        another -0.08 doesn't accidentally erase a previously stamped
        -0.20 from a heavier skill.
        """
        self.effects[effect] = max(self.effects.get(effect, 0), duration)
        if overrides:
            existing = self.effect_overrides.get(effect, {})
            self.effect_overrides[effect] = _merge_effect_overrides(existing, overrides)
        # Vô Tướng Phong refresh: each application resets the absorber pool
        # to the cast's ``formless_charges`` value. Snowballed spd_pct
        # growth in the override's stat_bonus is preserved by the merge
        # (larger-magnitude wins) — recast adds charges without erasing
        # accumulated spd from prior consumptions.
        if effect == "BuffVoTuongPhong":
            merged_ov = self.effect_overrides.get(effect) or overrides or {}
            self.vo_tuong_phong_charges = int(merged_ov.get("formless_charges", 3))
        # Declarative stacking — when the effect opts in (``EffectMeta.stackable``),
        # every (re)application banks ``stacks`` more, capped, stored generically
        # in ``effect_stacks``. Inert for the default non-stackable effects.
        from src.game.engine.effects import EFFECTS, effective_stack_cap
        _meta = EFFECTS.get(effect)
        if _meta is not None and getattr(_meta, "stackable", False):
            cap = effective_stack_cap(self, effect)
            self.effect_stacks[effect] = min(
                cap, self.effect_stacks.get(effect, 0) + max(0, int(stacks)),
            )
        # Thánh Sơn Bất Động Thể — Kiên Cố's weakness. ANY landed hard CC
        # (turn-skip class: stun / freeze / paralysis) cracks ONE Kiên Cố stack,
        # disarming the L9 immovable gate until rebuilt. Lives HERE — the single
        # effect-stamp site — rather than in ``inflict_debuff``, so direct
        # ``apply_effect`` CC paths (Bắc Minh freeze, stun_on_hit, retaliate
        # freezes, aura transfers) can't bypass the designed counterplay.
        # ``thanh_son_kien_co_just_cracked`` lets seams with a log handle
        # announce it (inflict_debuff immediately; the periodic announcer as a
        # fallback for the direct paths).
        if (
            _meta is not None
            and getattr(_meta, "skips_turn", False)
            and self.thanh_son_kien_co_cap > 0
            and self.thanh_son_kien_co_stacks > 0
        ):
            self.thanh_son_kien_co_stacks -= 1
            self.thanh_son_kien_co_just_cracked = True

    def tick_effects(self) -> list[str]:
        expired = [k for k, v in self.effects.items() if v <= 1]
        self.effects = {k: v - 1 for k, v in self.effects.items() if v > 1}
        # Per-instance overrides die with the effect — a refreshed application
        # has to re-stamp them rather than inherit a stale magnitude.
        for k in expired:
            self.effect_overrides.pop(k, None)
        # Generic declarative stacks: an expired stackable effect drops its
        # whole stack ("drop"). Future ``expire`` modes hook here.
        for k in expired:
            self.effect_stacks.pop(k, None)
        # Legacy per-mechanic stack / charge fields (pre-declarative) reset to
        # zero when their backing effect ends. This data table replaces ~25
        # hand-written ``if`` branches; migrating an entry onto ``effect_stacks``
        # (``EffectMeta.stackable``) retires its row.
        for k in expired:
            for attr in _LEGACY_EXPIRE_RESETS.get(k, ()):
                setattr(self, attr, type(getattr(self, attr))(0))
        return expired

    def tick_cooldowns(self) -> None:
        self.cooldowns = {k: max(0, v - 1) for k, v in self.cooldowns.items()}

    def skill_on_cooldown(self, skill_key: str) -> bool:
        return self.cooldowns.get(skill_key, 0) > 0

    def set_cooldown(self, skill_key: str, turns: int) -> None:
        """Set ``skill_key``'s cooldown, applying CDR via fractional accumulator.

        Each call adds ``turns * cooldown_reduce`` to ``cdr_credit`` and
        refunds whole turns once accumulated. So 12% CDR on 8-CD skills
        saves ~0.96 turn each cast — accumulating to a full +1 turn refund
        on the 2nd cast (then +1 every cast thereafter), vs. the old
        truncation behavior that arbitrarily refunded 1 turn for any
        non-zero CDR.
        """
        if self.cooldown_reduce > 0:
            self.cdr_credit += turns * self.cooldown_reduce
            refund = int(self.cdr_credit)
            self.cdr_credit -= refund
            effective = max(1, turns - refund)
        else:
            effective = turns
        self.cooldowns[skill_key] = effective



# ── Effect-override merge helper ─────────────────────────────────────────────

def _merge_effect_overrides(existing: dict, incoming: dict) -> dict:
    """Combine two override dicts so the *stronger* magnitude wins.

    Used when an effect is reapplied while still active — a fresh stamp
    from a heavy skill must not be erased by a weaker reapplication of
    the same effect from a lighter skill. Stronger means:

      • numeric stat_bonus values (debuffs are negative): farther from 0
      • dot_pct: the higher value
      • dot_element: incoming wins (str overrides aren't ranked)

    Unknown override keys are pass-through (incoming wins) so future
    fields don't need to retro-fit this helper.
    """
    out: dict = dict(existing)

    # stat_bonus is the most common override — merge per-stat by magnitude.
    inc_stats = incoming.get("stat_bonus") or {}
    if inc_stats:
        merged_stats = dict(out.get("stat_bonus") or {})
        for k, v in inc_stats.items():
            if k in merged_stats and isinstance(v, (int, float)) and not isinstance(v, bool):
                # Keep whichever value has the larger absolute magnitude.
                merged_stats[k] = v if abs(v) > abs(merged_stats[k]) else merged_stats[k]
            else:
                merged_stats[k] = v
        out["stat_bonus"] = merged_stats

    if "dot_pct" in incoming:
        out["dot_pct"] = max(float(incoming["dot_pct"]), float(out.get("dot_pct", 0.0)))
    if "dot_element" in incoming:
        out["dot_element"] = incoming["dot_element"]

    # Any other custom keys: incoming wins.
    for k, v in incoming.items():
        if k in ("stat_bonus", "dot_pct", "dot_element"):
            continue
        out[k] = v
    return out
