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
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

from src.data.registry import registry
from src.game.constants.balance import (
    HEAL_CRIT_CHANCE, HEAL_CRIT_MULT, MAX_ELEMENTAL_RES, MAX_FINAL_DMG_REDUCE,
)
from src.game.constants.effects import EffectKey
from src.game.engine import linh_can_effects as lc_effects
from src.game.engine.damage import colorize_damage
from src.game.engine.drop import roll_drops
from src.game.engine.effects import (
    EFFECTS, check_cc_skip_turn, check_prevents_skills, get_combat_modifiers,
    get_periodic_damage,
)
from src.game.systems.combatant import Combatant

from .casting import auto_attack, cast_skill, fire_formation_skills
from .helpers import effective_spd, spd_extra_turn_pct


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
    # Optional override that bypasses the enemy's ``loot_table_key`` lookup.
    # Used by Linh Căn dungeons so every wave drops from the element's own
    # material table regardless of which enemy was actually killed.
    loot_table_override: str | None = None

    # ── Turn orchestration ────────────────────────────────────────────────

    def _actor_phase(
        self, actor: Combatant, target: Combatant, actor_is_player: bool
    ) -> Optional[CombatResult]:
        """Run actor's normal turn plus a possible SPD-driven extra action.

        Returns a CombatResult if the phase ends the fight, else None.
        """
        self._take_turn(actor, target)
        if not target.is_alive() and not self._try_phoenix_revive(target):
            return self._victory() if actor_is_player else self._defeat()

        # Extra-turn roll: faster combatant may get a bonus action this round
        extra_pct = spd_extra_turn_pct(effective_spd(actor), effective_spd(target))
        if extra_pct > 0 and self.rng.random() < extra_pct:
            self.log.append(
                f"  💨 **{actor.name}** vượt tốc độ — hành động thêm một lần!"
            )
            self._take_turn(actor, target)
            if not target.is_alive() and not self._try_phoenix_revive(target):
                return self._victory() if actor_is_player else self._defeat()
        # Lôi-build: flat turn-steal roll, independent of SPD gap. Fires at most
        # once per phase so even a maxed-out build can't lock the opponent out.
        if actor.turn_steal_pct > 0 and self.rng.random() < actor.turn_steal_pct:
            self.log.append(
                f"  ⚡ **{actor.name}** cướp lượt — **Lôi Tốc Hành Động!**"
            )
            self._take_turn(actor, target)
            if not target.is_alive() and not self._try_phoenix_revive(target):
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
        if self.turn == 1:
            # Once-per-fight start-of-combat auras (Thôn Thiên Ma Khí, …).
            self._apply_stat_drain_aura(self.player, self.enemy)
            self._apply_stat_drain_aura(self.enemy, self.player)
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
        if not self.player.is_alive() and not self._try_phoenix_revive(self.player):
            return (self.log[start_idx:], self._defeat())
        if not self.enemy.is_alive() and not self._try_phoenix_revive(self.enemy):
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
            auto_attack(self, actor, target)
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
            auto_attack(self, actor, target)
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
            auto_attack(self, actor, target)
            return

        cast_skill(self, actor, target, skill_key, skill_data, mp_cost)

        # Parallel formation barrage — each active formation whose signature
        # skill is off cooldown + affordable fires after the main cast.
        # Gives multi-slot Trận Tu true simultaneity: all active formations
        # act each turn rather than time-sharing the main rotation.
        if target.is_alive():
            fire_formation_skills(self, actor, target)

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

    # ── Shared mutation helpers (called from multiple modules) ───────────

    def _apply_stat_drain_aura(self, holder: Combatant, target: Combatant) -> None:
        """Thôn Thiên Ma Khí — at combat start the holder drains
        ``stat_drain_aura_pct`` of the target's core stats and absorbs the
        same amount. Idempotent via the ``stat_drain_aura_applied`` flag so
        repeated calls (e.g. test harnesses) don't compound.
        """
        pct = holder.stat_drain_aura_pct
        if pct <= 0 or holder.stat_drain_aura_applied:
            return
        holder.stat_drain_aura_applied = True

        drained_atk = int(target.atk * pct)
        drained_matk = int(target.matk * pct)
        drained_def = int(target.def_stat * pct)
        drained_spd = int(target.spd * pct)

        target.atk = max(0, target.atk - drained_atk)
        target.matk = max(0, target.matk - drained_matk)
        target.def_stat = max(0, target.def_stat - drained_def)
        target.spd = max(1, target.spd - drained_spd)

        holder.atk += drained_atk
        holder.matk += drained_matk
        holder.def_stat += drained_def
        holder.spd += drained_spd

        if drained_atk + drained_matk + drained_def + drained_spd > 0:
            self.log.append(
                f"  🌑 **{holder.name}** Thôn Thiên Ma Khí — hấp thụ "
                f"{int(pct * 100)}% chỉ số đối phương "
                f"(+{drained_atk} ATK / +{drained_matk} MATK / "
                f"+{drained_def} DEF / +{drained_spd} SPD)!"
            )

    def _try_phoenix_revive(self, combatant: Combatant) -> bool:
        """Niết Bàn Trùng Sinh — once-per-combat revive on lethal damage.

        If the combatant has ``phoenix_revive_pct > 0`` and hasn't yet used
        the revive this fight, restore HP to ``phoenix_revive_pct × hp_max``,
        bump offensive/defensive stats by ``phoenix_revive_buff_pct``, mark
        used, and return True. Otherwise return False so the caller can
        finalize the death.
        """
        if combatant.is_alive():
            return False
        if combatant.phoenix_revive_used:
            return False
        if combatant.phoenix_revive_pct <= 0:
            return False

        combatant.phoenix_revive_used = True
        revived_hp = max(1, int(combatant.hp_max * combatant.phoenix_revive_pct))
        combatant.hp = revived_hp

        buff = combatant.phoenix_revive_buff_pct
        if buff > 0:
            combatant.atk = int(combatant.atk * (1.0 + buff))
            combatant.matk = int(combatant.matk * (1.0 + buff))
            combatant.def_stat = int(combatant.def_stat * (1.0 + buff))
            combatant.final_dmg_bonus += buff
            combatant.final_dmg_reduce = min(
                MAX_FINAL_DMG_REDUCE, combatant.final_dmg_reduce + buff,
            )

        # Clear DoTs and adverse stacks — the rebirth purges lingering effects
        combatant.burn_stacks = 0
        combatant.bleed_stacks = 0
        combatant.shock_stacks = 0
        combatant.effects.clear()

        buff_tag = f" · ST/Giáp +{buff * 100:.0f}%" if buff > 0 else ""
        self.log.append(
            f"  🔥🦅 **{combatant.name}** **NIẾT BÀN TRÙNG SINH!** "
            f"Hồi sinh +{revived_hp:,}/{combatant.hp_max:,} HP{buff_tag}"
        )
        return True

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

    # ── Periodic / end-of-round + resolution ─────────────────────────────

    def _process_periodic(self, combatant: Combatant) -> None:
        """Process all periodic effects (DoTs, HP regen, Linh Căn procs) at end of turn."""
        # The opposing combatant is treated as the DoT's applier — lets Moc
        # builds leech HP/MP off the poison they inflicted.
        opponent = self.enemy if combatant is self.player else self.player

        # Thánh Tuyền Thể — pay out one queued installment of deferred damage.
        # Bypasses ``take_damage`` to avoid re-deferring the already-deferred
        # chunk (would never apply otherwise).
        if combatant.deferred_damage_queue:
            installment = combatant.deferred_damage_queue.pop(0)
            if installment > 0:
                combatant.hp = max(0, combatant.hp - installment)
                self.log.append(
                    f"  💧 **{combatant.name}** Thánh Tuyền hoàn trả "
                    f"{colorize_damage(f'-{installment:,} HP', 'thuy')}"
                )

        # DoT damage from all active debuffs (poison, burn, bleed, etc.)
        # Pass is_dot=True so the Fortify Aura post-hit brace ignores DoT
        # ticks — only direct skill/aura/reflect hits arm the brace.
        for effect_key, dot_dmg, is_crit in get_periodic_damage(combatant, self.rng):
            combatant.take_damage(dot_dmg, is_dot=True)

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

        # Solar aura — Thái Dương Thần Thể tier passive: every turn, deal
        # fire damage to the opponent equal to (combatant.hp_max × solar_aura_pct),
        # boosted by final_dmg_bonus + burn_dmg_bonus, with bonus_dmg_vs_burn
        # vs already-burning targets and element_res_shred reducing the target's
        # hoa resistance. Independent of skill actions and DoT ticks.
        if (
            combatant.is_alive()
            and combatant.solar_aura_pct > 0
            and opponent
            and opponent.is_alive()
        ):
            base = int(combatant.hp_max * combatant.solar_aura_pct)
            if base > 0:
                mult = 1.0 + combatant.final_dmg_bonus + combatant.burn_dmg_bonus
                if opponent.burn_stacks > 0 and combatant.bonus_dmg_vs_burn > 0:
                    mult += combatant.bonus_dmg_vs_burn
                target_res = max(
                    0.0,
                    min(MAX_ELEMENTAL_RES, opponent.resistances.get("hoa", 0.0) - combatant.element_res_shred.get("hoa", 0.0)),
                )
                aura_dmg = max(1, int(base * mult * (1.0 - target_res)))
                opponent.take_damage(aura_dmg)
                aura_tag = colorize_damage(f"-{aura_dmg:,} HP", "hoa")
                self.log.append(
                    f"  ☀️ **{combatant.name}** Thái Dương Thần Quang → "
                    f"**{opponent.name}** {aura_tag}"
                )

        # Wither aura — Khô Mộc Thần Thể tier passive: drains hp_max × pct
        # from the opponent as moc damage, then heals the holder by the same
        # amount (routed through ``_apply_heal`` so heal_can_crit /
        # bleed-heal-reduction / heal-to-damage queue all behave correctly).
        if (
            combatant.is_alive()
            and combatant.wither_aura_pct > 0
            and opponent
            and opponent.is_alive()
        ):
            base = int(combatant.hp_max * combatant.wither_aura_pct)
            if base > 0:
                mult = 1.0 + combatant.final_dmg_bonus + combatant.dot_dmg_bonus
                target_res = max(
                    0.0,
                    min(MAX_ELEMENTAL_RES, opponent.resistances.get("moc", 0.0) - combatant.element_res_shred.get("moc", 0.0)),
                )
                drain_dmg = max(1, int(base * mult * (1.0 - target_res)))
                opponent.take_damage(drain_dmg)
                healed = self._apply_heal(combatant, drain_dmg)
                drain_tag = colorize_damage(f"-{drain_dmg:,} HP", "moc")
                self.log.append(
                    f"  🌿 **{combatant.name}** Khô Mộc Hấp Thu → "
                    f"**{opponent.name}** {drain_tag} (+{healed:,} HP)"
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

        # Hào Quang Củng Cố — gain one stack each periodic phase (Hoang Cổ
        # Thánh Thể Chain 9). Capped at fortify_stack_cap; no-op when the
        # holder doesn't carry the aura. Decrement the post-hit brace AFTER
        # combat resolution so the brace covers the swing that arrived this
        # turn — once the periodic phase fires, the brace expires.
        if combatant.is_alive() and combatant.fortify_per_turn_pct > 0:
            cap = combatant.fortify_stack_cap or 0
            if cap > 0 and combatant.fortify_stacks < cap:
                combatant.fortify_stacks += 1
                bonus = combatant.fortify_per_turn_pct * combatant.fortify_stacks
                self.log.append(
                    f"  🛡️ **{combatant.name}** Hào Quang Củng Cố [×{combatant.fortify_stacks}/{cap}] "
                    f"(+{bonus * 100:.1f}% ST cuối / Giảm ST)"
                )
        if combatant.fortify_braced_turns > 0:
            combatant.fortify_braced_turns -= 1

        combatant.tick_effects()

    def _roll_loot(self) -> list[dict]:
        enemy_data = registry.get_enemy(self.enemy.key)
        # ``loot_table_override`` (set by themed dungeons) takes precedence so
        # every wave can pull from the same dungeon-specific table regardless
        # of which enemy was actually killed.
        if self.loot_table_override:
            loot_key = self.loot_table_override
        elif enemy_data:
            # Use enemy-specific table if defined (special bosses), else fall
            # back to zone table.
            loot_key = enemy_data.get("loot_table_key") or f"LootZone_{enemy_data.get('realm_level', 1)}"
        else:
            return []
        drop_table = registry.get_loot_table(loot_key)
        # Constitution / equipment loot bonuses stack on top of the session's
        # baseline (elite roll, dungeon grade). ``loot_luck_bonus`` is additive
        # on luck_pct; ``loot_qty_bonus`` is additive on the qty multiplier.
        effective_luck = self.loot_luck_pct + max(0.0, self.player.loot_luck_bonus)
        effective_qty_mult = self.loot_qty_multiplier * (1.0 + max(0.0, self.player.loot_qty_bonus))
        drops = roll_drops(drop_table, self.rng, luck_pct=effective_luck).merge()
        if effective_qty_mult != 1.0:
            drops = [
                {"item_key": d["item_key"], "quantity": max(1, round(d["quantity"] * effective_qty_mult))}
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

    # ── Thin delegates to module-level functions ─────────────────────────
    # Callers (tests, linh_can_effects introspection) reach through the
    # session for these, so the shims keep the public surface stable while
    # the logic lives in its own module.

    def _run_on_hit_procs(self, actor: Combatant, target: Combatant, is_crit: bool) -> None:
        from .procs import run_on_hit_procs
        run_on_hit_procs(self, actor, target, is_crit)

    def _apply_reactive_damage(self, actor: Combatant, target: Combatant, dmg: int) -> None:
        from .procs import apply_reactive_damage
        apply_reactive_damage(self, actor, target, dmg)

    def _apply_skill_effects(
        self, skill_data: dict, actor: Combatant, target: Combatant, hit: bool,
    ) -> None:
        from .casting import apply_skill_effects
        apply_skill_effects(self, skill_data, actor, target, hit)

    def _burst_burn(self, actor: Combatant, target: Combatant, skill_data: dict) -> None:
        from .bursts import burst_burn
        burst_burn(self, actor, target, skill_data)

    def _burst_shield(self, actor: Combatant, target: Combatant, skill_data: dict) -> None:
        from .bursts import burst_shield
        burst_shield(self, actor, target, skill_data)

    def _burst_mana_stacks(self, actor: Combatant, target: Combatant, skill_data: dict) -> None:
        from .bursts import burst_mana_stacks
        burst_mana_stacks(self, actor, target, skill_data)
