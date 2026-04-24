"""Post-damage proc machinery.

Everything here fires AFTER a successful damaging hit:
  - chance-based on-hit debuff/stack procs (_ON_HIT_PROCS table)
  - reactive damage (Thủy reflect, Thổ thorn)
  - Âm soul-drain + stat-steal (with lazy ``_original`` snapshots)

Each function takes ``session`` explicitly so it can append to the log and
read the RNG without holding a class reference.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.game.constants.balance import (
    SOUL_DRAIN_CAP_PCT, SOUL_DRAIN_PER_PROC_PCT, SOUL_DRAIN_SELF_GAIN_PCT,
    STAT_STEAL_CAP_PCT, STAT_STEAL_PER_PROC_PCT,
)
from src.game.constants.effects import EffectKey
from src.game.engine.damage import colorize_damage
from src.game.engine.effects import EFFECTS, default_duration
from src.game.systems.combatant import Combatant

from .helpers import _ON_HIT_PROCS, _propagate_stack_build

if TYPE_CHECKING:
    from .session import CombatSession


def snapshot_original(combatant: Combatant, field: str, live_value: int) -> int:
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


def run_on_hit_procs(
    session: "CombatSession", actor: Combatant, target: Combatant, is_crit: bool
) -> None:
    """Roll each chance-based on-hit proc from _ON_HIT_PROCS plus special cases.

    Special cases not driven by the table:
      - stun_on_hit_pct respects target's hard-CC immunity
      - paralysis_on_crit fires only on crit, always lands
      - freeze_on_skill uses a fixed 0.15 chance independent of any attr
    """
    for spec in _ON_HIT_PROCS:
        chance = getattr(actor, spec["chance_attr"], 0.0)
        if chance <= 0 or session.rng.random() >= chance:
            continue
        effect_key = spec["effect_key"]
        dur = default_duration(effect_key)
        target.apply_effect(effect_key, dur)
        stack_kind = spec.get("stack_kind")
        if stack_kind:
            _propagate_stack_build(actor, target, stack_kind)
            getattr(target, spec["stack_add"])(1)
            session.log.append(spec["log_fmt"].format(
                stacks=getattr(target, spec["stacks_attr"]),
                cap=getattr(target, spec["cap_attr"]),
            ))
        else:
            session.log.append(spec["log_fmt"])

    # Thổ build: stun_on_hit — flat chance, respects hard-CC immunity
    if actor.stun_on_hit_pct > 0 and session.rng.random() < actor.stun_on_hit_pct:
        if target.immune_hard_cc:
            session.log.append(f"    🛡️ **{target.name}** miễn dịch Choáng!")
        else:
            target.apply_effect(EffectKey.CC_STUN, default_duration(EffectKey.CC_STUN))
            session.log.append(f"    💫 Choáng kích hoạt!")
    if is_crit and actor.paralysis_on_crit:
        target.apply_effect(EffectKey.CC_STUN, default_duration(EffectKey.CC_STUN))
        session.log.append(f"    ⚡ Tê Liệt khi Bạo Kích kích hoạt!")
    # Quang build: silence_on_crit — crit-gated CCMuted application.
    # Respects the same hard-CC immunity used by stun_on_hit.
    if is_crit and actor.silence_on_crit_pct > 0 and session.rng.random() < actor.silence_on_crit_pct:
        if target.immune_hard_cc:
            session.log.append(f"    🛡️ **{target.name}** miễn dịch Câm Lặng!")
        else:
            target.apply_effect(EffectKey.CC_MUTED, default_duration(EffectKey.CC_MUTED))
            session.log.append(f"    ✨ Thánh Quang Chế Ngự — câm lặng kích hoạt!")
    if actor.freeze_on_skill and session.rng.random() < 0.15:
        dur = default_duration(EffectKey.DEBUFF_DONG_BANG)
        target.apply_effect(EffectKey.DEBUFF_DONG_BANG, dur)
        session.log.append(f"    🧊 Đông Băng kích hoạt!")
    # Âm build: Hồn Phệ — permanently drain target.hp_max, transfer half
    # to actor. Capped to avoid trivializing bosses.
    if actor.soul_drain_on_hit_pct > 0 and session.rng.random() < actor.soul_drain_on_hit_pct:
        apply_soul_drain(session, actor, target)
    # Âm build: Đạo Pháp Thôn Phệ — steal atk/matk/def from target.
    if actor.stat_steal_on_hit_pct > 0 and session.rng.random() < actor.stat_steal_on_hit_pct:
        apply_stat_steal(session, actor, target)
    # Aura-on-hit from active buffs — e.g. BuffHanKhi spreads Làm Chậm to
    # anyone the holder strikes. Iterate the actor's effects once and
    # fire any aura hooks their EffectMeta declares.
    for effect_key in list(actor.effects.keys()):
        aura_meta = EFFECTS.get(effect_key)
        if aura_meta is None or aura_meta.aura_on_hit is None:
            continue
        aura_effect, chance = aura_meta.aura_on_hit
        if session.rng.random() >= chance:
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
        if effective < 1.0 and session.rng.random() >= effective:
            continue
        target.apply_effect(aura_effect, default_duration(aura_effect))
        session.log.append(
            f"    {aura_meta.emoji} Hào quang **{aura_meta.vi}** "
            f"→ {aura_dst_meta.emoji} {aura_dst_meta.vi}"
        )


def apply_soul_drain(
    session: "CombatSession", actor: Combatant, target: Combatant
) -> None:
    """Permanently shrink target.hp_max; transfer part to actor.

    No-op once the cap (SOUL_DRAIN_CAP_PCT of target's starting hp_max)
    is reached. The drained amount also removes current HP in parallel
    so the immediate fight impact matches the long-term one.

    World bosses are immune: mutating ``hp_max`` on a shared-pool entity
    would double-credit the attacker via the local-sim damage calc.
    """
    if target.is_world_boss:
        session.log.append(
            f"    🌑 **Hồn Phệ** vô hiệu — **{target.name}** là Boss Thế Giới, "
            f"HP Max không thể bị xói mòn."
        )
        return
    start = snapshot_original(target, "hp_max_original", target.hp_max)
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
    session.log.append(
        f"    🌑 **Hồn Phệ** — **{target.name}** -{drain:,} HP Max "
        f"(tổng {target.hp_max_drained:,}/{cap:,}) → **{actor.name}** +{gain:,} HP Max"
    )


def apply_stat_steal(
    session: "CombatSession", actor: Combatant, target: Combatant
) -> None:
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
        start = snapshot_original(target, snap_attr, getattr(target, live_attr))
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
        session.log.append(
            f"    🩶 **Đạo Pháp Thôn Phệ** — **{actor.name}** cướp "
            f"{' / '.join(stolen_parts)} từ **{target.name}**"
        )


def apply_reactive_damage(
    session: "CombatSession", actor: Combatant, target: Combatant, dmg: int
) -> None:
    """Thủy reflect + Thổ thorn — defender-triggered retaliation."""
    if target.reflect_pct > 0:
        apply_reflect(session, actor, target, dmg)
    if target.thorn_pct > 0 and actor.is_alive():
        apply_thorn(session, actor, target, dmg)


def apply_reflect(
    session: "CombatSession", attacker: Combatant, defender: Combatant, dmg: int
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
    session.log.append(
        f"    🪞 **{defender.name}** phản đòn → **{attacker.name}** {reflect_tag}"
    )
    if not defender.reflect_applies_effects:
        return

    # Mirror the defender's on-hit effect flags back at the attacker.
    if defender.freeze_on_skill and session.rng.random() < 0.25:
        dur = default_duration(EffectKey.DEBUFF_DONG_BANG)
        attacker.apply_effect(EffectKey.DEBUFF_DONG_BANG, dur)
        session.log.append(f"    🧊 Mirror Đóng Băng — **{attacker.name}** bị đông cứng!")
    if defender.slow_on_hit_pct > 0 and session.rng.random() < defender.slow_on_hit_pct:
        dur = default_duration(EffectKey.DEBUFF_LAM_CHAM)
        attacker.apply_effect(EffectKey.DEBUFF_LAM_CHAM, dur)
        session.log.append(f"    🐢 Mirror Làm Chậm — **{attacker.name}**")
    if defender.burn_on_hit_pct > 0 and session.rng.random() < defender.burn_on_hit_pct:
        dur = default_duration(EffectKey.DEBUFF_THIEU_DOT)
        attacker.apply_effect(EffectKey.DEBUFF_THIEU_DOT, dur)
        _propagate_stack_build(defender, attacker, "burn")
        attacker.add_burn_stack(1)
        session.log.append(f"    🔥 Mirror Thiêu Đốt — **{attacker.name}**")
    if defender.bleed_on_hit_pct > 0 and session.rng.random() < defender.bleed_on_hit_pct:
        dur = default_duration(EffectKey.DEBUFF_CHAY_MAU)
        attacker.apply_effect(EffectKey.DEBUFF_CHAY_MAU, dur)
        _propagate_stack_build(defender, attacker, "bleed")
        attacker.add_bleed_stack(1)
        session.log.append(f"    🩸 Mirror Chảy Máu — **{attacker.name}**")


def apply_thorn(
    session: "CombatSession", attacker: Combatant, defender: Combatant, dmg: int
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
    session.log.append(
        f"    🌵 Gai Phản Đòn → **{attacker.name}** {thorn_tag}"
    )
