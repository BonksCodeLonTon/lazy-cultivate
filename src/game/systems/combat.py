"""Turn-based combat engine.

Rules:
- Physical skills scale with ATK; magical skills scale with MATK
- Luyện Thể path gains most ATK; Luyện Khí path gains most MATK
- Physical damage reduced by DEF (diminishing returns: def/(def+500), capped 75%)
- Magical damage reduced by elemental resistance only
- Out of mana or silenced → triggers physical auto-attack instead
- SPD determines turn order
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from typing import Optional

from src.data.registry import registry
from src.game.constants.balance import (
    ENEMY_RANK_BASE_ATK, ENEMY_RANK_BASE_MATK, ENEMY_RANK_BASE_DEF, ENEMY_RANK_BASE_EVASION,
    ENEMY_SCALE_MAX, ENEMY_HP_SCALE_FACTOR, ENEMY_DMG_BONUS_SCALE, ENEMY_BASE_ELEM_RES,
    ENEMY_REALM_LEVEL_STAT_MULT,
    TRUE_DMG_PCT_CAP,
    SPD_EXTRA_TURN_SCALE, SPD_EXTRA_TURN_MAX_PCT,
    HEAL_CRIT_CHANCE, HEAL_CRIT_MULT,
    SOUL_DRAIN_PER_PROC_PCT, SOUL_DRAIN_CAP_PCT, SOUL_DRAIN_SELF_GAIN_PCT,
    STAT_STEAL_PER_PROC_PCT, STAT_STEAL_CAP_PCT,
)
from src.game.constants.effects import EffectKey
from src.game.engine.damage import (
    apply_damage_scaling,
    build_attack_stats,
    build_defense_stats,
    calculate_damage,
    colorize_damage,
    effective_damage_reduction,
    spd_evasion_bonus,
)
from src.game.engine.drop import roll_drops
from src.game.engine.effects import (
    EFFECTS,
    EffectMeta,
    check_cc_skip_turn,
    check_prevents_skills,
    default_duration,
    get_combat_modifiers,
    get_periodic_damage,
)
from src.game.engine import linh_can_effects as lc_effects
from src.game.models.character import Character
from src.game.systems.combatant import Combatant


def _propagate_dot_bonuses(actor: Combatant, target: Combatant) -> None:
    """Record the attacker's DoT-amplification stats + scaling context in the
    target's per-source map, then recompute the live aggregate fields.

    Multiple attackers' amplifiers add together; re-applications from the same
    attacker do NOT compound (source map is keyed by ``actor.key``).
    """
    target.dot_bonus_sources[actor.key] = {
        "dot":            actor.dot_dmg_bonus,
        "burn":           actor.burn_dmg_bonus,
        "bleed":          actor.bleed_dmg_bonus,
        "poison":         actor.poison_dmg_bonus,
        "power":          max(actor.atk, actor.matk),
        "scales_hp_pct":  actor.dot_scales_hp_pct,
    }
    target.dot_dmg_bonus    = sum(s["dot"]    for s in target.dot_bonus_sources.values())
    target.burn_dmg_bonus   = sum(s["burn"]   for s in target.dot_bonus_sources.values())
    target.bleed_dmg_bonus  = sum(s["bleed"]  for s in target.dot_bonus_sources.values())
    target.poison_dmg_bonus = sum(s["poison"] for s in target.dot_bonus_sources.values())
    target.dot_scales_hp_pct = any(s["scales_hp_pct"] for s in target.dot_bonus_sources.values())


# Per-element field names carried from attacker to target on stack application.
# Max-merge keeps the strongest build in play when multiple attackers stack the
# same DoT on one target.
_STACK_BUILD_FIELDS: dict[str, tuple[str, ...]] = {
    "burn":  ("burn_stack_cap", "burn_per_stack_pct"),
    "bleed": ("bleed_stack_cap", "bleed_per_stack_pct", "bleed_heal_reduce"),
    "shock": ("shock_stack_cap", "shock_per_stack_pct"),
}


def _propagate_stack_build(actor: Combatant, target: Combatant, kind: str) -> None:
    """Copy the attacker's DoT-stack build flags onto the target (max-merge).

    kind ∈ {"burn", "bleed", "shock"}. Fire/bleed also propagate ``dot_can_crit``
    and the DoT damage-bonus aggregate. Shock only needs the cap + per-stack.
    """
    for field_name in _STACK_BUILD_FIELDS[kind]:
        a_val = getattr(actor, field_name)
        if a_val > getattr(target, field_name):
            setattr(target, field_name, a_val)
    if kind in ("burn", "bleed"):
        if actor.dot_can_crit:
            target.dot_can_crit = True
        _propagate_dot_bonuses(actor, target)


def _build_skill_obj(skill_key: str, skill_data: dict, mp_cost: int):
    """Hydrate a runtime Skill dataclass from a registry dict entry.

    Kept local-import because the Skill model pulls in several enums that
    the combat module otherwise doesn't need.
    """
    from src.game.models.skill import AttackType, DmgScale, Skill, SkillCategory
    return Skill(
        key=skill_key,
        vi=skill_data.get("vi", ""),
        en=skill_data.get("en", ""),
        realm=int(skill_data.get("realm", 1)),
        category=SkillCategory(skill_data.get("category", "attack")),
        mp_cost=mp_cost,
        cooldown=skill_data.get("cooldown", 1),
        base_dmg=skill_data.get("base_dmg", 0),
        element=skill_data.get("element"),
        attack_type=AttackType(skill_data.get("attack_type", "magical")),
        dmg_scale=DmgScale.from_raw(skill_data.get("dmg_scale")),
    )


# On-hit proc table driven by _run_on_hit_procs. Each entry describes a
# single chance-based proc that runs after a successful damaging hit.
#   chance_attr: actor attribute name holding the proc probability (float 0-1)
#   effect_key : EffectKey applied to target on success
#   stack_kind : optional key into _STACK_BUILD_FIELDS — when set, the
#                attacker's build flags propagate and a stack is added
#                (stack_add_fn is looked up on Combatant by name).
#   stack_add  : Combatant method name that adds a stack (returns stacks gained)
#   stacks_attr/cap_attr: attribute names read from target to format the log
#   log_fmt    : Vietnamese message template, positional or keyword placeholders
# Special cases (immune_hard_cc for stun, fixed 0.15 chance for freeze_on_skill)
# are kept as plain inline branches below the loop.
_ON_HIT_PROCS: tuple[dict, ...] = (
    {
        "chance_attr": "burn_on_hit_pct", "effect_key": EffectKey.DEBUFF_THIEU_DOT,
        "stack_kind":  "burn",  "stack_add": "add_burn_stack",
        "stacks_attr": "burn_stacks",  "cap_attr": "burn_stack_cap",
        "log_fmt": "    🔥 Thiêu Đốt kích hoạt! [×{stacks}/{cap}]",
    },
    {
        "chance_attr": "bleed_on_hit_pct", "effect_key": EffectKey.DEBUFF_CHAY_MAU,
        "stack_kind":  "bleed", "stack_add": "add_bleed_stack",
        "stacks_attr": "bleed_stacks", "cap_attr": "bleed_stack_cap",
        "log_fmt": "    🩸 Chảy Máu kích hoạt! [×{stacks}/{cap}]",
    },
    {
        "chance_attr": "shock_on_hit_pct", "effect_key": EffectKey.DEBUFF_SOC_DIEN,
        "stack_kind":  "shock", "stack_add": "add_shock_stack",
        "stacks_attr": "shock_stacks", "cap_attr": "shock_stack_cap",
        "log_fmt": "    ⚡ Sốc Điện kích hoạt! [×{stacks}/{cap}]",
    },
    {
        "chance_attr": "mark_on_hit_pct", "effect_key": EffectKey.DEBUFF_PHONG_AN,
        "log_fmt": "    🌀 Phong Ấn kích hoạt!",
    },
    {
        "chance_attr": "slow_on_hit_pct", "effect_key": EffectKey.DEBUFF_LAM_CHAM,
        "log_fmt": "    🐢 Làm Chậm kích hoạt!",
    },
    {
        "chance_attr": "heal_reduce_on_hit_pct", "effect_key": EffectKey.DEBUFF_CAT_DUT,
        "log_fmt": "    ✂️ Cắt Đứt kích hoạt!",
    },
)


def effective_spd(combatant: Combatant) -> int:
    """Live SPD after applying active spd_pct modifiers (BuffTangToc, DebuffLamCham…)."""
    mods = get_combat_modifiers(combatant)
    return max(1, round(combatant.spd * (1.0 + mods.get("spd_pct", 0.0))))


def spd_extra_turn_pct(actor_spd: int, target_spd: int) -> float:
    """Fractional chance that the actor takes a bonus action after its turn.

    Returns 0 when actor is not faster than target. Scales with relative SPD gap
    and is clamped at SPD_EXTRA_TURN_MAX_PCT so SPD alone never fully locks out
    the slower side.
    """
    if actor_spd <= target_spd:
        return 0.0
    gap_pct = (actor_spd - target_spd) / max(1, target_spd)
    return min(SPD_EXTRA_TURN_MAX_PCT, gap_pct * SPD_EXTRA_TURN_SCALE)


class CombatEndReason(StrEnum):
    PLAYER_WIN = "player_win"
    PLAYER_DEAD = "player_dead"
    PLAYER_FLED = "player_fled"
    MAX_TURNS = "max_turns"


@dataclass
class CombatAction:
    actor: str
    skill_key: str
    target: str
    damage: int
    is_crit: bool
    is_evaded: bool
    effects_applied: list[str] = field(default_factory=list)
    mp_restored: int = 0
    hp_restored: int = 0
    log_line: str = ""


@dataclass
class CombatResult:
    reason: CombatEndReason
    turns: int
    log: list[str]
    loot: list[dict]
    merit_gained: int
    karma_gained: int


@dataclass
class CombatSession:
    player: Combatant
    enemy: Combatant
    player_skill_keys: list[str]
    rng: random.Random = field(default_factory=random.Random)
    turn: int = 0
    max_turns: int = 30  # full rounds (player + enemy each act once per round)
    log: list[str] = field(default_factory=list)
    loot_qty_multiplier: float = 1.0  # >1.0 for elite/upgraded-rank encounters
    # Scales drop-roll weights in the drop engine. Independent of quantity:
    #   luck_pct  = 1.0 → each drop entry's effective weight is ×2
    # Caller passes the grade's luck_pct (0.0 / 0.20 / 0.50 / 1.0 / 2.0) so rare
    # entries (low-weight, low-activation pools) become meaningfully more likely
    # at higher grades instead of only multiplying the amount when they land.
    loot_luck_pct: float = 0.0

    def _actor_phase(
        self, actor: Combatant, target: Combatant, actor_is_player: bool
    ) -> Optional[CombatResult]:
        """Run actor's normal turn plus a possible SPD-driven extra action.

        Returns a CombatResult if the phase ends the fight, else None.
        """
        self._take_turn(actor, target)
        if not target.is_alive():
            return self._victory() if actor_is_player else self._defeat()

        # Extra-turn roll: faster combatant may get a bonus action this round
        extra_pct = spd_extra_turn_pct(effective_spd(actor), effective_spd(target))
        if extra_pct > 0 and self.rng.random() < extra_pct:
            self.log.append(
                f"  💨 **{actor.name}** vượt tốc độ — hành động thêm một lần!"
            )
            self._take_turn(actor, target)
            if not target.is_alive():
                return self._victory() if actor_is_player else self._defeat()
        # Lôi-build: flat turn-steal roll, independent of SPD gap. Fires at most
        # once per phase so even a maxed-out build can't lock the opponent out.
        if actor.turn_steal_pct > 0 and self.rng.random() < actor.turn_steal_pct:
            self.log.append(
                f"  ⚡ **{actor.name}** cướp lượt — **Lôi Tốc Hành Động!**"
            )
            self._take_turn(actor, target)
            if not target.is_alive():
                return self._victory() if actor_is_player else self._defeat()
        return None

    def step(self) -> tuple[list[str], Optional[CombatResult]]:
        """Process one full round: player acts → enemy acts → periodic effects.

        Returns (new_log_lines, result_if_over).
        result is None while the fight is still ongoing.
        """
        if self.turn >= self.max_turns:
            return ([], CombatResult(
                reason=CombatEndReason.MAX_TURNS,
                turns=self.turn, log=self.log, loot=[], merit_gained=0, karma_gained=0,
            ))

        start_idx = len(self.log)
        self.turn += 1
        self.log.append(f"\n**— Lượt {self.turn} —**")

        result = self._actor_phase(self.player, self.enemy, actor_is_player=True)
        if result is not None:
            return (self.log[start_idx:], result)

        result = self._actor_phase(self.enemy, self.player, actor_is_player=False)
        if result is not None:
            return (self.log[start_idx:], result)

        # Cooldowns tick for both after the full round
        self.player.tick_cooldowns()
        self.enemy.tick_cooldowns()

        # Periodic effects (DoTs, HP/MP regen, Linh Căn procs) once per round
        self._process_periodic(self.player)
        self._process_periodic(self.enemy)
        if not self.player.is_alive():
            return (self.log[start_idx:], self._defeat())
        if not self.enemy.is_alive():
            return (self.log[start_idx:], self._victory())

        return (self.log[start_idx:], None)

    def run(self) -> CombatResult:
        """Run all rounds to completion (used for AFK/tick systems)."""
        self.log.append(f"⚔️ **{self.player.name}** vs **{self.enemy.name}**")
        while True:
            _, result = self.step()
            if result is not None:
                return result

    def _take_turn(self, actor: Combatant, target: Combatant) -> None:
        # Pre-turn: Phong Linh Căn — target may fully dodge the incoming attack
        if lc_effects.try_dodge(target, self.rng, self.log):
            return

        # CC check — any skips_turn effect (stun/freeze/knockup) or probabilistic paralysis
        cc_key = check_cc_skip_turn(actor, self.rng)
        if cc_key:
            meta = EFFECTS.get(cc_key)
            cc_name = meta.vi if meta else cc_key
            self.log.append(f"  💤 **{actor.name}** bị **{cc_name}**, bỏ lượt.")
            return

        # Silence check — CCMuted / CCInterrupt prevents skill use
        silent_key = check_prevents_skills(actor)
        if silent_key:
            meta = EFFECTS.get(silent_key)
            cc_name = meta.vi if meta else silent_key
            self.log.append(f"  🔇 **{actor.name}** bị **{cc_name}**, không thể dùng kỹ năng.")
            self._auto_attack(actor, target)
            return

        # Pre-turn: Quang Linh Căn — actor may cleanse a debuff before acting
        lc_effects.try_cleanse(actor, self.rng, self.log)

        skill_key, reason = self._choose_skill(actor)
        if not skill_key:
            # Report the ACTUAL cause (cooldown vs mana vs no-skills-known) so
            # the player isn't misled into thinking they lack MP when they're
            # really just waiting on cooldowns.
            if reason == "cooldown":
                self.log.append(f"  ⏳ **{actor.name}** mọi kỹ năng đang hồi — tấn công cơ bản.")
            elif reason == "mana":
                self.log.append(f"  💤 **{actor.name}** không đủ linh lực — tấn công cơ bản.")
            else:
                self.log.append(f"  💢 **{actor.name}** không có kỹ năng khả dụng — tấn công cơ bản.")
            self._auto_attack(actor, target)
            return

        skill_data = registry.get_skill(skill_key)
        if not skill_data:
            return

        # Default aligned with _choose_skill (999) so a skill with a missing
        # mp_cost field is treated identically in both paths.
        mp_cost = skill_data.get("mp_cost", 999)
        if actor.mp < mp_cost:
            self.log.append(
                f"  💤 **{actor.name}** không đủ MP cho *{skill_data.get('vi', skill_key)}* "
                f"({actor.mp}/{mp_cost}) — tấn công cơ bản."
            )
            self._auto_attack(actor, target)
            return

        actor.mp = max(0, actor.mp - mp_cost)
        base_dmg = skill_data.get("base_dmg", 0)

        actor_mods = get_combat_modifiers(actor)
        target_mods = get_combat_modifiers(target)

        if base_dmg > 0:
            skill_obj = _build_skill_obj(skill_key, skill_data, mp_cost)
            attack_stats = build_attack_stats(actor, target, actor_mods, skill_obj.element)
            defense_stats = build_defense_stats(target, target_mods, actor, spd_evasion_bonus)
            # Pre-damage: Kim Linh Căn — may gain elemental penetration this hit
            pen_pct = lc_effects.get_pen_pct(actor, self.rng, self.log)
            result = calculate_damage(skill_obj, attack_stats, defense_stats, self.rng, pen_pct)
            dmg = result.final
            crit_tag = " 💥BẠO KÍCH!" if result.is_crit else ""

            if result.is_evaded:
                self.log.append(
                    f"  🌀 **{actor.name}** dùng *{skill_data['vi']}* → **{target.name}** né tránh!"
                )
            else:
                # Target damage reduction → actor's HP/MP/evasion/shield/mana-stack scaling
                target_dr = effective_damage_reduction(target, target_mods)
                if target_dr > 0:
                    dmg = int(dmg * (1.0 - target_dr))
                dmg = apply_damage_scaling(dmg, actor, actor_mods)

                # Moc build: consume queued heal→damage built up from prior heals
                if actor.queued_heal_dmg > 0:
                    dmg += actor.queued_heal_dmg
                    self.log.append(
                        f"    🌿 Dưỡng Sinh Hóa Sát — +{actor.queued_heal_dmg:,} ST từ máu hồi"
                    )
                    actor.queued_heal_dmg = 0

                # Apply Thổ shield (absorbs before HP loss)
                if target.shield > 0:
                    absorbed = min(target.shield, dmg)
                    target.shield -= absorbed
                    dmg -= absorbed
                    if absorbed > 0:
                        self.log.append(f"    🛡️ Khiên [Thổ] hấp thụ {absorbed:,} sát thương!")

                # BuffBatTu — prevent killing blow once
                if dmg >= target.hp and target.has_effect(EffectKey.BUFF_BAT_TU):
                    dmg = target.hp - 1
                    target.effects.pop(EffectKey.BUFF_BAT_TU, None)
                    self.log.append(f"    💫 **{target.name}** kích hoạt **Bất Tử** — sống sót!")

                target.hp = max(0, target.hp - dmg)
                skill_elem = skill_data.get("element")
                dmg_tag = colorize_damage(f"-{dmg:,} HP", skill_elem)
                self.log.append(
                    f"  ⚡ **{actor.name}** dùng *{skill_data['vi']}* → {dmg_tag}{crit_tag}"
                    f" | {target.name}: {target.hp:,}/{target.hp_max:,} HP"
                )

                # True damage — Kim playstyle. Percentage of target max HP that
                # bypasses def, res, and damage-reduction. Combined skill pct
                # (skill_data["true_dmg_pct"]) and passive pct (actor.true_dmg_pct),
                # capped at TRUE_DMG_PCT_CAP per hit to prevent 1-shot abuse.
                skill_true_pct = float(skill_data.get("true_dmg_pct", 0.0))
                total_true_pct = min(
                    TRUE_DMG_PCT_CAP, skill_true_pct + actor.true_dmg_pct,
                )
                if target.is_alive() and total_true_pct > 0:
                    true_dmg = max(1, int(target.hp_max * total_true_pct))
                    if result.is_crit:
                        true_dmg = int(true_dmg * 1.5)
                    target.hp = max(0, target.hp - true_dmg)
                    true_tag = colorize_damage(f"-{true_dmg:,} HP", None, true_dmg=True)
                    self.log.append(
                        f"    🗡️ **Chân Thương** xuyên mọi phòng ngự → {true_tag}"
                        f" ({total_true_pct * 100:.1f}% máu)"
                    )

                self._run_on_hit_procs(actor, target, is_crit=result.is_crit)
                self._apply_mana_gains(actor, dmg)
                self._apply_reactive_damage(actor, target, dmg)

                # On-hit: Linh Căn procs (consolidated in effects.py)
                lc_effects.on_hit(actor, target, dmg, result.is_crit, self.rng, self.log)

            # Apply skill debuff/CC effects to target
            self._apply_skill_effects(skill_data, actor, target, hit=not result.is_evaded)

        else:
            # Support / defense skill — applies effects to self (actor)
            self.log.append(f"  🛡️ **{actor.name}** dùng *{skill_data['vi']}*")
            self._apply_support_skill(skill_data, actor, target)

        actor.set_cooldown(skill_key, skill_data.get("cooldown", 1))

    def _apply_support_skill(
        self, skill_data: dict, actor: Combatant, target: Combatant
    ) -> None:
        """Handle a support/defense skill: instant heals and buff application."""
        effect_chances: dict[str, float] = skill_data.get("effect_chances", {})
        for effect_key in skill_data.get("effects", []):
            meta = EFFECTS.get(effect_key)

            if effect_key == "HpRegen":
                # Instant HP heal (10% of max) — routed through _apply_heal so
                # bleed reduction, clamp, and queued_heal_dmg all apply.
                actor_mods = get_combat_modifiers(actor)
                heal_mult = 1.0 + actor.heal_pct + actor_mods.get("hp_regen_pct", 0.0)
                requested = max(1, int(actor.hp_max * 0.10 * heal_mult))
                if actor.bleed_stacks > 0 and actor.bleed_heal_reduce > 0:
                    self.log.append(
                        f"    🩸 *Chảy Máu giảm hiệu lực hồi máu {actor.bleed_heal_reduce * 100:.0f}%*"
                    )
                applied = self._apply_heal(actor, requested)
                self.log.append(f"    ❤️ +{applied:,} HP")

            elif effect_key == "MpRegen":
                # Instant MP restore (10% of max)
                regen = max(1, actor.mp_max // 10)
                actor.mp = min(actor.mp_max, actor.mp + regen)
                self.log.append(f"    💙 +{regen:,} MP")

            elif meta and meta.kind.value == "buff":
                # Apply buff to self (actor)
                dur = default_duration(effect_key)
                actor.apply_effect(effect_key, dur)
                self.log.append(
                    f"    {meta.emoji} **{meta.vi}** ({dur}t) — {meta.description_vi}"
                )

            elif meta and meta.kind.value in ("debuff", "cc"):
                # CC skills with base_dmg=0 that debuff the target (e.g. CCBind skill)
                base_chance = effect_chances.get(effect_key, 1.0)
                effective_chance = base_chance * (1.0 - target.debuff_immune_pct)
                if effective_chance >= 1.0 or self.rng.random() < effective_chance:
                    self._inflict_debuff(effect_key, meta, target, actor=actor)

    def _apply_skill_effects(
        self, skill_data: dict, actor: Combatant, target: Combatant, hit: bool
    ) -> None:
        """Apply all effect_keys from a skill's effects list to the appropriate target.

        For debuffs/CC: checks the skill's effect_chances dict (default 1.0) multiplied
        by (1 - target.debuff_immune_pct) to get the effective proc probability.

        Special keywords handled in-line (not in the EFFECTS registry):
          - ``ConsumeBurnBurst``: detonate all burn stacks on the target for
            burst damage proportional to stack count.
        """
        effect_chances: dict[str, float] = skill_data.get("effect_chances", {})
        for effect_key in skill_data.get("effects", []):
            # ── Special: burn burst ───────────────────────────────────────────
            if effect_key == "ConsumeBurnBurst":
                if hit and target.burn_stacks > 0:
                    self._burst_burn(actor, target, skill_data)
                continue
            # ── Special: Thủy mana-stack burst ────────────────────────────────
            if effect_key == "ConsumeManaBurst":
                if hit and actor.mana_stacks > 0:
                    self._burst_mana_stacks(actor, target, skill_data)
                continue
            # ── Special: Thổ shield burst ────────────────────────────────────
            if effect_key == "ConsumeShieldBurst":
                if hit and actor.shield > 0:
                    self._burst_shield(actor, target, skill_data)
                continue
            # ── Special: Âm soul-drain on skill hit ──────────────────────────
            # The skill may specify ``soul_drain_procs`` for multi-proc bursts;
            # default 1 mirrors the on-hit passive.
            if effect_key == "ApplySoulDrain":
                if hit:
                    for _ in range(max(1, int(skill_data.get("soul_drain_procs", 1)))):
                        self._apply_soul_drain(actor, target)
                continue
            # ── Special: Âm stat-steal on skill hit ──────────────────────────
            if effect_key == "ApplyStatSteal":
                if hit:
                    for _ in range(max(1, int(skill_data.get("stat_steal_procs", 1)))):
                        self._apply_stat_steal(actor, target)
                continue

            meta = EFFECTS.get(effect_key)
            if not meta:
                continue
            if meta.kind.value == "buff":
                # Self-buff (actor), e.g. a skill that deals damage AND grants a buff
                dur = default_duration(effect_key)
                actor.apply_effect(effect_key, dur)
                self.log.append(f"    {meta.emoji} **{actor.name}** nhận **{meta.vi}** ({dur}t)")
            elif hit and meta.kind.value in ("debuff", "cc"):
                base_chance = effect_chances.get(effect_key, 1.0)
                effective_chance = base_chance * (1.0 - target.debuff_immune_pct)
                if effective_chance >= 1.0 or self.rng.random() < effective_chance:
                    self._inflict_debuff(effect_key, meta, target, actor=actor)

    def _apply_heal(self, combatant: Combatant, amount: int) -> int:
        """Centralized heal: applies bleed heal-reduction, clamps to hp_max,
        and accumulates ``queued_heal_dmg`` for Moc's heal→damage conversion.

        When ``heal_can_crit`` is set (Mộc / Quang build flag), rolls
        HEAL_CRIT_CHANCE and multiplies by HEAL_CRIT_MULT before reduction —
        symmetric with ``dot_can_crit``.

        Returns the actual HP restored (post-reduction, post-clamp).
        """
        if amount <= 0:
            return 0
        if combatant.heal_can_crit and self.rng.random() < HEAL_CRIT_CHANCE:
            amount = int(amount * HEAL_CRIT_MULT)
            self.log.append(f"    💖BẠO HỒI! **{combatant.name}** +{amount:,} dự kiến")
        if combatant.bleed_stacks > 0 and combatant.bleed_heal_reduce > 0:
            amount = max(1, int(amount * (1.0 - min(0.90, combatant.bleed_heal_reduce))))
        room = max(0, combatant.hp_max - combatant.hp)
        applied = min(amount, room)
        combatant.hp += applied
        # Queue heal→damage for Moc builds on the applied amount (not the
        # requested amount — overheal is wasted)
        if combatant.damage_from_heal_pct > 0 and applied > 0:
            combatant.queued_heal_dmg += int(applied * combatant.damage_from_heal_pct)
        return applied

    def _apply_mana_gains(self, actor: Combatant, dmg: int) -> None:
        """Thủy build — MP leech + mana-stack accumulation per successful hit."""
        if actor.mp_leech_pct > 0:
            gain = max(1, int(dmg * actor.mp_leech_pct))
            applied = min(gain, max(0, actor.mp_max - actor.mp))
            if applied > 0:
                actor.mp += applied
                self.log.append(f"    💧 Hút Linh Khí → +{applied:,} MP")
        if actor.mana_stack_per_attack > 0 and actor.mana_stack_cap > 0:
            before = actor.mana_stacks
            actor.add_mana_stack(actor.mana_stack_per_attack)
            if actor.mana_stacks > before:
                self.log.append(
                    f"    💠 Linh Khí Tích Tụ [×{actor.mana_stacks}/{actor.mana_stack_cap}]"
                )

    def _apply_reactive_damage(
        self, actor: Combatant, target: Combatant, dmg: int
    ) -> None:
        """Thủy reflect + Thổ thorn — defender-triggered retaliation."""
        if target.reflect_pct > 0:
            self._apply_reflect(actor, target, dmg)
        if target.thorn_pct > 0 and actor.is_alive():
            self._apply_thorn(actor, target, dmg)

    def _run_on_hit_procs(
        self, actor: Combatant, target: Combatant, is_crit: bool
    ) -> None:
        """Roll each chance-based on-hit proc from _ON_HIT_PROCS plus special cases.

        Special cases not driven by the table:
          - stun_on_hit_pct respects target's hard-CC immunity
          - paralysis_on_crit fires only on crit, always lands
          - freeze_on_skill uses a fixed 0.15 chance independent of any attr
        """
        for spec in _ON_HIT_PROCS:
            chance = getattr(actor, spec["chance_attr"], 0.0)
            if chance <= 0 or self.rng.random() >= chance:
                continue
            effect_key = spec["effect_key"]
            dur = default_duration(effect_key)
            target.apply_effect(effect_key, dur)
            stack_kind = spec.get("stack_kind")
            if stack_kind:
                _propagate_stack_build(actor, target, stack_kind)
                getattr(target, spec["stack_add"])(1)
                self.log.append(spec["log_fmt"].format(
                    stacks=getattr(target, spec["stacks_attr"]),
                    cap=getattr(target, spec["cap_attr"]),
                ))
            else:
                self.log.append(spec["log_fmt"])

        # Thổ build: stun_on_hit — flat chance, respects hard-CC immunity
        if actor.stun_on_hit_pct > 0 and self.rng.random() < actor.stun_on_hit_pct:
            if target.immune_hard_cc:
                self.log.append(f"    🛡️ **{target.name}** miễn dịch Choáng!")
            else:
                target.apply_effect(EffectKey.CC_STUN, default_duration(EffectKey.CC_STUN))
                self.log.append(f"    💫 Choáng kích hoạt!")
        if is_crit and actor.paralysis_on_crit:
            target.apply_effect(EffectKey.CC_STUN, default_duration(EffectKey.CC_STUN))
            self.log.append(f"    ⚡ Tê Liệt khi Bạo Kích kích hoạt!")
        # Quang build: silence_on_crit — crit-gated CCMuted application.
        # Respects the same hard-CC immunity used by stun_on_hit.
        if is_crit and actor.silence_on_crit_pct > 0 and self.rng.random() < actor.silence_on_crit_pct:
            if target.immune_hard_cc:
                self.log.append(f"    🛡️ **{target.name}** miễn dịch Câm Lặng!")
            else:
                target.apply_effect(EffectKey.CC_MUTED, default_duration(EffectKey.CC_MUTED))
                self.log.append(f"    ✨ Thánh Quang Chế Ngự — câm lặng kích hoạt!")
        if actor.freeze_on_skill and self.rng.random() < 0.15:
            dur = default_duration(EffectKey.DEBUFF_DONG_BANG)
            target.apply_effect(EffectKey.DEBUFF_DONG_BANG, dur)
            self.log.append(f"    🧊 Đông Băng kích hoạt!")
        # Âm build: Hồn Phệ — permanently drain target.hp_max, transfer half
        # to actor. Capped to avoid trivializing bosses.
        if actor.soul_drain_on_hit_pct > 0 and self.rng.random() < actor.soul_drain_on_hit_pct:
            self._apply_soul_drain(actor, target)
        # Âm build: Đạo Pháp Thôn Phệ — steal atk/matk/def from target.
        if actor.stat_steal_on_hit_pct > 0 and self.rng.random() < actor.stat_steal_on_hit_pct:
            self._apply_stat_steal(actor, target)
        # Aura-on-hit from active buffs — e.g. BuffHanKhi spreads Làm Chậm to
        # anyone the holder strikes. Iterate the actor's effects once and
        # fire any aura hooks their EffectMeta declares.
        for effect_key in list(actor.effects.keys()):
            aura_meta = EFFECTS.get(effect_key)
            if aura_meta is None or aura_meta.aura_on_hit is None:
                continue
            aura_effect, chance = aura_meta.aura_on_hit
            if self.rng.random() >= chance:
                continue
            aura_dst_meta = EFFECTS.get(aura_effect)
            if aura_dst_meta is None:
                continue
            # Respect hard-CC immunity for stun/silence-style auras.
            if target.immune_hard_cc and (
                aura_dst_meta.skips_turn or aura_dst_meta.prevents_skills
            ):
                continue
            effective = 1.0 - target.debuff_immune_pct
            if effective < 1.0 and self.rng.random() >= effective:
                continue
            target.apply_effect(aura_effect, default_duration(aura_effect))
            self.log.append(
                f"    {aura_meta.emoji} Hào quang **{aura_meta.vi}** "
                f"→ {aura_dst_meta.emoji} {aura_dst_meta.vi}"
            )

    @staticmethod
    def _snapshot_original(combatant: Combatant, field: str, live_value: int) -> int:
        """Lazily record the combatant's starting value for a stat.

        Soul-drain and stat-steal cap math references pre-mutation snapshots;
        we capture the first-seen value instead of baking it into every call
        site of build_*_combatant. Returns the snapshot (live value if unset).
        """
        current = getattr(combatant, field, 0)
        if current:
            return current
        setattr(combatant, field, live_value)
        return live_value

    def _apply_soul_drain(self, actor: Combatant, target: Combatant) -> None:
        """Permanently shrink target.hp_max; transfer part to actor.

        No-op once the cap (SOUL_DRAIN_CAP_PCT of target's starting hp_max)
        is reached. The drained amount also removes current HP in parallel
        so the immediate fight impact matches the long-term one.
        """
        start = self._snapshot_original(target, "hp_max_original", target.hp_max)
        cap = int(start * SOUL_DRAIN_CAP_PCT)
        remaining = cap - target.hp_max_drained
        if remaining <= 0:
            return
        drain = min(remaining, max(1, int(start * SOUL_DRAIN_PER_PROC_PCT)))
        target.hp_max_drained += drain
        target.hp_max = max(1, target.hp_max - drain)
        target.hp = min(target.hp, target.hp_max)
        gain = max(1, int(drain * SOUL_DRAIN_SELF_GAIN_PCT))
        actor.hp_max += gain
        actor.hp = min(actor.hp_max, actor.hp + gain)
        self.log.append(
            f"    🌑 **Hồn Phệ** — **{target.name}** -{drain:,} HP Max "
            f"(tổng {target.hp_max_drained:,}/{cap:,}) → **{actor.name}** +{gain:,} HP Max"
        )

    def _apply_stat_steal(self, actor: Combatant, target: Combatant) -> None:
        """Transfer a slice of target's atk/matk/def into the actor.

        Each stat has its own cap (STAT_STEAL_CAP_PCT of the snapshot) so a
        tank's DEF, a mage's MATK, and a fighter's ATK are all partially
        stealable without any single axis dominating.
        """
        labels = [("atk", "stolen_atk", "atk_original", "Công"),
                  ("matk", "stolen_matk", "matk_original", "Pháp"),
                  ("def_stat", "stolen_def", "def_stat_original", "Phòng")]
        stolen_parts: list[str] = []
        for live_attr, stolen_attr, snap_attr, label in labels:
            start = self._snapshot_original(target, snap_attr, getattr(target, live_attr))
            if start <= 0:
                continue
            cap = int(start * STAT_STEAL_CAP_PCT)
            already = getattr(target, stolen_attr)
            remaining = cap - already
            if remaining <= 0:
                continue
            steal = min(remaining, max(1, int(start * STAT_STEAL_PER_PROC_PCT)))
            setattr(target, stolen_attr, already + steal)
            setattr(target, live_attr, max(0, getattr(target, live_attr) - steal))
            # Mirror: actor grows by the same amount (tracked on the actor as
            # "stolen_*" for display symmetry; combat math reads live stats).
            setattr(actor, stolen_attr, getattr(actor, stolen_attr) + steal)
            setattr(actor, live_attr, getattr(actor, live_attr) + steal)
            stolen_parts.append(f"+{steal} {label}")
        if stolen_parts:
            self.log.append(
                f"    🩶 **Đạo Pháp Thôn Phệ** — **{actor.name}** cướp "
                f"{' / '.join(stolen_parts)} từ **{target.name}**"
            )

    def _apply_reflect(
        self, attacker: Combatant, defender: Combatant, dmg: int
    ) -> None:
        """Reflect a portion of damage dealt to ``defender`` back at ``attacker``.

        When ``defender.reflect_applies_effects`` is True, the defender's own
        on-hit proc flags (freeze_on_skill, slow_on_hit_pct, burn_on_hit_pct,
        bleed_on_hit_pct) also roll against the attacker — the mirror throws
        the defender's build flavor back at them.
        """
        if not attacker.is_alive() or dmg <= 0:
            return
        reflected = max(1, int(dmg * defender.reflect_pct))
        attacker.hp = max(0, attacker.hp - reflected)
        # Reflect paints in the defender's element (mirror = their flavor).
        reflect_tag = colorize_damage(f"-{reflected:,} HP", defender.element)
        self.log.append(
            f"    🪞 **{defender.name}** phản đòn → **{attacker.name}** {reflect_tag}"
        )
        if not defender.reflect_applies_effects:
            return

        # Mirror the defender's on-hit effect flags back at the attacker.
        if defender.freeze_on_skill and self.rng.random() < 0.25:
            dur = default_duration(EffectKey.DEBUFF_DONG_BANG)
            attacker.apply_effect(EffectKey.DEBUFF_DONG_BANG, dur)
            self.log.append(f"    🧊 Mirror Đóng Băng — **{attacker.name}** bị đông cứng!")
        if defender.slow_on_hit_pct > 0 and self.rng.random() < defender.slow_on_hit_pct:
            dur = default_duration(EffectKey.DEBUFF_LAM_CHAM)
            attacker.apply_effect(EffectKey.DEBUFF_LAM_CHAM, dur)
            self.log.append(f"    🐢 Mirror Làm Chậm — **{attacker.name}**")
        if defender.burn_on_hit_pct > 0 and self.rng.random() < defender.burn_on_hit_pct:
            dur = default_duration(EffectKey.DEBUFF_THIEU_DOT)
            attacker.apply_effect(EffectKey.DEBUFF_THIEU_DOT, dur)
            _propagate_stack_build(defender, attacker, "burn")
            attacker.add_burn_stack(1)
            self.log.append(f"    🔥 Mirror Thiêu Đốt — **{attacker.name}**")
        if defender.bleed_on_hit_pct > 0 and self.rng.random() < defender.bleed_on_hit_pct:
            dur = default_duration(EffectKey.DEBUFF_CHAY_MAU)
            attacker.apply_effect(EffectKey.DEBUFF_CHAY_MAU, dur)
            _propagate_stack_build(defender, attacker, "bleed")
            attacker.add_bleed_stack(1)
            self.log.append(f"    🩸 Mirror Chảy Máu — **{attacker.name}**")

    def _apply_thorn(
        self, attacker: Combatant, defender: Combatant, dmg: int
    ) -> None:
        """Thorn reflects raw physical damage back; never misses.

        When ``defender.thorn_from_shield`` is True, the reflected amount is
        also paid out of the defender's shield pool first (using it as an
        offense budget). Attackers cannot die from thorn (floor at 1 HP) so
        they can still retaliate — the dedicated thorn build win condition is
        damage-over-time accumulation, not one-shot kills.
        """
        if dmg <= 0 or not attacker.is_alive():
            return
        thorn = max(1, int(dmg * defender.thorn_pct))
        # Optional: deduct thorn from shield as an offense cost
        if defender.thorn_from_shield and defender.shield > 0:
            spent = min(defender.shield, thorn // 2)
            if spent > 0:
                defender.shield -= spent
        # Apply physical reduction from attacker def
        from src.game.engine.damage.physical import apply_physical_defense
        reduced = apply_physical_defense(thorn, "physical", attacker.def_stat)
        final_dmg = max(1, reduced)
        attacker.hp = max(1, attacker.hp - final_dmg)
        thorn_tag = colorize_damage(f"-{final_dmg:,} HP", None)  # physical thorn
        self.log.append(
            f"    🌵 Gai Phản Đòn → **{attacker.name}** {thorn_tag}"
        )

    @staticmethod
    def _apply_elem_res(raw: int, target: Combatant, element: str, shred: float = 0.0) -> int:
        """Apply target's (shredded) elemental resistance to a raw burst amount."""
        res = max(0.0, target.resistances.get(element, 0.0) - shred)
        return max(1, int(raw * (1.0 - min(0.75, res))))

    def _burst_shield(
        self, actor: Combatant, target: Combatant, skill_data: dict
    ) -> None:
        """Consume the entire shield pool for burst damage (thổ resistance applies)."""
        shield_amt = actor.consume_shield()
        if shield_amt <= 0:
            return
        mult = float(skill_data.get("burst_shield_mult", 1.5))
        dmg = self._apply_elem_res(max(1, int(shield_amt * mult)), target, "tho")
        target.hp = max(0, target.hp - dmg)
        self.log.append(
            f"    🪨💥 **Thổ Tường Bùng Nổ!** tiêu {shield_amt:,} khiên → "
            f"{colorize_damage(f'-{dmg:,} HP', 'tho')} ({target.hp:,}/{target.hp_max:,})"
        )

    def _burst_mana_stacks(
        self, actor: Combatant, target: Combatant, skill_data: dict
    ) -> None:
        """Consume all mana stacks → thủy burst damage scaled by stacks × mp_max."""
        stacks = actor.consume_mana_stacks()
        if stacks <= 0:
            return
        per_stack_mult = float(skill_data.get("burst_per_mana_stack_mult", 0.12))
        raw = max(1, int(actor.mp_max * per_stack_mult * stacks))
        dmg = self._apply_elem_res(raw, target, "thuy")
        target.hp = max(0, target.hp - dmg)
        self.log.append(
            f"    💧💥 **Linh Khí Bùng Nổ!** nổ {stacks} tầng → "
            f"{colorize_damage(f'-{dmg:,} HP', 'thuy')} ({target.hp:,}/{target.hp_max:,})"
        )

    def _burst_burn(
        self, actor: Combatant, target: Combatant, skill_data: dict
    ) -> None:
        """Consume all burn stacks on target → hoa burst scaled by stacks × (base + matk×0.5)."""
        stacks = target.consume_burn_stacks()
        if stacks <= 0:
            return
        per_stack_mult = float(skill_data.get("burst_per_stack_mult", 0.35))
        base = skill_data.get("base_dmg", 0) + int(actor.matk * 0.5)
        raw = max(1, int(base * per_stack_mult * stacks))
        dmg = self._apply_elem_res(raw, target, "hoa", shred=actor.fire_res_shred)
        target.hp = max(0, target.hp - dmg)
        # Stacks are gone, clear the burn debuff marker too
        target.effects.pop(EffectKey.DEBUFF_THIEU_DOT, None)
        self.log.append(
            f"    🔥💥 **Hỏa Bùng Phát!** nổ {stacks} tầng Thiêu Đốt → "
            f"{colorize_damage(f'-{dmg:,} HP', 'hoa')} ({target.hp:,}/{target.hp_max:,})"
        )

    def _inflict_debuff(
        self, effect_key: str, meta: "EffectMeta", target: Combatant,
        actor: Combatant | None = None,
    ) -> None:
        """Apply a debuff or CC to target, respecting immunities.

        ``actor`` is optional; when provided, fire-build flags (dot_can_crit,
        burn_per_stack_pct) are propagated to the target so DoT ticks honor
        the attacker's build.
        """
        if effect_key == EffectKey.DEBUFF_DOC_TO and target.poison_immunity:
            self.log.append(f"    💚 **{target.name}** miễn dịch Độc Tố!")
            return
        # World bosses / immune_hard_cc combatants shrug off hard CC (stun,
        # freeze, silence, interrupt, knock-up). Soft debuffs still apply.
        if target.immune_hard_cc and (meta.skips_turn or meta.prevents_skills):
            self.log.append(
                f"    🛡️ **{target.name}** miễn dịch khống chế — **{meta.vi}** vô hiệu!"
            )
            return
        dur = default_duration(effect_key)
        target.apply_effect(effect_key, dur)
        # Burn stacks on every application — fire-build cornerstone
        if effect_key == EffectKey.DEBUFF_THIEU_DOT:
            if actor is not None:
                _propagate_stack_build(actor, target, "burn")
            target.add_burn_stack(1)
            self.log.append(
                f"    {meta.emoji} **{target.name}** bị **{meta.vi}** [×{target.burn_stacks}/{target.burn_stack_cap}] ({dur}t)"
            )
            return
        # Bleed stacks — Kim playstyle mirror of burn
        if effect_key == EffectKey.DEBUFF_CHAY_MAU:
            if actor is not None:
                _propagate_stack_build(actor, target, "bleed")
            target.add_bleed_stack(1)
            self.log.append(
                f"    {meta.emoji} **{target.name}** bị **{meta.vi}** [×{target.bleed_stacks}/{target.bleed_stack_cap}] ({dur}t)"
            )
            return
        # Shock stacks — Lôi playstyle mirror of burn
        if effect_key == EffectKey.DEBUFF_SOC_DIEN:
            if actor is not None:
                _propagate_stack_build(actor, target, "shock")
            target.add_shock_stack(1)
            self.log.append(
                f"    {meta.emoji} **{target.name}** bị **{meta.vi}** [×{target.shock_stacks}/{target.shock_stack_cap}] ({dur}t)"
            )
            return
        # Generic DoT: propagate attacker's DoT damage boosters (poison, bleed-mk2, etc.)
        if meta.dot_pct > 0 and actor is not None:
            _propagate_dot_bonuses(actor, target)
        self.log.append(
            f"    {meta.emoji} **{target.name}** bị **{meta.vi}** ({dur}t)"
        )

    def _auto_attack(self, actor: Combatant, target: Combatant) -> None:
        """Physical auto-attack using ATK stat, reduced by target physical defense."""
        from src.game.engine.damage.physical import apply_physical_defense
        raw = max(1, int(actor.atk * self.rng.uniform(0.85, 1.15) + 5))
        dmg = apply_physical_defense(raw, "physical", target.def_stat)
        if target.final_dmg_reduce > 0:
            dmg = int(dmg * (1.0 - target.final_dmg_reduce))
        dmg = max(1, dmg)
        target.hp = max(0, target.hp - dmg)
        self.log.append(
            f"  👊 **{actor.name}** tấn công cơ bản → "
            f"{colorize_damage(f'-{dmg} HP', None)}"
        )

    def _choose_skill(self, actor: Combatant) -> tuple[Optional[str], str]:
        """Pick the highest-damage skill the actor can cast this turn.

        Returns a (skill_key, reason) tuple. When no skill is available,
        ``skill_key`` is None and ``reason`` is one of:
            "cooldown"  — at least one known skill exists but all are cooling
            "mana"      — at least one skill is off cooldown but too expensive
            "none"      — actor has no usable skill keys registered at all

        This granularity lets the caller log an accurate message instead of
        blaming mana for every fallback to basic attack.
        """
        known = [sk for sk in actor.skill_keys if registry.get_skill(sk) is not None]
        if not known:
            return None, "none"

        off_cooldown = [sk for sk in known if not actor.skill_on_cooldown(sk)]
        if not off_cooldown:
            return None, "cooldown"

        affordable = [
            sk for sk in off_cooldown
            if actor.mp >= (registry.get_skill(sk) or {}).get("mp_cost", 999)
        ]
        if not affordable:
            return None, "mana"

        return (
            max(affordable, key=lambda sk: (registry.get_skill(sk) or {}).get("base_dmg", 0)),
            "ok",
        )

    def _process_periodic(self, combatant: Combatant) -> None:
        """Process all periodic effects (DoTs, HP regen, Linh Căn procs) at end of turn."""
        # The opposing combatant is treated as the DoT's applier — lets Moc
        # builds leech HP/MP off the poison they inflicted.
        opponent = self.enemy if combatant is self.player else self.player

        # DoT damage from all active debuffs (poison, burn, bleed, etc.)
        for effect_key, dot_dmg, is_crit in get_periodic_damage(combatant, self.rng):
            combatant.hp = max(0, combatant.hp - dot_dmg)

            # Moc build: leech a fraction of DoT damage as HP + MP to the applier
            if opponent and opponent.dot_leech_pct > 0 and opponent.is_alive():
                leech = max(1, int(dot_dmg * opponent.dot_leech_pct))
                heal_amt = self._apply_heal(opponent, leech)
                mp_gain = min(opponent.mp_max - opponent.mp, leech // 2)
                if mp_gain > 0:
                    opponent.mp += mp_gain
                self.log.append(
                    f"    🌿 **{opponent.name}** hút máu DoT → +{heal_amt:,} HP / +{mp_gain:,} MP"
                )

            meta = EFFECTS.get(effect_key)
            emoji = meta.emoji if meta else "💢"
            name = meta.vi if meta else effect_key
            crit_tag = " 💥BẠO!" if is_crit else ""
            stack_tag = (
                f" [×{combatant.burn_stacks}]"
                if effect_key == EffectKey.DEBUFF_THIEU_DOT and combatant.burn_stacks > 0
                else ""
            )
            dot_tag = colorize_damage(
                f"-{dot_dmg:,} HP", meta.dot_element if meta else None,
            )
            self.log.append(
                f"  {emoji} **{combatant.name}** bị {name}{stack_tag} {dot_tag}{crit_tag}"
            )

        # Periodic: Thổ Linh Căn — activate shield when HP is low
        lc_effects.check_shield(combatant, self.log)

        # Thổ build: shield regen (pct of hp_max + flat), capped at shield_cap
        if combatant.is_alive() and (combatant.shield_regen_pct > 0 or combatant.shield_regen_flat > 0):
            regen = int(combatant.hp_max * combatant.shield_regen_pct) + combatant.shield_regen_flat
            gained = combatant.add_shield(regen)
            if gained > 0:
                self.log.append(
                    f"  🪨 **{combatant.name}** Thổ Tường hồi +{gained:,} khiên "
                    f"({combatant.shield:,}/{combatant.shield_cap():,})"
                )

        # HP regen: pct (of hp_max) + flat, stacked.
        if combatant.is_alive():
            mods = get_combat_modifiers(combatant)
            effective_regen_pct = combatant.hp_regen_pct + mods.get("hp_regen_pct", 0.0)
            hp_pct_regen = int(combatant.hp_max * effective_regen_pct) if effective_regen_pct > 0 else 0
            hp_total_regen = hp_pct_regen + max(0, combatant.hp_regen_flat)
            if hp_total_regen > 0 and combatant.hp < combatant.hp_max:
                applied = self._apply_heal(combatant, hp_total_regen)
                if applied > 0:
                    self.log.append(f"  💚 **{combatant.name}** hồi sinh lực +{applied} HP")

            # MP regen: pct (of mp_max) + flat, stacked.
            mp_pct_regen = int(combatant.mp_max * combatant.mp_regen_pct) if combatant.mp_regen_pct > 0 else 0
            mp_total_regen = mp_pct_regen + max(0, combatant.mp_regen_flat)
            if mp_total_regen > 0 and combatant.mp < combatant.mp_max:
                mp_total_regen = max(1, mp_total_regen)
                combatant.mp = min(combatant.mp_max, combatant.mp + mp_total_regen)
                self.log.append(f"  💙 **{combatant.name}** hồi linh lực +{mp_total_regen} MP")

        combatant.tick_effects()

    def _roll_loot(self) -> list[dict]:
        enemy_data = registry.get_enemy(self.enemy.key)
        if not enemy_data:
            return []
        # Use enemy-specific table if defined (special bosses), else fall back to zone table.
        loot_key = enemy_data.get("loot_table_key") or f"LootZone_{enemy_data.get('realm_level', 1)}"
        drop_table = registry.get_loot_table(loot_key)
        drops = roll_drops(drop_table, self.rng, luck_pct=self.loot_luck_pct).merge()
        if self.loot_qty_multiplier != 1.0:
            drops = [
                {"item_key": d["item_key"], "quantity": max(1, round(d["quantity"] * self.loot_qty_multiplier))}
                for d in drops
            ]
        return drops

    def _victory(self) -> CombatResult:
        loot = self._roll_loot()
        enemy_data = registry.get_enemy(self.enemy.key)
        rank = enemy_data.get("rank", "pho_thong") if enemy_data else "pho_thong"
        merit_map = {
            "pho_thong": 3, "tinh_anh": 7,
            "cuong_gia": 10, "hung_manh": 20,
            "dai_nang": 30, "than_thu": 50, "tien_thu": 75,
            "chi_ton": 100,
        }
        merit = merit_map.get(rank, 3)
        karma = 1 if rank in ("than_thu", "tien_thu", "dai_nang", "chi_ton") else 0

        loot_lines = [f"  🎁 {d['item_key']} × {d['quantity']}" for d in loot] or ["  (không có vật phẩm)"]
        self.log.append(f"\n🏆 **Chiến thắng!** Phần thưởng:\n" + "\n".join(loot_lines))
        return CombatResult(
            reason=CombatEndReason.PLAYER_WIN, turns=self.turn,
            log=self.log, loot=loot, merit_gained=merit, karma_gained=karma,
        )

    def _defeat(self) -> CombatResult:
        self.log.append(f"\n💀 **{self.player.name}** đã bại trận!")
        return CombatResult(
            reason=CombatEndReason.PLAYER_DEAD, turns=self.turn,
            log=self.log, loot=[], merit_gained=0, karma_gained=0,
        )


def build_player_combatant(
    char: Character,
    player_skill_keys: list[str],
    gem_count: int = 0,
    equip_stats: dict | None = None,
    gem_keys: list[str] | None = None,
) -> Combatant:
    """Build a Combatant from a Character dataclass.

    Delegates all stat math to compute_combat_stats — single source of truth.
    """
    from src.game.systems.character_stats import compute_combat_stats

    cs = compute_combat_stats(char, gem_count=gem_count, equip_stats=equip_stats, gem_keys=gem_keys)

    # Auto-inject formation skill if active and not already in skill list
    final_skill_keys = list(player_skill_keys)
    if char.active_formation:
        form_data = registry.get_formation(char.active_formation)
        if form_data:
            frm_skill = form_data.get("formation_skill_key")
            if frm_skill and frm_skill not in final_skill_keys:
                final_skill_keys.append(frm_skill)

    hp_current = min(char.hp_current, cs.hp_max) if char.hp_current > 0 else cs.hp_max
    mp_current = min(char.mp_current, cs.mp_max) if char.mp_current > 0 else cs.mp_max

    # Carry every field CombatStats and Combatant share — field names match
    # by construction (see character_stats.CombatStats). Any CombatStats-only
    # fields (mp_reserved, mp_reserve_pct) are filtered out automatically.
    combatant_fields = {f.name for f in fields(Combatant)}
    cs_kwargs = {k: v for k, v in asdict(cs).items() if k in combatant_fields}

    return Combatant(
        key="player",
        name=char.name,
        hp=hp_current,
        mp=mp_current,
        element=None,
        skill_keys=final_skill_keys,
        linh_can=list(char.linh_can),
        **cs_kwargs,
    )


def build_enemy_combatant(enemy_key: str, player_realm_total: int) -> Combatant | None:
    """Build enemy Combatant scaled to player realm, using per-enemy hp_scale.

    realm_level (1-10) in enemy data applies an additional combat-stat multiplier on top
    of the existing player-realm-based scaling, letting enemies within the same rank feel
    meaningfully different in power.
    """
    enemy_data = registry.get_enemy(enemy_key)
    if not enemy_data:
        return None

    rank = enemy_data.get("rank", "pho_thong")
    realm_scale = 1.0 + (player_realm_total / ENEMY_SCALE_MAX) * ENEMY_HP_SCALE_FACTOR
    hp_scale = enemy_data.get("hp_scale", 1.0)
    hp = int(enemy_data["base_hp"] * realm_scale * hp_scale)
    spd = enemy_data.get("base_spd", 8)
    enemy_dmg_bonus = (player_realm_total / ENEMY_SCALE_MAX) * ENEMY_DMG_BONUS_SCALE

    # Additional multiplier from realm_level (1-10) — differentiates enemies within same rank
    realm_level = enemy_data.get("realm_level", 1)
    rl_mult = ENEMY_REALM_LEVEL_STAT_MULT.get(realm_level, 1.0)

    # Combat stats: rank-based fallback × player-realm scale × realm-level scale
    atk          = int(enemy_data.get("base_atk",     ENEMY_RANK_BASE_ATK.get(rank,     30)) * realm_scale * rl_mult)
    matk         = int(enemy_data.get("base_matk",    ENEMY_RANK_BASE_MATK.get(rank,    30)) * realm_scale * rl_mult)
    def_stat     = int(enemy_data.get("base_def",     ENEMY_RANK_BASE_DEF.get(rank,     20)) * realm_scale * rl_mult)
    evasion_rating = int(enemy_data.get("base_evasion", ENEMY_RANK_BASE_EVASION.get(rank, 0)) * realm_scale * rl_mult)

    res: dict[str, float] = {}
    elem = enemy_data.get("element")
    if elem:
        # Scale with player realm, cap at 35% to keep combat fair
        res[elem] = min(0.35, ENEMY_BASE_ELEM_RES * realm_scale)

    # Enemies regen MP so they can keep casting skills across a long fight.
    # Without this, their small MP pool empties after a handful of turns and
    # they fall back to auto-attacks for the rest of combat.
    mp_max = hp // 2
    enemy_mp_regen_pct = enemy_data.get("mp_regen_pct", 0.04)
    # HP regen for themed enemies (Dược Viên wood-spirits carry a regen aura).
    enemy_hp_regen_pct = enemy_data.get("hp_regen_pct", 0.0)

    # Optional combat-rating overrides from JSON — scale like atk/matk/def so
    # a "base_crit_rating": 80 at R9 turns into real crit chance after the
    # realm multipliers. Lets us put threat on individual apex enemies
    # instead of moving global rank fallbacks.
    # Crit ratings scale like atk/matk — realm × rl_mult.
    crit_rating = int(enemy_data.get("base_crit_rating", 0) * realm_scale * rl_mult)
    crit_dmg_rating = int(enemy_data.get("base_crit_dmg_rating", 0) * realm_scale * rl_mult)
    # JSON final_dmg_bonus stacks flat on top of the realm-based
    # enemy_dmg_bonus. Keep it small — realm scaling already contributes
    # +150-470 % before this field is added.
    extra_fdb = float(enemy_data.get("final_dmg_bonus", 0.0))

    return Combatant(
        key=enemy_key,
        name=enemy_data["vi"],
        hp=hp,
        hp_max=hp,
        mp=mp_max,
        mp_max=mp_max,
        spd=spd,
        element=elem,
        atk=atk,
        matk=matk,
        def_stat=def_stat,
        evasion_rating=evasion_rating,
        crit_rating=crit_rating,
        crit_dmg_rating=crit_dmg_rating,
        resistances=res,
        skill_keys=enemy_data.get("skill_keys", []),
        final_dmg_bonus=enemy_dmg_bonus + extra_fdb,
        mp_regen_pct=enemy_mp_regen_pct,
        hp_regen_pct=enemy_hp_regen_pct,
        immune_hard_cc=bool(enemy_data.get("immune_hard_cc", False)),
    )


def build_world_boss_combatant(
    boss_data: dict, current_hp: int, player_realm_total: int
) -> Combatant:
    """Build a persistent world-boss Combatant from a world_bosses.json entry.

    Unlike regular enemies, world bosses:
    - Persist HP across player attacks (passed in via ``current_hp``)
    - Always set ``immune_hard_cc=True`` so hard CC never lands
    - Use a fixed, pre-defined ``skill_pool`` instead of realm-scaled enemy skills
    - Scale their raw stats with the attacking player's realm so every fight stays challenging
    """
    from src.game.constants.balance import (
        ENEMY_RANK_BASE_ATK, ENEMY_RANK_BASE_MATK, ENEMY_RANK_BASE_DEF,
        ENEMY_RANK_BASE_EVASION, ENEMY_SCALE_MAX, ENEMY_DMG_BONUS_SCALE,
        ENEMY_BASE_ELEM_RES,
    )

    rank = "chi_ton"   # World bosses are always supreme-rank for stat lookups
    realm_scale = 1.0 + (player_realm_total / ENEMY_SCALE_MAX) * 0.5

    hp_max = int(boss_data["base_hp"] * boss_data.get("hp_scale", 1.0))
    hp = max(0, min(current_hp, hp_max)) if current_hp else hp_max

    atk = int(ENEMY_RANK_BASE_ATK.get(rank, 100) * realm_scale * 1.5)
    matk = int(ENEMY_RANK_BASE_MATK.get(rank, 100) * realm_scale * 1.5)
    def_stat = int(ENEMY_RANK_BASE_DEF.get(rank, 60) * realm_scale * 1.5)
    evasion_rating = int(ENEMY_RANK_BASE_EVASION.get(rank, 0) * realm_scale)
    enemy_dmg_bonus = (player_realm_total / ENEMY_SCALE_MAX) * ENEMY_DMG_BONUS_SCALE

    elem = boss_data.get("element")
    res: dict[str, float] = {}
    if elem:
        res[elem] = min(0.40, ENEMY_BASE_ELEM_RES * realm_scale * 1.2)

    mp_max = max(800, hp_max // 4)

    return Combatant(
        key=boss_data["key"],
        name=boss_data["vi"],
        hp=hp,
        hp_max=hp_max,
        mp=mp_max,
        mp_max=mp_max,
        spd=boss_data.get("base_spd", 15),
        element=elem,
        atk=atk,
        matk=matk,
        def_stat=def_stat,
        evasion_rating=evasion_rating,
        resistances=res,
        skill_keys=list(boss_data.get("skill_pool", [])),
        final_dmg_bonus=enemy_dmg_bonus,
        mp_regen_pct=0.08,
        immune_hard_cc=True,
        final_dmg_reduce=0.15,   # World bosses shrug off 15% of damage by default
    )
