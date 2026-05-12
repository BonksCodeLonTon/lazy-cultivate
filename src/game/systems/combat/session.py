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
    AUTO_LOOT_DROP_RATE, HEAL_CRIT_CHANCE, HEAL_CRIT_MULT, MAX_ELEMENTAL_RES,
    MAX_FINAL_DMG_REDUCE,
)
from src.game.constants.effects import EffectKey
from src.game.engine import linh_can_effects as lc_effects
from src.game.engine.damage import colorize_damage
from src.game.engine.drop import roll_drops
from src.game.engine.effects import (
    EFFECTS, check_cc_skip_turn, check_prevents_skills, count_elemental_dots,
    default_duration, effective_stack_cap, get_combat_modifiers,
    get_periodic_damage,
)
from src.game.systems.combatant import Combatant

from . import phase as phase_lock
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
    max_turns: int = 50  # full rounds (player + enemy each act once per round)
    # When True (default), running out of turns is treated as a player defeat —
    # if the player can't kill the enemy within ``max_turns`` it counts as a
    # loss. World boss combat opts out (``False``) because hitting the round
    # limit there is the normal "chip away" cadence, not a death.
    max_turns_is_defeat: bool = True
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
    # When True, 90% of kills yield no loot at all (rolled before the drop
    # table). Auto-repeat sets this so afk grinding pays out at 10% the rate
    # of manual play.
    auto_mode: bool = False

    # ── Turn orchestration ────────────────────────────────────────────────

    def _actor_phase(
        self, actor: Combatant, target: Combatant, actor_is_player: bool
    ) -> Optional[CombatResult]:
        """Run actor's normal turn plus a possible SPD-driven extra action.

        Returns a CombatResult if the phase ends the fight, else None.
        """
        self._take_turn(actor, target)
        # Phase-lock check fires BEFORE the death/revive cascade so a
        # lethal-overkill burst that crosses the threshold still arms the
        # invulnerability window — the activation heals to full so a
        # negative HP becomes a fresh full bar instead of a victory log.
        if actor_is_player:
            phase_lock.try_trigger(target, self.log)
        if not target.is_alive() and not self._try_phoenix_revive(target):
            return self._victory() if actor_is_player else self._defeat()

        # Extra-turn roll: faster combatant may get a bonus action this round
        extra_pct = spd_extra_turn_pct(effective_spd(actor), effective_spd(target))
        if extra_pct > 0 and self.rng.random() < extra_pct:
            self.log.append(
                f"  💨 **{actor.name}** vượt tốc độ — hành động thêm một lần!"
            )
            self._take_turn(actor, target)
            if actor_is_player:
                phase_lock.try_trigger(target, self.log)
            if not target.is_alive() and not self._try_phoenix_revive(target):
                return self._victory() if actor_is_player else self._defeat()
        # Lôi-build: flat turn-steal roll, independent of SPD gap. Fires at most
        # once per phase so even a maxed-out build can't lock the opponent out.
        if actor.turn_steal_pct > 0 and self.rng.random() < actor.turn_steal_pct:
            self.log.append(
                f"  ⚡ **{actor.name}** cướp lượt — **Lôi Tốc Hành Động!**"
            )
            self._take_turn(actor, target)
            if actor_is_player:
                phase_lock.try_trigger(target, self.log)
            if not target.is_alive() and not self._try_phoenix_revive(target):
                return self._victory() if actor_is_player else self._defeat()
        return None

    def step(self) -> tuple[list[str], Optional[CombatResult]]:
        """Process one full round: player acts → enemy acts → periodic effects.

        Returns (new_log_lines, result_if_over).
        result is None while the fight is still ongoing.
        """
        if self.turn >= self.max_turns:
            if self.max_turns_is_defeat:
                self.log.append(
                    f"\n⏰ **{self.player.name}** không hạ được đối thủ trong "
                    f"{self.max_turns} lượt — coi như thất bại."
                )
                return ([], self._defeat())
            return ([], CombatResult(
                reason=CombatEndReason.MAX_TURNS,
                turns=self.turn, log=self.log, loot=[], merit_gained=0, karma_gained=0,
            ))

        start_idx = len(self.log)
        self.turn += 1
        # Reset per-round damage-taken accumulators. Drives the heavy-hit
        # passive trigger (``proc_on_heavy_hit_pct``) — only damage taken
        # within this single round counts toward the threshold.
        self.player.damage_taken_this_turn = 0
        self.enemy.damage_taken_this_turn = 0
        if self.turn == 1:
            # Energy Shield prefill — a shield-investment build (HauTo /
            # Cường Khiên / Nguyên Linh, ``pfx_shield_max_*`` affixes) should
            # walk into the fight with their shield cap already populated;
            # otherwise the shield only fills via per-turn regen, which makes
            # the first few turns play as if the investment didn't exist.
            # Symmetric on enemies — bosses with ``shield_max_base`` get the
            # same treatment, matching how HP starts at hp_max.
            for c in (self.player, self.enemy):
                cap = c.shield_cap()
                if cap > 0 and c.shield < cap:
                    c.shield = cap
            # Once-per-fight start-of-combat auras (Thôn Thiên Ma Khí, …).
            self._apply_stat_drain_aura(self.player, self.enemy)
            self._apply_stat_drain_aura(self.enemy, self.player)
            # Passive aura skills (``aura: true`` in skill JSON) — stamp
            # their effects on the configured ``aura_targets``. Fire from
            # both sides so a player-carried aura also lands.
            self._apply_passive_auras(self.player, self.enemy)
            self._apply_passive_auras(self.enemy, self.player)
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

        # Phase-Lock decay/expiry — only the enemy carries phase_lock_config
        # in current data, but the helper is symmetric and tolerates either
        # side. On expiry the opponent (player) is forced to 0 HP so the
        # standard death-check below converts cleanly to a defeat.
        phase_lock.tick(self.player, self.enemy, self.log)
        phase_lock.tick(self.enemy, self.player, self.log)

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
        # Phase Lock — boss is invulnerable during the active window and skips
        # its own turn. Player still acts normally on alternating phases.
        if phase_lock.is_phase_lock_active(actor):
            cfg = actor.phase_lock_config or {}
            emoji = cfg.get("phase_emoji", "✨")
            self.log.append(
                f"  {emoji} **{actor.name}** đang trong **"
                f"{cfg.get('phase_name_vi', 'Phase Lock')}** — không thể tấn công."
            )
            return

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
            if target.is_alive():
                self._fire_hits_taken_procs(target, actor)
                self._fire_heavy_hit_procs(target, actor)
            return

        # Pre-turn: Quang Linh Căn — actor may cleanse a debuff before acting.
        # Opponent is passed so Đế Tịnh Quang Thể's Cleanse Retaliate can fire
        # damage at the right target.
        opponent = self.enemy if actor is self.player else self.player
        lc_effects.try_cleanse(actor, self.rng, self.log, opponent=opponent)

        # Pre-turn: Kính Hoa Thủy Nguyệt — buff-driven debuff transfer.
        # Independent of cleanse so a holder can both cleanse one debuff AND
        # bounce another back at the opponent in the same turn.
        self._try_transfer_debuffs(actor, opponent)

        # Pre-turn: Lưu Ly Tịnh Hỏa — per-fire-DoT cleanse aura. Runs after
        # try_cleanse / transfer so the rolls operate on the post-cleanse
        # debuff set (and each successful cleanse here bumps crit_res for
        # the rest of the buff's duration).
        self._process_luu_ly_tinh_hoa(actor, opponent)

        # Pre-turn: Lưu Tinh Cản Nguyệt — refresh the dynamic evasion / spd
        # contributions based on the opponent's current fire-DoT count.
        # Re-writes the buff's override stat_bonus so subsequent
        # ``get_combat_modifiers`` calls this turn see the current scaling.
        self._refresh_luu_tinh_can_nguyet(actor, opponent)

        # Auto-cast on stacks — if the target carries enough of a tagged stack
        # (burn/bleed/shock), force-fire the matching finisher this turn instead
        # of the actor's normal pick. Off-cooldown is bypassed, but MP must be
        # available (falls back to normal flow if not).
        from .skill_extras import find_auto_cast_skill, consume_auto_cast_stacks
        ac_key, ac_data = find_auto_cast_skill(actor, target)
        if ac_key and ac_data:
            from src.game.systems.combat.helpers import effective_mp_cost
            ac_mp = effective_mp_cost(actor, ac_data) if "mp_cost" in ac_data else 999
            if actor.mp >= ac_mp:
                self.log.append(
                    f"  ⚡ **{actor.name}** kích hoạt **{ac_data.get('vi', ac_key)}** "
                    f"— tự động phát động!"
                )
                cast_skill(self, actor, target, ac_key, ac_data, ac_mp)
                consume_auto_cast_stacks(self, actor, target, ac_data)
                return

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
            if target.is_alive():
                self._fire_hits_taken_procs(target, actor)
                self._fire_heavy_hit_procs(target, actor)
            return

        skill_data = registry.get_skill(skill_key)
        if not skill_data:
            return

        # Default aligned with _choose_skill (999) so a skill with a missing
        # mp_cost field is treated identically in both paths. ``effective_mp_cost``
        # folds in the actor's per-element MP-cost multiplier (Thiên Nhất Sinh
        # Thủy Trận amps thuy MP) so this affordability gate matches the spend.
        from src.game.systems.combat.helpers import effective_mp_cost
        if "mp_cost" in skill_data:
            mp_cost = effective_mp_cost(actor, skill_data)
        else:
            mp_cost = 999
        if actor.mp < mp_cost:
            self.log.append(
                f"  💤 **{actor.name}** không đủ MP cho *{skill_data.get('vi', skill_key)}* "
                f"({actor.mp}/{mp_cost}) — tấn công cơ bản."
            )
            auto_attack(self, actor, target)
            if target.is_alive():
                self._fire_hits_taken_procs(target, actor)
                self._fire_heavy_hit_procs(target, actor)
            return

        cast_skill(self, actor, target, skill_key, skill_data, mp_cost)

        # Reactive proc — if the target now sits on a multiple of any of
        # their ``proc_on_hits_taken`` thresholds, fire that skill back at
        # the actor before the formation barrage runs.
        if target.is_alive():
            self._fire_hits_taken_procs(target, actor)
            self._fire_heavy_hit_procs(target, actor)

        # Parallel formation barrage — each active formation whose signature
        # skill is off cooldown + affordable fires after the main cast.
        # Gives multi-slot Trận Tu true simultaneity: all active formations
        # act each turn rather than time-sharing the main rotation. The
        # main_skill_data hand-off lets ``per_hit_followup`` formations
        # echo once per main-skill hit.
        if target.is_alive():
            fire_formation_skills(self, actor, target, main_skill_data=skill_data)

    def _fire_self_evade_procs(
        self, defender: Combatant, attacker: Combatant,
    ) -> None:
        """Fire any of ``defender``'s on-evade reactive casts.

        Two trigger shapes are supported:
          • ``proc_on_self_evade: true`` — the skill itself casts back at
            the attacker (Hỏa Vân Tàn Ảnh Kiếm pattern).
          • ``proc_on_self_evade_cast: "<skill_key>"`` — auto-casts the
            *named* skill if the holder owns it (Phượng Hoàng Triển Sí
            chains into Phượng Hoàng Chân Hỏa). The named skill's MP +
            cooldown still apply, so chaining a heavy defensive isn't free.

        No threshold gating — both fire every successful evade. MP and
        cooldown for the GATING skill (proc_on_self_evade) and the NAMED
        skill (proc_on_self_evade_cast) are both enforced.

        Routed through ``cast_skill`` with ``_suppress_extras=True`` so
        the inner cast can't recursively trigger another evade-proc chain.
        """
        if not defender.is_alive() or not attacker.is_alive():
            return
        for sk in defender.skill_keys:
            data = registry.get_skill(sk)
            if data is None:
                continue
            # Direct proc — fire this skill itself back at the attacker.
            if data.get("proc_on_self_evade"):
                from src.game.systems.combat.helpers import effective_mp_cost
                mp_cost = effective_mp_cost(defender, data)
                if defender.mp < mp_cost:
                    continue
                if defender.skill_on_cooldown(sk):
                    continue
                self.log.append(
                    f"  🗡️ **{defender.name}** né tránh → phản chiêu "
                    f"**{data.get('vi', sk)}**"
                )
                cast_skill(self, defender, attacker, sk, data, mp_cost,
                           _suppress_extras=True)
                continue
            # Indirect proc — auto-cast a NAMED skill that the holder also
            # owns. ``cast_target`` defaults to the attacker but a "self"
            # config routes to the holder (useful for defensive self-buffs).
            named_key = data.get("proc_on_self_evade_cast")
            if named_key:
                if named_key not in defender.skill_keys:
                    continue  # skill not equipped — silently skip
                named_data = registry.get_skill(named_key)
                if named_data is None:
                    continue
                from src.game.systems.combat.helpers import effective_mp_cost
                named_mp = effective_mp_cost(defender, named_data)
                if defender.mp < named_mp:
                    continue
                if defender.skill_on_cooldown(named_key):
                    continue
                cast_target_cfg = data.get("proc_on_self_evade_target", "self")
                cast_target = defender if cast_target_cfg == "self" else attacker
                self.log.append(
                    f"  🦅 **{defender.name}** né tránh → tự kích "
                    f"**{named_data.get('vi', named_key)}** "
                    f"(do **{data.get('vi', sk)}**)"
                )
                cast_skill(self, defender, cast_target, named_key, named_data,
                           named_mp, _suppress_extras=True)

    def _fire_target_evades_procs(
        self, attacker: Combatant, defender: Combatant,
    ) -> None:
        """Fire any of ``attacker``'s ``proc_on_target_evades`` skills whose
        threshold the defender's ``evades_count`` just crossed.

        Triggered after each successful evade. Mirror of
        ``_fire_hits_taken_procs`` but keyed off the defender's evade
        counter rather than the attacker's hit-taken counter — used by
        skills like Bắc Minh Hữu Ngư that punish dodge-stacking targets.

        Bounded recursion: the proc's outgoing hit goes through
        ``cast_skill``, which can resolve as evaded too and bump the
        defender's counter again — but we only fire from this single call
        site, so a chain of N evades fires the proc at most once per
        outer ``_take_turn`` resolution.
        """
        if defender.evades_count <= 0 or not attacker.is_alive():
            return
        for sk in attacker.skill_keys:
            data = registry.get_skill(sk)
            if data is None:
                continue
            n = int(data.get("proc_on_target_evades") or 0)
            if n <= 0:
                continue
            if defender.evades_count % n != 0:
                continue
            from src.game.systems.combat.helpers import effective_mp_cost
            mp_cost = effective_mp_cost(attacker, data)
            if attacker.mp < mp_cost:
                continue
            if attacker.skill_on_cooldown(sk):
                continue
            self.log.append(
                f"  🐟 **{attacker.name}** kích hoạt **{data.get('vi', sk)}** "
                f"(địch né tránh thứ {defender.evades_count})"
            )
            cast_skill(self, attacker, defender, sk, data, mp_cost,
                       _suppress_extras=True)

    def _fire_hits_taken_procs(
        self, holder: Combatant, attacker: Combatant,
    ) -> None:
        """Fire any of ``holder``'s ``proc_on_hits_taken`` skills whose
        threshold the holder's ``hits_taken`` counter just crossed.

        Triggered after each non-DoT incoming hit on ``holder``. The proc
        is treated as a free reactive cast: MP and cooldown still apply
        (so a misconfigured proc with non-zero MP can fizzle), and the
        proc's own damage routes through the standard ``cast_skill``
        pipeline. Multiple thresholds firing on the same hit is allowed
        and rare in practice; we iterate in skill-key order for
        determinism.

        Bounded recursion: the proc's outgoing hit increments ``attacker``'s
        own ``hits_taken``, but we only fire the holder's procs from the
        explicit call site in ``_take_turn`` — the proc-cast itself doesn't
        recurse into another ``_fire_hits_taken_procs`` call.
        """
        if holder.hits_taken <= 0:
            return
        for sk in holder.skill_keys:
            data = registry.get_skill(sk)
            if data is None:
                continue
            n = int(data.get("proc_on_hits_taken") or 0)
            if n <= 0:
                continue
            if holder.hits_taken % n != 0:
                continue
            from src.game.systems.combat.helpers import effective_mp_cost
            mp_cost = effective_mp_cost(holder, data)
            if holder.mp < mp_cost:
                continue
            if holder.skill_on_cooldown(sk):
                continue
            self.log.append(
                f"  ⚡ **{holder.name}** phản kích **{data.get('vi', sk)}** "
                f"(đòn nhận thứ {holder.hits_taken})"
            )
            cast_skill(self, holder, attacker, sk, data, mp_cost)

    def _fire_heavy_hit_procs(
        self, holder: Combatant, attacker: Combatant,
    ) -> None:
        """Fire ``proc_on_heavy_hit_pct`` passives that just crossed their
        threshold this round.

        Each such skill carries a fraction (e.g. 0.30) — when the holder's
        ``damage_taken_this_turn`` reaches ``hp_max × threshold`` and the
        skill is off cooldown, the passive activates:

          1. ``BuffBatDietHoaChung`` is applied on the holder (covers
             this turn's tail + the next full turn, ``skips_turn`` blocks
             the next action, +70% final_dmg_reduce mitigates incoming hits).
          2. ``bat_diet_retaliate_pending`` is set to 2 so the periodic
             phase fires the delayed retaliation at end of next round.
          3. The skill's cooldown is set per its ``cooldown`` field.

        The retaliation magnitudes (``retaliate_dmg_pct_max_hp``,
        ``retaliate_stun_turns``) are read at firing time from the same
        skill data, so per-cast overrides are unnecessary.
        """
        if holder.damage_taken_this_turn <= 0 or not holder.is_alive():
            return
        threshold_hp = 0
        for sk in holder.skill_keys:
            data = registry.get_skill(sk)
            if data is None:
                continue
            pct = float(data.get("proc_on_heavy_hit_pct") or 0.0)
            if pct <= 0:
                continue
            if holder.skill_on_cooldown(sk):
                continue
            threshold_hp = int(holder.hp_max * pct)
            if holder.damage_taken_this_turn < threshold_hp:
                continue
            # Trigger fires.
            buff_key = EffectKey.BUFF_BAT_DIET_HOA_CHUNG.value
            buff_meta = EFFECTS.get(buff_key)
            buff_dur = default_duration(buff_key)
            holder.apply_effect(buff_key, buff_dur)
            holder.bat_diet_retaliate_pending = 2
            holder.set_cooldown(sk, int(data.get("cooldown", 0)))
            emoji = buff_meta.emoji if buff_meta else "🔥"
            self.log.append(
                f"  {emoji} **{holder.name}** kích **{data.get('vi', sk)}** "
                f"(nhận {holder.damage_taken_this_turn:,}/{threshold_hp:,} HP "
                f"trong lượt — hỏa chủng bất diệt cuộn thân, +70% giảm ST, "
                f"khoá hành động 1 lượt)"
            )
            return  # one heavy-hit passive per round is plenty

    def _try_transfer_debuffs(self, actor: Combatant, opponent: Combatant) -> None:
        """Kính Hoa Thủy Nguyệt — once per turn, roll the buff's transfer
        chance; on success, pop a random non-stack debuff/CC off the actor
        and re-stamp it on the opponent with the same remaining duration +
        per-instance override. Stack-based DoTs (burn/bleed/shock/poison)
        are excluded because their actual damage lives on per-stack counters,
        not the effect entry — transferring the entry alone would be a no-op.
        Hard-CC immunity on the opponent silently skips the transfer for
        skips_turn / prevents_skills effects so a world boss can't be
        chain-stunned via mirror.
        """
        if not actor.has_effect(EffectKey.BUFF_KINH_HOA_THUY_NGUYET):
            return
        from src.game.engine.effects import EffectKind
        chance = float(get_combat_modifiers(actor).get("debuff_transfer_on_turn_pct", 0.0))
        if chance <= 0:
            return
        candidates = []
        for k in list(actor.effects):
            meta = EFFECTS.get(k)
            if meta is None:
                continue
            if meta.kind not in (EffectKind.DEBUFF, EffectKind.CC):
                continue
            if not meta.cleansable:
                continue
            if meta.stack_kind:
                continue
            candidates.append(k)
        if not candidates:
            return
        if self.rng.random() >= chance:
            return
        pick = self.rng.choice(candidates)
        meta = EFFECTS[pick]
        if opponent.immune_hard_cc and (meta.skips_turn or meta.prevents_skills):
            return
        if pick == EffectKey.DEBUFF_DOC_TO and opponent.poison_immunity:
            return
        remaining = actor.effects.get(pick, 0)
        override = actor.effect_overrides.get(pick)
        actor.effects.pop(pick, None)
        actor.effect_overrides.pop(pick, None)
        opponent.apply_effect(pick, remaining, overrides=override)
        self.log.append(
            f"  🪞 **{actor.name}** Kính Hoa Thủy Nguyệt → đẩy ngược "
            f"**{meta.vi}** ({remaining}t) sang **{opponent.name}**"
        )

    def _refresh_luu_tinh_can_nguyet(
        self, actor: Combatant, opponent: Combatant,
    ) -> None:
        """Refresh Lưu Tinh Cản Nguyệt's dynamic stat layer.

        The aura base ``spd_pct: 0.20`` lives on the EffectMeta (always-on
        while the aura is active). The DYNAMIC layer — ``+150 Né Tránh`` and
        ``+5% Tốc Độ`` per distinct fire DoT on the opponent — is rewritten
        each turn into the holder's ``effect_overrides`` under
        ``stat_bonus.evasion_rating`` and ``stat_bonus.spd_pct``. The
        override magnitude wins over the meta default per stat
        (``get_combat_modifiers`` standard behavior), so we always preserve
        the base 0.20 by writing ``0.20 + 0.05 × count``.

        Magnitudes are tunable per-skill via the cast's effect_overrides:
        ``_lt_base_spd_pct``, ``_lt_per_dot_evasion``, ``_lt_per_dot_spd_pct``.
        These config keys are popped from the aggregated stat dict by the
        cleanup pass in ``effects.get_combat_modifiers`` so they don't leak.
        """
        buff_key = EffectKey.BUFF_LUU_TINH_CAN_NGUYET.value
        if not actor.has_effect(buff_key):
            return
        fire_dots = count_elemental_dots(opponent, "hoa")
        ovr = actor.effect_overrides.setdefault(buff_key, {})
        sb = ovr.setdefault("stat_bonus", {})
        base_spd = float(sb.get("_lt_base_spd_pct", 0.20))
        per_dot_eva = float(sb.get("_lt_per_dot_evasion", 150.0))
        per_dot_spd = float(sb.get("_lt_per_dot_spd_pct", 0.05))
        sb["spd_pct"] = base_spd + per_dot_spd * fire_dots
        sb["evasion_rating"] = per_dot_eva * fire_dots

    def _process_luu_ly_tinh_hoa(self, actor: Combatant, opponent: Combatant) -> None:
        """Lưu Ly Tịnh Hỏa — per-turn cleanse aura keyed off opponent fire DoTs.

        For every distinct fire-element DoT currently active on ``opponent``,
        roll the buff's cleanse chance (default 30%). Each successful roll
        strips one random cleansable effect off ``actor`` and increments
        ``luu_ly_tinh_hoa_stacks`` (capped at the holder's stack cap). The
        scaling rule on ``BuffLuuLyTinhHoa`` then folds stacks × per_unit
        into ``crit_res_rating`` via ``get_combat_modifiers``.

        Cleansable picking mirrors the Quang ``try_cleanse`` path — uses
        the data-driven ``EffectMeta.cleansable`` flag so oddly-keyed
        debuffs (e.g. ``EffectNgungDong``) are also eligible.
        """
        if not actor.has_effect(EffectKey.BUFF_LUU_LY_TINH_HOA):
            return
        # Distinct fire DoT kinds on opponent — same filter as
        # ``count_elemental_dots`` but returns the list of keys so each
        # roll below can quote the originating fire DoT in the log.
        fire_dot_keys = [
            k for k in opponent.effects
            if (m := EFFECTS.get(k)) is not None
            and m.dot_element == "hoa"
            and (
                m.dot_pct > 0
                or m.stack_kind
                or m.dot_caster_hp_pct > 0
                or m.dot_caster_matk_scale > 0
            )
        ]
        if not fire_dot_keys:
            return
        # Cleanse chance lives in the buff's stat_bonus so per-skill
        # effect_overrides flow through naturally. Default 0.30.
        buff_ovr = actor.effect_overrides.get(EffectKey.BUFF_LUU_LY_TINH_HOA.value) or {}
        ovr_stats = buff_ovr.get("stat_bonus") or {}
        chance = float(ovr_stats.get(
            "luu_ly_cleanse_chance",
            EFFECTS[EffectKey.BUFF_LUU_LY_TINH_HOA].stat_bonus.get("luu_ly_cleanse_chance", 0.30),
        ))
        cap = effective_stack_cap(actor, EffectKey.BUFF_LUU_LY_TINH_HOA.value)
        for fire_key in fire_dot_keys:
            if actor.luu_ly_tinh_hoa_stacks >= cap:
                break
            if self.rng.random() >= chance:
                continue
            cleansable_keys = [
                k for k in list(actor.effects)
                if (m := EFFECTS.get(k)) is not None and m.cleansable
            ]
            if not cleansable_keys:
                break  # nothing left to cleanse — bail rather than burn rolls
            removed = self.rng.choice(cleansable_keys)
            removed_meta = EFFECTS.get(removed)
            del actor.effects[removed]
            actor.effect_overrides.pop(removed, None)
            actor.luu_ly_tinh_hoa_stacks += 1
            fire_meta = EFFECTS.get(fire_key)
            self.log.append(
                f"  🔮 **{actor.name}** Lưu Ly Tịnh Hỏa "
                f"({fire_meta.vi if fire_meta else fire_key} → Thanh Tẩy "
                f"*{removed_meta.vi if removed_meta else removed}*) "
                f"[×{actor.luu_ly_tinh_hoa_stacks}/{cap}]"
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

        Reactive / passive entries are filtered out of normal selection:
          • ``proc_on_hits_taken`` — fires reactively from the post-cast hook
            in ``_take_turn``, never as the actor's main action.
          • ``proc_on_target_status`` — fires from ``inflict_debuff`` when the
            named status lands on a target (e.g. Diệt Tận on stun), never as
            the actor's main action.
          • ``aura: true`` or ``category: "passive"`` — applied once at fight
            start, never picked here.
          • ``usage_limit`` exhausted — the per-fight cast counter on the
            combatant has reached the cap.

        ``trigger: "phase_one_start"`` skills are picked first if they
        haven't been used yet, so a Tận-Diệt-class opener fires on the
        boss's first usable turn regardless of base_dmg ordering.
        """
        known = []
        for sk in actor.skill_keys:
            data = registry.get_skill(sk)
            if data is None:
                continue
            if data.get("proc_on_hits_taken"):
                continue
            if data.get("proc_on_target_status"):
                # Reactive — fires from inflict_debuff when its trigger
                # status lands on a target, never as a main-action pick.
                continue
            if data.get("proc_on_target_evades"):
                # Reactive — fires from cast_skill's evade branch via
                # _fire_target_evades_procs when the defender's evade
                # counter crosses the threshold. Never normal-picked.
                continue
            if data.get("aura") or data.get("category") == "passive":
                continue
            # Pure reactive skills: only fire from the auto-cast path
            # (find_auto_cast_skill). Never picked as the actor's normal turn.
            if data.get("auto_cast_on_buff_count"):
                continue
            # On-debuff-received reactive — only fires from inflict_debuff's
            # post-application hook in casting.py.
            if data.get("cast_on_debuff_received"):
                continue
            # Chain-only — skill exists solely as a follow-up cast triggered
            # by another skill's ``chain_skill`` spec (e.g. Thủy Long Ngâm
            # fired every 4th cast of Thủy Long Đạn). Never picked as a
            # normal turn action.
            if data.get("chain_only"):
                continue
            limit = data.get("usage_limit")
            if limit is not None and actor.skill_usage_count.get(sk, 0) >= int(limit):
                continue
            known.append(sk)
        if not known:
            return None, "none"

        off_cooldown = [sk for sk in known if not actor.skill_on_cooldown(sk)]
        if not off_cooldown:
            return None, "cooldown"

        from src.game.systems.combat.helpers import effective_mp_cost
        affordable = [
            sk for sk in off_cooldown
            if (
                (d := registry.get_skill(sk)) is not None
                and "mp_cost" in d
                and actor.mp >= effective_mp_cost(actor, d)
            )
        ]
        if not affordable:
            return None, "mana"

        # Filter out skills whose ``requires_target_debuff_count`` gate isn't
        # satisfied — finishers like Cửu U Thần Trảo only become eligible once
        # the target carries enough debuffs. Counts only ``EffectKind.DEBUFF``
        # entries (CC and buffs don't qualify) so the gate stays semantically
        # tight. Falls back to the unfiltered list when *no* candidate qualifies
        # so the actor doesn't hard-stall when its rotation is gated.
        from src.game.engine.effects import EFFECTS, EffectKind
        opponent = self.enemy if actor is self.player else self.player
        target_debuff_count = sum(
            1 for k in opponent.effects
            if (m := EFFECTS.get(k)) and m.kind is EffectKind.DEBUFF
        )
        gated = [
            sk for sk in affordable
            if target_debuff_count
            >= int((registry.get_skill(sk) or {}).get("requires_target_debuff_count", 0))
        ]
        if gated:
            affordable = gated

        # Prioritise unused phase-one-start triggers — the boss's signature
        # opener (e.g. Tận Diệt) fires before any base_dmg-driven pick.
        opener = next(
            (
                sk for sk in affordable
                if (registry.get_skill(sk) or {}).get("trigger") == "phase_one_start"
                and actor.skill_usage_count.get(sk, 0) == 0
            ),
            None,
        )
        if opener is not None:
            return opener, "ok"

        return (
            max(affordable, key=lambda sk: (registry.get_skill(sk) or {}).get("base_dmg", 0)),
            "ok",
        )

    # ── Shared mutation helpers (called from multiple modules) ───────────

    def _apply_passive_auras(self, holder: Combatant, opponent: Combatant) -> None:
        """Stamp passive auras onto their targets at combat start.

        Two skill-data shapes are honored:
          • ``aura: true`` — the entire skill is passive; its full ``effects``
            list applies to ``aura_targets`` (default ``["self"]``).
          • ``passive_effects: [...]`` — the skill is otherwise active
            (movement / attack / defense), but the listed effects are
            stamped onto self at fight start. Lets one JSON entry expose
            both an active cast AND a permanent passive aura.

        Each effect honors the per-cast ``effect_overrides[<key>]``
        magnitudes — same routing as a normal cast, just without MP cost
        or cooldown.

        Idempotent against duplicate registry entries: each effect is
        applied via ``apply_effect``, which already merges magnitudes by
        max-magnitude per stat — so re-running this hook a second time
        (e.g. simulator harness) doesn't compound bonuses.
        """
        from src.game.engine.effects import EFFECTS, default_duration
        for skill_key in holder.skill_keys:
            data = registry.get_skill(skill_key)
            if not data:
                continue

            if data.get("aura"):
                aura_keys = list(data.get("effects") or [])
                targets_cfg = data.get("aura_targets") or ["self"]
            elif data.get("passive_effects"):
                # Hybrid skill — active cast lives in ``effects``, passive
                # aura lives in ``passive_effects``. Always self-targeted.
                aura_keys = list(data.get("passive_effects") or [])
                targets_cfg = ["self"]
            else:
                continue

            target_map = {"self": holder, "opponent": opponent}
            recipients = [
                target_map[t] for t in targets_cfg if t in target_map
            ]
            if not recipients or not aura_keys:
                continue
            overrides = data.get("effect_overrides") or {}
            for effect_key in aura_keys:
                meta = EFFECTS.get(effect_key)
                if not meta:
                    continue
                ov = overrides.get(effect_key) or {}
                dur = int(ov.get("duration", default_duration(effect_key)))
                stamp = {k: v for k, v in ov.items() if k != "duration"} or None
                # ``hp_max_pct`` on a BUFF is a one-shot grow — mirror of the
                # one-shot shrink on debuffs handled in cast_skill. Mutate the
                # recipient's hp_max + hp before stamping so the buff's
                # bigger-pool intent applies immediately.
                merged_sb = dict(meta.stat_bonus)
                if stamp and "stat_bonus" in stamp:
                    merged_sb.update(stamp["stat_bonus"])
                grow_pct = float(merged_sb.get("hp_max_pct", 0.0))
                for recipient in recipients:
                    if grow_pct > 0 and not recipient.has_effect(effect_key):
                        delta = max(0, int(recipient.hp_max * grow_pct))
                        recipient.hp_max += delta
                        recipient.hp += delta
                    recipient.apply_effect(effect_key, dur, overrides=stamp)
                    self.log.append(
                        f"  {meta.emoji} **{recipient.name}** chịu hào quang "
                        f"**{meta.vi}** từ *{data.get('vi', skill_key)}*."
                    )

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

        Falls back to ``_try_buff_revive`` when phoenix doesn't fire — a
        Thiên-Sứ-Hộ-Mệnh-class buff with ``revive_hp_pct`` in its
        ``stat_bonus`` triggers as a free, one-shot revive that strips the
        buff itself rather than every effect on the combatant.
        """
        if combatant.is_alive():
            return False
        if combatant.phoenix_revive_used or combatant.phoenix_revive_pct <= 0:
            return self._try_buff_revive(combatant)

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
        combatant.poison_stacks = 0
        combatant.effects.clear()

        buff_tag = f" · ST/Giáp +{buff * 100:.0f}%" if buff > 0 else ""
        self.log.append(
            f"  🔥🦅 **{combatant.name}** **NIẾT BÀN TRÙNG SINH!** "
            f"Hồi sinh +{revived_hp:,}/{combatant.hp_max:,} HP{buff_tag}"
        )
        return True

    def _try_buff_revive(self, combatant: Combatant) -> bool:
        """Buff-driven one-shot revive (Thiên Sứ Hộ Mệnh class).

        If the combatant carries an active effect whose ``stat_bonus`` (or
        per-instance override) declares ``revive_hp_pct > 0``, restore HP
        to that fraction of hp_max, strip JUST that effect (the buff's
        other bonuses go with it), and return True. Unlike phoenix revive,
        the rest of the combatant's effects + DoT stacks survive — only
        the protective buff is consumed.
        """
        if combatant.is_alive():
            return False
        from src.game.engine.effects import EFFECTS
        for effect_key in list(combatant.effects):
            meta = EFFECTS.get(effect_key)
            if not meta:
                continue
            override = combatant.effect_overrides.get(effect_key) or {}
            override_sb = override.get("stat_bonus") or {}
            revive_pct = float(override_sb.get(
                "revive_hp_pct",
                meta.stat_bonus.get("revive_hp_pct", 0.0),
            ))
            if revive_pct <= 0:
                continue

            revived_hp = max(1, int(combatant.hp_max * revive_pct))
            combatant.hp = revived_hp
            combatant.effects.pop(effect_key, None)
            combatant.effect_overrides.pop(effect_key, None)
            self.log.append(
                f"  {meta.emoji} **{combatant.name}** **{meta.vi}** vỡ tan — "
                f"hồi sinh +{revived_hp:,}/{combatant.hp_max:,} HP, "
                f"mất hào quang phù hộ."
            )
            return True
        return False

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
        # Effect-driven heal-taken reduction (Vô Đạo, future cleansable
        # heal-block debuffs). Read off ``get_combat_modifiers`` so it picks
        # up per-cast ``effect_overrides`` automatically. Capped at 90% so a
        # stack of multiple sources can't make heals strictly zero.
        mods = get_combat_modifiers(combatant)
        heal_reduce = float(mods.get("heal_taken_reduce", 0.0))
        if heal_reduce > 0:
            amount = max(1, int(amount * (1.0 - min(0.90, heal_reduce))))
        # Mirror amplifier — Phật Quang Phổ Chiếu and any future heal-amp
        # buff stamp ``heal_taken_bonus`` for the holder. Applied after
        # reduction so a 30% amp + 50% reduce reads as ``×1.3 × 0.5``.
        heal_bonus = float(mods.get("heal_taken_bonus", 0.0))
        if heal_bonus > 0:
            amount = max(1, int(amount * (1.0 + heal_bonus)))
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

        # Endure announcement — flag is set inside Combatant.take_damage when
        # the survival-floor clamp engages. Log it here so the player sees the
        # save in the same turn it happened, then reset.
        if combatant.endure_just_triggered:
            self.log.append(
                f"  🌿 **{combatant.name}** **Cội Nguồn Bất Tận** — sống sót ở "
                f"{combatant.hp:,}/{combatant.hp_max:,} HP "
                f"(hồi chiêu {combatant.endure_remaining}t)"
            )
            combatant.endure_just_triggered = False

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

            # Curse-class extras: a DoT meta may also drain shield and MP each
            # tick (Lục Hồn Chú-style). Skipped when the combatant has no shield
            # or MP to drain so the log stays clean.
            tick_meta = EFFECTS.get(effect_key)
            if tick_meta is not None:
                if tick_meta.dot_shield_drain_pct > 0 and combatant.shield > 0:
                    shield_loss = min(
                        combatant.shield,
                        max(1, int(dot_dmg * tick_meta.dot_shield_drain_pct)),
                    )
                    combatant.shield -= shield_loss
                    self.log.append(
                        f"    🛡️ **{combatant.name}** mất {shield_loss:,} khiên do {tick_meta.vi}"
                    )
                if tick_meta.dot_mp_drain_pct > 0 and combatant.mp > 0:
                    mp_loss = min(
                        combatant.mp,
                        max(1, int(combatant.mp_max * tick_meta.dot_mp_drain_pct)),
                    )
                    combatant.mp -= mp_loss
                    self.log.append(
                        f"    💙 **{combatant.name}** -{mp_loss:,} MP do {tick_meta.vi}"
                    )

            # Moc build: leech a fraction of DoT damage as HP + MP to the
            # applier. Healing applies silently — the per-tick log line was
            # removed so the combat feed isn't confused with the direct-hit
            # ``life_steal_pct`` mechanic (which intentionally ignores DoT).
            if opponent and opponent.dot_leech_pct > 0 and opponent.is_alive():
                leech = max(1, int(dot_dmg * opponent.dot_leech_pct))
                self._apply_heal(opponent, leech)
                mp_gain = min(opponent.mp_max - opponent.mp, leech // 2)
                if mp_gain > 0:
                    opponent.mp += mp_gain

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

        # U Minh Quỷ Hỏa — pure MP-burn DoT. While DebuffUMinh is active,
        # drain ``u_minh_stacks × u_minh_per_stack_mp_pct × mp_max`` MP each
        # turn. No HP damage; runs alongside the DoT loop because the meta
        # doesn't enter ``get_periodic_damage`` (no dot_pct / stack_kind).
        if (
            combatant.has_effect(EffectKey.DEBUFF_U_MINH)
            and combatant.u_minh_stacks > 0
            and combatant.mp > 0
        ):
            u_meta = EFFECTS.get(EffectKey.DEBUFF_U_MINH)
            drain_pct = combatant.u_minh_stacks * combatant.u_minh_per_stack_mp_pct
            mp_loss = min(combatant.mp, max(1, int(combatant.mp_max * drain_pct)))
            combatant.mp -= mp_loss
            self.log.append(
                f"    👻 **{combatant.name}** -{mp_loss:,} MP do "
                f"**{u_meta.vi if u_meta else 'U Minh'}** "
                f"[×{combatant.u_minh_stacks}/"
                f"{effective_stack_cap(combatant, EffectKey.DEBUFF_U_MINH.value)}]"
            )

        # Bất Diệt Hỏa Chủng — delayed retaliation. The heavy-hit passive
        # set ``bat_diet_retaliate_pending = 2`` when it fired; each
        # periodic phase decrements by 1. When it transitions from 1 → 0
        # the retaliation fires: deal ``retaliate_dmg_pct_max_hp × hp_max``
        # fire damage to the opponent and stun them for
        # ``retaliate_stun_turns``. Magnitudes are read from the same
        # passive's skill data on the holder so JSON drives the tuning.
        if combatant.bat_diet_retaliate_pending > 0:
            combatant.bat_diet_retaliate_pending -= 1
            if combatant.bat_diet_retaliate_pending == 0 and opponent and opponent.is_alive():
                passive_data = None
                for sk in combatant.skill_keys:
                    d = registry.get_skill(sk)
                    if d and float(d.get("proc_on_heavy_hit_pct") or 0.0) > 0:
                        passive_data = d
                        break
                if passive_data is not None:
                    dmg_pct = float(passive_data.get("retaliate_dmg_pct_max_hp", 0.15))
                    stun_turns = int(passive_data.get("retaliate_stun_turns", 1))
                    burst = max(1, int(combatant.hp_max * dmg_pct))
                    tag = colorize_damage(f"-{burst:,} HP", "hoa")
                    opponent.take_damage(burst)
                    self.log.append(
                        f"  🔥 **{combatant.name}** bùng nổ **Bất Diệt Hỏa Chủng** "
                        f"→ **{opponent.name}** {tag} ({int(dmg_pct * 100)}% HP tối đa)"
                    )
                    # Stun the opponent — routed through inflict_debuff so
                    # immune_hard_cc / effect_resist gates apply normally.
                    # Duration is ``stun_turns + 1``: the opponent's own
                    # periodic phase runs RIGHT AFTER this hook (same round),
                    # ticking the just-applied stun once. The +1 ensures the
                    # opponent still skips ``stun_turns`` actual turns.
                    if stun_turns > 0 and opponent.is_alive():
                        stun_meta = EFFECTS.get(EffectKey.CC_STUN.value)
                        if stun_meta is not None:
                            from .casting import inflict_debuff
                            inflict_debuff(
                                self, EffectKey.CC_STUN.value, stun_meta,
                                opponent, actor=combatant,
                                overrides={"duration": stun_turns + 1},
                            )

        # Summons — tick each spawned helper for its scheduled damage. Done
        # before solar/wither auras so a summon's hit can stack with auras
        # in the same round-end pulse.
        if combatant.is_alive() and combatant.summons and opponent and opponent.is_alive():
            from .skill_extras import tick_summons
            tick_summons(self, combatant, opponent)

        # Solar aura — Thái Dương Thần Thể tier passive: every turn, deal
        # fire damage to the opponent equal to (combatant.hp_max × solar_aura_pct),
        # boosted by final_dmg_bonus + burn_dmg_bonus, with bonus_dmg_vs_burn
        # vs already-burning targets and element_pen reducing the target's
        # hoa resistance. Independent of skill actions and DoT ticks.
        if (
            combatant.is_alive()
            and combatant.solar_aura_pct > 0
            and opponent
            and opponent.is_alive()
        ):
            base = int(combatant.hp_max * combatant.solar_aura_pct)
            if base > 0:
                mult = (
                    1.0 + combatant.final_dmg_bonus
                    + float(combatant.dot_dmg_bonus_by_kind.get("burn", 0.0))
                )
                if opponent.burn_stacks > 0 and combatant.bonus_dmg_vs_burn > 0:
                    mult += combatant.bonus_dmg_vs_burn
                target_res = max(
                    0.0,
                    min(MAX_ELEMENTAL_RES, opponent.resistances.get("hoa", 0.0) - combatant.element_pen.get("hoa", 0.0)),
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
                    min(MAX_ELEMENTAL_RES, opponent.resistances.get("moc", 0.0) - combatant.element_pen.get("moc", 0.0)),
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

        # Energy Shield recharge (PoE-style): the pause counter is decremented
        # each periodic phase; while > 0, ``shield_regen_pct/flat`` are skipped
        # so a recently-hit defender doesn't get free shield instantly. After
        # the pause elapses, regen ticks normally up to ``shield_cap``.
        if combatant.shield_recharge_pause > 0:
            combatant.shield_recharge_pause -= 1

        # Active-buff layer for shield_regen_pct (e.g. Quang Minh Tung Hoành
        # Bộ active stamps +0.5% on top of any static investment).
        shield_regen_mods = get_combat_modifiers(combatant)
        effective_shield_regen_pct = (
            combatant.shield_regen_pct + shield_regen_mods.get("shield_regen_pct", 0.0)
        )
        if (
            combatant.is_alive()
            and combatant.shield_recharge_pause == 0
            and (effective_shield_regen_pct > 0 or combatant.shield_regen_flat > 0)
        ):
            # ``shield_regen_pct`` scales off the holder's own shield_cap so
            # high-shield builds regenerate proportionally to their investment.
            # ``shield_regen_flat`` adds on top.
            regen = int(combatant.shield_cap() * effective_shield_regen_pct) + combatant.shield_regen_flat
            gained = combatant.add_shield(regen)
            if gained > 0:
                self.log.append(
                    f"  🪨 **{combatant.name}** Hộ Thuẫn hồi +{gained:,} khiên "
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
            # Mirrors HP regen: stat_bonus modifiers (e.g. DebuffLinhLucKiet's
            # mp_regen_pct: -0.50) apply on top of the base. Negative aggregate
            # zeroes the regen rather than draining MP.
            effective_mp_regen_pct = max(
                0.0, combatant.mp_regen_pct + mods.get("mp_regen_pct", 0.0)
            )
            mp_pct_regen = int(combatant.mp_max * effective_mp_regen_pct) if effective_mp_regen_pct > 0 else 0
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

        # Endure cooldown — Đế Sinh Mộc Thể's "Cội Nguồn Bất Tận" must wait
        # ``endure_cooldown`` turns between triggers. Counter decrements
        # whether or not the holder is hit; expires naturally so the floor
        # is ready by the next time the holder gets killed.
        if combatant.endure_remaining > 0:
            combatant.endure_remaining -= 1

        expired = combatant.tick_effects()
        # Auto-cycling effects: when an effect with ``on_expire_apply``
        # expires naturally (NOT via cleanse — that path skips this hook),
        # immediately chain-apply the linked follow-up. Powers self-loops
        # like Thiên Ma Giải Thể (BuffThienMa ↔ DebuffThienMaPost).
        for key in expired:
            meta = EFFECTS.get(key)
            if not meta or not meta.on_expire_apply:
                continue
            next_key, next_override = meta.on_expire_apply
            next_meta = EFFECTS.get(next_key)
            if not next_meta:
                continue
            ov = next_override or {}
            dur = int(ov.get("duration", default_duration(next_key)))
            stamp = {k: v for k, v in ov.items() if k != "duration"} or None
            combatant.apply_effect(next_key, dur, overrides=stamp)
            self.log.append(
                f"  {next_meta.emoji} **{combatant.name}** chuyển hóa "
                f"**{meta.vi}** → **{next_meta.vi}** ({dur}t)"
            )

        # Lục Dục Thiên Ma Vũ — auto-cycling six-desires passive. Driven by
        # skill ownership rather than aura plumbing so the cycle survives a
        # cleanse on the marker.
        if "SkillAmLucDucThienMaVu_R9" in combatant.skill_keys:
            self._tick_luc_duc_thien_ma_vu(combatant)

    # ── Lục Dục Thiên Ma Vũ cycle hook ───────────────────────────────────────
    _LUC_DUC_DESIRE_KEYS: tuple[str, ...] = (
        "BuffLucDucSac", "BuffLucDucThanh", "BuffLucDucHuong",
        "BuffLucDucVi", "BuffLucDucXuc", "BuffLucDucPhap",
    )

    def _tick_luc_duc_thien_ma_vu(self, combatant: Combatant) -> None:
        """Run one Lục Dục Thiên Ma Vũ cycle step on the holder.

        Phases:
          gaining → +2 desires per tick until 6 are active
          amp → all 6 + Cộng Minh marker, lasts one acting turn
          expired → 2 silent ticks with everything cleared
          → restart at gaining
        """
        if combatant.luc_duc_expire_turns_left > 0:
            combatant.luc_duc_expire_turns_left -= 1
            return

        amp_key = "BuffLucDucCongMinh"
        active_desires = [
            k for k in self._LUC_DUC_DESIRE_KEYS if combatant.has_effect(k)
        ]
        amp_active = combatant.has_effect(amp_key)

        # Amp resolved last turn — clear everything and start the silent phase.
        if amp_active and len(active_desires) == 6:
            for k in (*self._LUC_DUC_DESIRE_KEYS, amp_key):
                combatant.effects.pop(k, None)
                combatant.effect_overrides.pop(k, None)
            combatant.luc_duc_expire_turns_left = 2
            self.log.append(
                f"  💤 **{combatant.name}** Lục Dục tan biến — yên lặng 2 lượt."
            )
            return

        # Six desires reached — apply the amp marker; cycle settles next tick.
        if len(active_desires) == 6 and not amp_active:
            combatant.apply_effect(amp_key, 99)
            self.log.append(
                f"  🌌 **{combatant.name}** Lục Dục Cộng Minh — toàn bộ Lục Dục khuếch đại 20%."
            )
            return

        # Gain phase — stamp the next 2 desires that aren't already active.
        not_active = [
            k for k in self._LUC_DUC_DESIRE_KEYS if k not in active_desires
        ]
        applied: list[str] = []
        for k in not_active[:2]:
            combatant.apply_effect(k, 99)
            meta = EFFECTS.get(k)
            applied.append(meta.vi if meta else k)
        if applied:
            self.log.append(
                f"  🌒 **{combatant.name}** Lục Dục Thiên Ma Vũ — kích hoạt: {', '.join(applied)}"
            )

    def _roll_loot(self) -> list[dict]:
        if self.auto_mode and self.rng.random() >= AUTO_LOOT_DROP_RATE:
            return []
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
        # Per-enemy ``loot_luck_bonus`` (e.g. linh_can apex) lets high-tier
        # mobs sharing a single table reward better drops than their low-tier
        # counterparts in the same dungeon.
        enemy_luck = float((enemy_data or {}).get("loot_luck_bonus", 0.0))
        effective_luck = (
            self.loot_luck_pct
            + max(0.0, self.player.loot_luck_bonus)
            + enemy_luck
        )
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

        # Sát Khí Đại Thành — record the kill on the player_c. Stacks persist
        # across waves of a single dungeon entry (player_c is reused), reset
        # when build_player_combatant rebuilds for a fresh entry.
        if self.player.kill_buff_per_kill_pct > 0:
            cap = self.player.kill_buff_cap or 0
            if cap > 0 and self.player.kill_streak_stacks < cap:
                self.player.kill_streak_stacks += 1
                bonus = self.player.kill_buff_per_kill_pct * self.player.kill_streak_stacks
                self.log.append(
                    f"  🗡️ **{self.player.name}** Sát Khí Đại Thành "
                    f"[×{self.player.kill_streak_stacks}/{cap}] (+{bonus*100:.0f}% ST)"
                )

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

    def _burst_shield(self, actor: Combatant, target: Combatant, skill_data: dict) -> None:
        from .bursts import burst_shield
        burst_shield(self, actor, target, skill_data)

    def _burst_mana_stacks(self, actor: Combatant, target: Combatant, skill_data: dict) -> None:
        from .bursts import burst_mana_stacks
        burst_mana_stacks(self, actor, target, skill_data)
