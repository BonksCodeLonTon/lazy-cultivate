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
    AUTO_LOOT_DROP_RATE, ENEMY_BASE_MERIT_PER_WAVE, HEAL_CRIT_CHANCE,
    HEAL_CRIT_MULT,
)
from src.game.constants.effects import EffectKey
from src.game.engine import linh_can_effects as lc_effects
from src.game.engine.damage import colorize_damage
from src.game.engine.drop import roll_drops
from src.game.engine.effects import (
    EFFECTS, EffectKind, check_cc_skip_turn, check_prevents_skills,
    default_duration, get_combat_modifiers,
)
from src.game.systems.combatant import Combatant

from . import hooks  # noqa: F401 — @register_hook side-effects load with session
from . import phase as phase_lock
from . import start_aura
from .bursts import burst_mana_stacks, burst_shield
from .casting import (
    apply_skill_effects, auto_attack, cast_skill, fire_formation_skills,
    inflict_debuff,
)
from .context import TurnContext
from .helpers import effective_mp_cost, effective_spd, spd_extra_turn_pct
from .hooks import TurnPhase, run_phase
from .procs import apply_reactive_damage, run_on_hit_procs
from .skill_extras import consume_auto_cast_stacks, find_auto_cast_skill


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

    def _try_revive(self, dying: Combatant) -> bool:
        """Run the ON_REVIVE cascade on a freshly-killed combatant.

        Returns True when one of the registered revive hooks (phoenix,
        buff, Chân Mệnh Lôi Phù) brought ``dying`` back. Caller checks
        the return to decide whether the death is final.
        """
        if dying.is_alive():
            return False
        opponent = self.enemy if dying is self.player else self.player
        ctx = TurnContext(actor=dying, target=opponent, session=self)
        run_phase(TurnPhase.ON_REVIVE, ctx)
        return dying.is_alive()

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
        if not target.is_alive() and not self._try_revive(target):
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
            if not target.is_alive() and not self._try_revive(target):
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
            if not target.is_alive() and not self._try_revive(target):
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
            # Once-per-fight start-of-combat auras (Thôn Thiên Ma Khí,
            # passive ``aura: true`` skills, hybrid passive_effects). Both
            # sides fire so a player-carried aura also lands.
            start_aura.apply_stat_drain_aura(self, self.player, self.enemy)
            start_aura.apply_stat_drain_aura(self, self.enemy, self.player)
            start_aura.apply_passive_auras(self, self.player, self.enemy)
            start_aura.apply_passive_auras(self, self.enemy, self.player)
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

        if not self.player.is_alive() and not self._try_revive(self.player):
            return (self.log[start_idx:], self._defeat())
        if not self.enemy.is_alive() and not self._try_revive(self.enemy):
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

        # Pre-turn aura chain — registered hooks in priority order:
        #   10 kinh_hoa  (debuff transfer)
        #   20 luu_ly    (fire-DoT cleanse aura)
        #   30 luu_tinh  (dynamic evasion/spd refresh)
        #   40 lieu_nhu  (Mộc-DoT drift refresh)
        #   50 bo_bo     (heal-taken → mobility refresh)
        #   60 phu_dao   (altitude tier bump)
        # Adding a new pre-turn aura is one new module in combat/auras/.
        run_phase(TurnPhase.PRE_TURN, TurnContext(actor=actor, target=opponent, session=self))

        # Auto-cast on stacks — if the target carries enough of a tagged stack
        # (burn/bleed/shock), force-fire the matching finisher this turn instead
        # of the actor's normal pick. Off-cooldown is bypassed, but MP must be
        # available (falls back to normal flow if not).
        ac_key, ac_data = find_auto_cast_skill(actor, target)
        if ac_key and ac_data:
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

        # Buff-driven on-evade debuff stamps — Bạch Hổ Thiểm style. Any
        # active buff carrying ``on_evade_debuffs`` in its per-instance
        # override applies the listed debuffs to the attacker on a successful
        # evade. Each entry is either a string ``"DebuffKey"`` (defaults
        # only — meta magnitudes / duration) or a dict
        # ``{"key": "DebuffKey", "overrides": {...}, "duration": int}`` for
        # per-cast magnitudes. Routed through ``inflict_debuff`` so immunity
        # / debuff_immune_pct gating + reflect / Hồng-Liên reactions all fire
        # consistently.
        for buff_key in list(defender.effects.keys()):
            buff_ovr = defender.effect_overrides.get(buff_key) or {}
            entries = buff_ovr.get("on_evade_debuffs")
            if not entries:
                continue
            for entry in entries:
                if isinstance(entry, str):
                    deb_key, deb_ovr = entry, None
                else:
                    deb_key = entry.get("key")
                    deb_ovr = entry.get("overrides")
                    _dur = entry.get("duration")
                    if _dur is not None:
                        deb_ovr = {**(deb_ovr or {}), "duration": _dur}
                deb_meta = EFFECTS.get(deb_key) if deb_key else None
                if deb_meta is None or not attacker.is_alive():
                    continue
                inflict_debuff(
                    self, deb_key, deb_meta, attacker,
                    actor=defender, overrides=deb_ovr,
                )

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

    # ── Pre-turn aura chain extracted to ``combat/auras/`` ────────────────
    # The six methods that used to live here (_try_transfer_debuffs,
    # _process_luu_ly_tinh_hoa, _refresh_luu_tinh_can_nguyet,
    # _refresh_lieu_nhu_tuy_phong, _refresh_bo_bo_sinh_lien, plus the
    # inline BuffPhuDao altitude block) are now @register_hook handlers in
    # ``combat/auras/{kinh_hoa,luu_ly,luu_tinh,lieu_nhu,bo_bo,phu_dao}.py``.
    # ``_take_turn`` drives them through ``run_phase(TurnPhase.PRE_TURN, ctx)``.

    # (Bodies of these six pre-turn auras moved to combat/auras/*.py — see
    # the marker block above this section.)

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

    # _apply_passive_auras + _apply_stat_drain_aura moved to combat/start_aura.py
    # _try_phoenix_revive + _try_buff_revive + _try_chan_menh_loi_phu moved to
    # combat/revives.py as ON_REVIVE hooks. CombatSession._try_revive (defined
    # above) drives the cascade through run_phase().

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
        # Mộc Linh Cộng Sinh — bank the clamped-off overheal remainder into any
        # active ``overheal_reservoir`` buff. Generic over the buff key; no-op
        # when the holder carries no such buff or the heal fully applied.
        overheal = amount - applied
        if overheal > 0:
            from src.game.systems.combat.overheal_reservoir import capture_overheal
            capture_overheal(combatant, overheal)
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
            actor.add_stack("mana", actor.mana_stack_per_attack)
            if actor.mana_stacks > before:
                self.log.append(
                    f"    💠 Linh Khí Tích Tụ [×{actor.mana_stacks}/{actor.mana_stack_cap}]"
                )

    # ── Periodic / end-of-round + resolution ─────────────────────────────

    def _process_periodic(self, combatant: Combatant) -> None:
        """Run the PERIODIC hook chain (end-of-round effects).

        The full implementation lives in ``combat/periodic/`` — 8
        modules registered as PERIODIC hooks with priorities:
          10/15/70 endure   — survival announcements + Thánh Tuyền + cooldown
          20       dots     — DoT loop + U Minh + Bất Diệt
          30       summons  — per-summon damage tick
          40/45    auras    — Thái Dương + Khô Mộc
          50       regen    — Thổ shield + shield/HP/MP regen
          60       fortify  — Hào Quang Củng Cố stacks + braced decrement
          80       expiry   — tick_effects + on-expire hooks
          90       luc_duc  — Lục Dục Thiên Ma Vũ cycle
        """
        opponent = self.enemy if combatant is self.player else self.player
        run_phase(
            TurnPhase.PERIODIC,
            TurnContext(actor=combatant, target=opponent, session=self),
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
        # Per-wave merit — base × the killed enemy's ``realm_level`` (1..10).
        # ``systems/dungeon.py`` then multiplies by the wave's encounter-grade
        # ``merit_mult`` so harder waves pay more. Falls back to base × 1 for
        # tribulations / world bosses (not in the regular enemy registry) and
        # for any enemy missing ``realm_level``. Karma stays 0 — no consumers.
        enemy_data = (
            registry.get_enemy(self.enemy.key)
            or registry.tribulations.get(self.enemy.key)
        )
        realm_level = max(1, int((enemy_data or {}).get("realm_level", 1)))
        merit = ENEMY_BASE_MERIT_PER_WAVE * realm_level
        return CombatResult(
            reason=CombatEndReason.PLAYER_WIN, turns=self.turn,
            log=self.log, loot=loot, merit_gained=merit, karma_gained=0,
        )

    def _defeat(self) -> CombatResult:
        self.log.append(f"\n💀 **{self.player.name}** đã bại trận!")
        return CombatResult(
            reason=CombatEndReason.PLAYER_DEAD, turns=self.turn,
            log=self.log, loot=[], merit_gained=0, karma_gained=0,
        )

    # ── Thin delegates to module-level functions ─────────────────────────
    # Each method below routes a call to a function that lives in another
    # combat submodule. They stay on the session because TESTS reach
    # through the session for them — removing them would break the safety
    # net. The actual logic lives in the imported module; this block is
    # purely a public-surface contract.
    #
    # Callers:
    #   _run_on_hit_procs       — tests/test_combat_builds.py (16+ calls)
    #   _apply_reactive_damage  — tests/test_combat_builds.py
    #   _apply_skill_effects    — tests/test_combat_builds.py
    #   _burst_shield           — tests/test_combat_builds.py
    #   _burst_mana_stacks      — tests/test_combat_builds.py
    #   _try_phoenix_revive     — tests/test_phoenix_revive.py
    #   _apply_passive_auras    — tests/test_am_skills.py
    #   _apply_stat_drain_aura  — tests/test_thon_thien_ma_the.py

    def _run_on_hit_procs(self, actor: Combatant, target: Combatant, is_crit: bool) -> None:
        run_on_hit_procs(self, actor, target, is_crit)

    def _apply_reactive_damage(self, actor: Combatant, target: Combatant, dmg: int) -> None:
        apply_reactive_damage(self, actor, target, dmg)

    def _apply_skill_effects(
        self, skill_data: dict, actor: Combatant, target: Combatant, hit: bool,
    ) -> None:
        apply_skill_effects(self, skill_data, actor, target, hit)

    def _burst_shield(self, actor: Combatant, target: Combatant, skill_data: dict) -> None:
        burst_shield(self, actor, target, skill_data)

    def _burst_mana_stacks(self, actor: Combatant, target: Combatant, skill_data: dict) -> None:
        burst_mana_stacks(self, actor, target, skill_data)

    def _try_phoenix_revive(self, dying: Combatant) -> bool:
        return self._try_revive(dying)

    def _apply_passive_auras(self, holder: Combatant, opponent: Combatant) -> None:
        start_aura.apply_passive_auras(self, holder, opponent)

    def _apply_stat_drain_aura(self, holder: Combatant, target: Combatant) -> None:
        start_aura.apply_stat_drain_aura(self, holder, target)
