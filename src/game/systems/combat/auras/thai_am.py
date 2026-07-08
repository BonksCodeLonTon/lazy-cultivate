"""Thái Âm Đạo Thể — Trảm Đạo cadence + Hải Thượng Minh Nguyệt (PRE_TURN).

Two mechanics ride one hook:

L6 Trảm Đạo Kiến Minh — every ``ta_tram_dao_interval`` acted turns: STRIP
(destroy) up to ``ta_tram_dao_strips`` random enemy buffs and stamp
DebuffTramDao (−10% atk/matk/def, 3t) through ``inflict_debuff`` so shrug /
immunity gates apply. The sheet's "nullify enemy linh-căn passive" is
unimplementable without a linh-căn-passive registry on enemies — the strip +
stat seal IS the trimmed mapping.

L9 Hải Thượng Minh Nguyệt — every acted turn: heal ``ta_moonlight_heal_pct``
of max HP (through ``session._apply_heal`` — anti-heal counters apply) and
roll ``ta_moonlight_freeze_chance`` to freeze the opponent (hard-CC immunity
respected via ``inflict_debuff``). The Kính Hoa resonance half lives in
``auras/kinh_hoa.py``.

Inert for every other build (both gates default 0).
"""
from __future__ import annotations

from src.game.engine.effects import EFFECTS, EffectKind

from ..context import TurnContext
from ..hooks import TurnPhase, register_hook


@register_hook(phase=TurnPhase.PRE_TURN, name="thai_am_moon", priority=41)
def _thai_am_moon(ctx: TurnContext) -> None:
    actor = ctx.actor
    target = ctx.target
    session = ctx.session

    # ── L9 moonlight — heal + freeze chance, every acted turn ────────────────
    if actor.ta_moonlight_heal_pct > 0 and actor.is_alive():
        healed = session._apply_heal(
            actor, int(actor.hp_max * actor.ta_moonlight_heal_pct)
        )
        if healed > 0:
            ctx.log.append(
                f"  🌕 **{actor.name}** Hải Thượng Minh Nguyệt → hồi {healed:,} HP"
            )
    if (
        actor.ta_moonlight_freeze_chance > 0
        and target.is_alive()
        and session.rng.random() < actor.ta_moonlight_freeze_chance
    ):
        from ..casting import inflict_debuff
        _db = EFFECTS.get("DebuffDongBang")
        if _db is not None:
            inflict_debuff(session, "DebuffDongBang", _db, target, actor=actor)

    # ── L6 Trảm Đạo cadence — strip + stat seal ──────────────────────────────
    if actor.ta_tram_dao_interval <= 0 or not target.is_alive():
        return
    if not actor.tick_cadence("ta_tram_dao_turn_counter", actor.ta_tram_dao_interval):
        return
    from ..casting import inflict_debuff
    ctx.log.append(f"  🌙 **{actor.name}** Trảm Đạo Kiến Minh — trảm pháp đoạt cơ!")
    for _ in range(max(0, actor.ta_tram_dao_strips)):
        _buffs = [
            k for k in list(target.effects)
            if (m := EFFECTS.get(k)) is not None and m.kind is EffectKind.BUFF
        ]
        if not _buffs:
            break
        _pick = session.rng.choice(_buffs)
        target.effects.pop(_pick, None)
        target.effect_overrides.pop(_pick, None)
        _pm = EFFECTS.get(_pick)
        ctx.log.append(
            f"    🌙 tước *{_pm.vi if _pm else _pick}* của **{target.name}**"
        )
    _seal = EFFECTS.get("DebuffTramDao")
    if _seal is not None:
        inflict_debuff(session, "DebuffTramDao", _seal, target, actor=actor)
