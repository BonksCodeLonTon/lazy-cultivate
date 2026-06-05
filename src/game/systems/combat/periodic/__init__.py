"""End-of-round periodic hooks.

Each submodule registers one or more PERIODIC hooks; the dispatcher walks
them in priority order, mirroring the chain that used to live inline in
``CombatSession._process_periodic``.

**Priority table** — pinned to the legacy order so existing tests stay
green:

  10  endure         — Cội Nguồn Bất Tận announcement
  15  endure         — Thánh Tuyền deferred-damage payout
  20  dots           — DoT loop + U Minh MP drain + Bất Diệt Hỏa Chủng
  25  thai_bach      — Bạch Kim Phong Vũ stack growth (opponent bleeding)
  25  chan_duong     — Hỏa Khí Tương Sinh burning crit-ramp (opponent burning)
  30  summons        — per-summon damage tick
  40  solar_wither   — Thái Dương Thần Quang aura
  45  solar_wither   — Khô Mộc Hấp Thu aura
  46  huyen_thuy     — Hồi Triều Nộ Hải tidal flood (reservoir discharge)
  47  truong_xuan    — Trường Xuân Hồi Nguyên debuff-count regen
  48  overheal       — Mộc Linh Cộng Sinh reservoir release (in overheal_reservoir.py)
  50  regen          — Thổ shield check + shield/HP/MP regen
  60  fortify        — Hào Quang Củng Cố stack + braced-turn decrement
  70  endure         — endure_remaining cooldown decrement
  80  expiry         — tick_effects + on-expire hooks (incl. aegis discharge)
  90  luc_duc        — Lục Dục Thiên Ma Vũ six-desires cycle

Adding a new periodic effect is one new module here plus picking a
priority that places it correctly in the chain — no edits to session.py.
"""
from . import (  # noqa: F401
    chan_duong,
    dots,
    endure,
    expiry,
    fortify,
    huyen_thuy,
    luc_duc,
    regen,
    solar_wither,
    summons,
    thai_bach,
    truong_xuan,
)

__all__ = [
    "chan_duong", "dots", "endure", "expiry", "fortify", "huyen_thuy",
    "luc_duc", "regen", "solar_wither", "summons", "thai_bach", "truong_xuan",
]
