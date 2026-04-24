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
from src.game.constants.balance import HEAL_CRIT_CHANCE, HEAL_CRIT_MULT
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

    # ── Turn orchestration ────────────────────────────────────────────────

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
