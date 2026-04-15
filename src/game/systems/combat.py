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
    ENEMY_RANK_BASE_ATK, ENEMY_RANK_BASE_MATK, ENEMY_RANK_BASE_DEF, ENEMY_RANK_BASE_EVASION,
    ENEMY_SCALE_MAX, ENEMY_HP_SCALE_FACTOR, ENEMY_DMG_BONUS_SCALE, ENEMY_BASE_ELEM_RES,
    ENEMY_REALM_LEVEL_STAT_MULT,
    REALM_POWER_BONUS_PER_STAGE, BASE_MP_REGEN_PCT, MAX_FINAL_DMG_REDUCE,
)
from src.game.constants.effects import EffectKey
from src.game.engine.damage import calculate_damage, DamageResult, AttackStats, DefenseStats
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
from src.game.engine.linh_can_effects import am, hoa, kim, loi, phong, quang, tho, thuy
from src.game.models.character import Character
from src.game.systems.combatant import Combatant


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
    max_turns: int = 30
    log: list[str] = field(default_factory=list)

    def _effective_spd(self, combatant: Combatant) -> int:
        """Return effective speed after applying all active buff/debuff spd_pct modifiers."""
        mods = get_combat_modifiers(combatant)
        multiplier = 1.0 + mods.get("spd_pct", 0.0)
        return max(1, int(combatant.spd * multiplier))

    def step(self) -> tuple[list[str], Optional[CombatResult]]:
        """Process one full game turn (both combatants act once).

        Returns (new_log_lines, result_if_over).
        result is None while the fight is still ongoing.
        """
        if self.turn >= self.max_turns:
            r = CombatResult(
                reason=CombatEndReason.MAX_TURNS,
                turns=self.turn, log=self.log, loot=[], merit_gained=0, karma_gained=0,
            )
            return ([], r)

        start_idx = len(self.log)
        self.turn += 1
        self.log.append(f"\n**— Lượt {self.turn} —**")

        if self._effective_spd(self.player) >= self._effective_spd(self.enemy):
            self._take_turn(self.player, self.enemy)
            if not self.enemy.is_alive():
                r = self._victory()
                return (self.log[start_idx:], r)
            self._take_turn(self.enemy, self.player)
            if not self.player.is_alive():
                r = self._defeat()
                return (self.log[start_idx:], r)
        else:
            self._take_turn(self.enemy, self.player)
            if not self.player.is_alive():
                r = self._defeat()
                return (self.log[start_idx:], r)
            self._take_turn(self.player, self.enemy)
            if not self.enemy.is_alive():
                r = self._victory()
                return (self.log[start_idx:], r)

        self.player.tick_cooldowns()
        self.enemy.tick_cooldowns()
        self._process_periodic(self.player)
        self._process_periodic(self.enemy)

        if not self.player.is_alive():
            r = self._defeat()
            return (self.log[start_idx:], r)
        if not self.enemy.is_alive():
            r = self._victory()
            return (self.log[start_idx:], r)

        return (self.log[start_idx:], None)

    def run(self) -> CombatResult:
        self.log.append(f"⚔️ **{self.player.name}** vs **{self.enemy.name}**")

        while self.turn < self.max_turns:
            self.turn += 1
            self.log.append(f"\n**— Lượt {self.turn} —**")

            if self._effective_spd(self.player) >= self._effective_spd(self.enemy):
                self._take_turn(self.player, self.enemy)
                if not self.enemy.is_alive():
                    return self._victory()
                self._take_turn(self.enemy, self.player)
                if not self.player.is_alive():
                    return self._defeat()
            else:
                self._take_turn(self.enemy, self.player)
                if not self.player.is_alive():
                    return self._defeat()
                self._take_turn(self.player, self.enemy)
                if not self.enemy.is_alive():
                    return self._victory()

            self.player.tick_cooldowns()
            self.enemy.tick_cooldowns()
            self._process_periodic(self.player)
            self._process_periodic(self.enemy)

            if not self.player.is_alive():
                return self._defeat()
            if not self.enemy.is_alive():
                return self._victory()

        return CombatResult(
            reason=CombatEndReason.MAX_TURNS,
            turns=self.turn, log=self.log, loot=[], merit_gained=0, karma_gained=0,
        )

    def _take_turn(self, actor: Combatant, target: Combatant) -> None:
        # Pre-turn: Phong Linh Căn — target may fully dodge the incoming attack
        if phong.try_dodge(target, self.rng, self.log):
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
        quang.try_cleanse(actor, self.rng, self.log)

        skill_key = self._choose_skill(actor)
        if not skill_key:
            # No skill available (out of mana or all on cooldown) → fall back to auto-attack
            self.log.append(f"  💤 **{actor.name}** không đủ linh lực — tấn công cơ bản.")
            self._auto_attack(actor, target)
            return

        skill_data = registry.get_skill(skill_key)
        if not skill_data:
            return

        mp_cost = skill_data.get("mp_cost", 5)
        if actor.mp < mp_cost:
            self._auto_attack(actor, target)
            return

        actor.mp = max(0, actor.mp - mp_cost)
        base_dmg = skill_data.get("base_dmg", 0)

        # Compute effective actor stats (base + active buff modifiers)
        actor_mods = get_combat_modifiers(actor)
        effective_final_dmg_bonus = actor.final_dmg_bonus + actor_mods.get("final_dmg_bonus", 0.0)

        # Compute effective target stats (base + debuff/buff modifiers on target)
        target_mods = get_combat_modifiers(target)
        res_all_mod = int(target_mods.get("res_all", 0))
        effective_target_res = {
            elem: max(0, res + res_all_mod)
            for elem, res in target.resistances.items()
        }
        # Target's effective damage reduction (buffs increase it, debuffs like DebuffPhaGiap reduce it)
        effective_target_dr = min(
            MAX_FINAL_DMG_REDUCE,
            max(0.0, target.final_dmg_reduce + target_mods.get("final_dmg_reduce", 0.0)),
        )

        if base_dmg > 0:
            from src.game.models.skill import AttackType, Skill, SkillType
            skill_obj = Skill(
                key=skill_key,
                vi=skill_data.get("vi", ""),
                en=skill_data.get("en", ""),
                skill_type=SkillType(skill_data.get("type", "thien")),
                mp_cost=mp_cost,
                cooldown=skill_data.get("cooldown", 1),
                base_dmg=base_dmg,
                element=skill_data.get("element"),
                attack_type=AttackType(skill_data.get("attack_type", "magical")),
                atk_scale=float(skill_data.get("atk_scale", 1.0)),
            )
            attack_stats = AttackStats(
                crit_rating=actor.crit_rating + int(actor_mods.get("crit_rating", 0)),
                crit_dmg_rating=actor.crit_dmg_rating + int(actor_mods.get("crit_dmg_rating", 0)),
                final_dmg_bonus=effective_final_dmg_bonus,
                atk=actor.atk,
                matk=actor.matk,
            )
            defense_stats = DefenseStats(
                evasion_rating=target.evasion_rating + int(target_mods.get("evasion_rating", 0)),
                crit_res_rating=target.crit_res_rating + int(target_mods.get("crit_res_rating", 0)),
                def_stat=target.def_stat,
                resistances=effective_target_res,
            )
            # Pre-damage: Kim Linh Căn — may gain elemental penetration this hit
            pen_pct = kim.get_pen_pct(actor, self.rng, self.log)
            result = calculate_damage(skill_obj, attack_stats, defense_stats, self.rng, pen_pct)
            dmg = result.final
            crit_tag = " 💥BẠO KÍCH!" if result.is_crit else ""

            if result.is_evaded:
                self.log.append(
                    f"  🌀 **{actor.name}** dùng *{skill_data['vi']}* → **{target.name}** né tránh!"
                )
            else:
                # Apply target's effective damage reduction (buffs + debuff penalties combined)
                if effective_target_dr > 0:
                    dmg = int(dmg * (1.0 - effective_target_dr))
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
                self.log.append(
                    f"  ⚡ **{actor.name}** dùng *{skill_data['vi']}* → -{dmg:,} HP{crit_tag}"
                    f" | {target.name}: {target.hp:,}/{target.hp_max:,} HP"
                )

                # On-hit formation procs
                if actor.burn_on_hit_pct > 0 and self.rng.random() < actor.burn_on_hit_pct:
                    dur = default_duration(EffectKey.DEBUFF_THIEU_DOT)
                    target.apply_effect(EffectKey.DEBUFF_THIEU_DOT, dur)
                    self.log.append(f"    🔥 Thiêu Đốt kích hoạt!")
                if actor.slow_on_hit_pct > 0 and self.rng.random() < actor.slow_on_hit_pct:
                    dur = default_duration(EffectKey.DEBUFF_LAM_CHAM)
                    target.apply_effect(EffectKey.DEBUFF_LAM_CHAM, dur)
                    self.log.append(f"    🐢 Làm Chậm kích hoạt!")
                if result.is_crit and actor.paralysis_on_crit:
                    target.apply_effect(EffectKey.CC_STUN, default_duration(EffectKey.CC_STUN))
                    self.log.append(f"    ⚡ Tê Liệt khi Bạo Kích kích hoạt!")
                if actor.freeze_on_skill and self.rng.random() < 0.15:
                    dur = default_duration(EffectKey.DEBUFF_DONG_BANG)
                    target.apply_effect(EffectKey.DEBUFF_DONG_BANG, dur)
                    self.log.append(f"    🧊 Đông Băng kích hoạt!")

                # On-hit: Linh Căn procs
                if dmg > 0:
                    hoa.on_hit(actor, target, dmg, self.rng, self.log)
                    thuy.on_hit(actor, target, dmg, self.rng, self.log)
                    loi.on_hit(actor, target, dmg, result.is_crit, self.rng, self.log)
                    am.on_hit(actor, target, dmg, self.rng, self.log)

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
                # Instant HP heal (10% of max)
                actor_mods = get_combat_modifiers(actor)
                heal_mult = 1.0 + actor.heal_pct + actor_mods.get("hp_regen_pct", 0.0)
                heal = max(1, int(actor.hp_max * 0.10 * heal_mult))
                actor.hp = min(actor.hp_max, actor.hp + heal)
                self.log.append(f"    ❤️ +{heal:,} HP")

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
                    self._inflict_debuff(effect_key, meta, target)

    def _apply_skill_effects(
        self, skill_data: dict, actor: Combatant, target: Combatant, hit: bool
    ) -> None:
        """Apply all effect_keys from a skill's effects list to the appropriate target.

        For debuffs/CC: checks the skill's effect_chances dict (default 1.0) multiplied
        by (1 - target.debuff_immune_pct) to get the effective proc probability.
        """
        effect_chances: dict[str, float] = skill_data.get("effect_chances", {})
        for effect_key in skill_data.get("effects", []):
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
                    self._inflict_debuff(effect_key, meta, target)

    def _inflict_debuff(
        self, effect_key: str, meta: "EffectMeta", target: Combatant
    ) -> None:
        """Apply a debuff or CC to target, respecting immunities."""
        if effect_key == EffectKey.DEBUFF_DOC_TO and target.poison_immunity:
            self.log.append(f"    💚 **{target.name}** miễn dịch Độc Tố!")
            return
        dur = default_duration(effect_key)
        target.apply_effect(effect_key, dur)
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
        self.log.append(f"  👊 **{actor.name}** tấn công cơ bản → -{dmg} HP")

    def _choose_skill(self, actor: Combatant) -> Optional[str]:
        available = [
            sk for sk in actor.skill_keys
            if not actor.skill_on_cooldown(sk)
            and registry.get_skill(sk) is not None
            and actor.mp >= (registry.get_skill(sk) or {}).get("mp_cost", 999)
        ]
        if not available:
            return None
        return max(available, key=lambda sk: (registry.get_skill(sk) or {}).get("base_dmg", 0))

    def _process_periodic(self, combatant: Combatant) -> None:
        """Process all periodic effects (DoTs, HP regen, Linh Căn procs) at end of turn."""
        # DoT damage from all active debuffs (poison, burn, bleed, etc.)
        for effect_key, dot_dmg in get_periodic_damage(combatant):
            combatant.hp = max(0, combatant.hp - dot_dmg)
            meta = EFFECTS.get(effect_key)
            emoji = meta.emoji if meta else "💢"
            name = meta.vi if meta else effect_key
            self.log.append(f"  {emoji} **{combatant.name}** bị {name} -{dot_dmg} HP")

        # Periodic: Thổ Linh Căn — activate shield when HP is low
        tho.check_shield(combatant, self.log)

        # HP regen: base passive + buff contributions (BuffSinhCo, Mộc Hồi Xuân, etc.)
        if combatant.is_alive():
            mods = get_combat_modifiers(combatant)
            effective_regen_pct = combatant.hp_regen_pct + mods.get("hp_regen_pct", 0.0)
            if effective_regen_pct > 0:
                regen = max(1, int(combatant.hp_max * effective_regen_pct))
                combatant.hp = min(combatant.hp_max, combatant.hp + regen)
                self.log.append(f"  💚 **{combatant.name}** hồi sinh lực +{regen} HP")

            # MP regen: base passive per turn
            if combatant.mp_regen_pct > 0 and combatant.mp < combatant.mp_max:
                mp_regen = max(1, int(combatant.mp_max * combatant.mp_regen_pct))
                combatant.mp = min(combatant.mp_max, combatant.mp + mp_regen)
                self.log.append(f"  💙 **{combatant.name}** hồi linh lực +{mp_regen} MP")

        combatant.tick_effects()

    def _roll_loot(self) -> list[dict]:
        enemy_data = registry.get_enemy(self.enemy.key)
        if not enemy_data:
            return []
        # Use enemy-specific table if defined (special bosses), else fall back to zone table.
        loot_key = enemy_data.get("loot_table_key") or f"LootZone_{enemy_data.get('realm_level', 1)}"
        drop_table = registry.get_loot_table(loot_key)
        return roll_drops(drop_table, self.rng).merge()

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
) -> Combatant:
    """Build a Combatant from a Character dataclass, applying formation + constitution bonuses."""
    from src.game.systems.cultivation import (
        compute_hp_max, compute_mp_max,
        compute_atk, compute_matk, compute_def_stat,
        compute_formation_bonuses, compute_constitution_bonuses, merge_bonuses,
    )

    from src.game.constants.linh_can import compute_linh_can_bonuses

    form_bonuses = compute_formation_bonuses(char.active_formation, gem_count)
    const_bonuses = compute_constitution_bonuses(char.constitution_type)
    lc_bonuses = compute_linh_can_bonuses(char.linh_can)
    # Mộc Linh Căn: Hồi Xuân 100% — always heal 4% HP per turn
    if "moc" in char.linh_can:
        lc_bonuses["hp_regen_pct"] = lc_bonuses.get("hp_regen_pct", 0.0) + 0.04
    bonuses = merge_bonuses(form_bonuses, const_bonuses, lc_bonuses)

    hp_max = compute_hp_max(char, bonuses)
    mp_max = compute_mp_max(char, bonuses)
    atk = compute_atk(char, bonuses)
    matk = compute_matk(char, bonuses)
    def_stat = compute_def_stat(char, bonuses)

    # Realm power: +0.8% damage per total cultivation stage (max ~+194% at full 243 stages)
    total_stages = (
        char.body_realm * 9 + char.body_level
        + char.qi_realm * 9 + char.qi_level
        + char.formation_realm * 9 + char.formation_level
    )
    realm_power_bonus = total_stages * REALM_POWER_BONUS_PER_STAGE

    # Base resistances from character stats (9 elements — mirrors Linh Căn)
    resistances: dict[str, int] = {
        "kim":   char.stats.res_kim,
        "moc":   char.stats.res_moc,
        "thuy":  char.stats.res_thuy,
        "hoa":   char.stats.res_hoa,
        "tho":   char.stats.res_tho,
        "loi":   char.stats.res_loi,
        "phong": char.stats.res_phong,
        "quang": char.stats.res_quang,
        "am":    char.stats.res_am,
    }

    # Apply res_all (flat bonus to all elements)
    res_all = bonuses.get("res_all", 0)
    if res_all:
        for elem in resistances:
            resistances[elem] += res_all

    # Apply res_element (formation's own element bonus)
    form_elem = form_bonuses.get("_formation_element")
    res_elem = form_bonuses.get("res_element", 0)
    if form_elem and res_elem:
        resistances[form_elem] = resistances.get(form_elem, 0) + res_elem

    # Auto-inject formation skill if active and not already in skill list
    final_skill_keys = list(player_skill_keys)
    if char.active_formation:
        form_data = registry.get_formation(char.active_formation)
        if form_data:
            frm_skill = form_data.get("formation_skill_key")
            if frm_skill and frm_skill not in final_skill_keys:
                final_skill_keys.append(frm_skill)

    spd_base = char.stats.spd + bonuses.get("spd_bonus", 0)
    spd_final = round(spd_base * (1.0 + bonuses.get("spd_pct", 0.0)))

    combatant = Combatant(
        key="player",
        name=char.name,
        hp=char.hp_current if char.hp_current > 0 else hp_max,
        hp_max=hp_max,
        mp=char.mp_current if char.mp_current > 0 else mp_max,
        mp_max=mp_max,
        spd=spd_final,
        element=None,
        atk=atk,
        matk=matk,
        def_stat=def_stat,
        crit_rating=char.stats.crit_rating + bonuses.get("crit_rating", 0),
        crit_dmg_rating=char.stats.crit_dmg_rating + bonuses.get("crit_dmg_rating", 0),
        evasion_rating=char.stats.evasion_rating + bonuses.get("evasion_rating", 0),
        crit_res_rating=char.stats.crit_res_rating + bonuses.get("crit_res_rating", 0),
        final_dmg_bonus=char.stats.final_dmg_bonus + bonuses.get("final_dmg_bonus", 0.0) + realm_power_bonus,
        final_dmg_reduce=bonuses.get("final_dmg_reduce", 0.0),
        hp_regen_pct=bonuses.get("hp_regen_pct", 0.0),
        mp_regen_pct=BASE_MP_REGEN_PCT + bonuses.get("mp_regen_pct", 0.0),
        heal_pct=bonuses.get("heal_pct", 0.0),
        cooldown_reduce=char.stats.cooldown_reduce + bonuses.get("cooldown_reduce", 0.0),
        burn_on_hit_pct=bonuses.get("burn_on_hit_pct", 0.0),
        slow_on_hit_pct=bonuses.get("slow_on_hit_pct", 0.0),
        paralysis_on_crit=bonuses.get("paralysis_on_crit", False),
        freeze_on_skill=bonuses.get("freeze_on_skill", False),
        poison_immunity=bonuses.get("poison_immunity", False),
        resistances=resistances,
        skill_keys=final_skill_keys,
        linh_can=list(char.linh_can),
    )
    combatant.hp = min(combatant.hp, hp_max)
    combatant.mp = min(combatant.mp, mp_max)
    return combatant


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

    res: dict[str, int] = {}
    elem = enemy_data.get("element")
    if elem:
        res[elem] = int(ENEMY_BASE_ELEM_RES * realm_scale)

    return Combatant(
        key=enemy_key,
        name=enemy_data["vi"],
        hp=hp,
        hp_max=hp,
        mp=hp // 3,
        mp_max=hp // 3,
        spd=spd,
        element=elem,
        atk=atk,
        matk=matk,
        def_stat=def_stat,
        evasion_rating=evasion_rating,
        resistances=res,
        skill_keys=enemy_data.get("skill_keys", []),
        final_dmg_bonus=enemy_dmg_bonus,
    )
