"""Pre-turn aura hooks.

Each submodule in this package owns one of the six pre-turn aura
behaviors that used to live as inline ``_refresh_*`` / ``_try_*`` /
``_process_*`` methods on ``CombatSession``. Importing this package
triggers the ``@register_hook`` decorators at the top of each module,
installing the hooks on the global :data:`src.game.systems.combat.hooks.registry`.

**Priority table** — pinned to match the comment-encoded order from the
legacy ``_take_turn`` body so existing tests stay green:

  10  kinh_hoa   — Kính Hoa Thủy Nguyệt (debuff transfer)
  20  luu_ly     — Lưu Ly Tịnh Hỏa (per-fire-DoT cleanse aura)
  30  luu_tinh   — Lưu Tinh Cản Nguyệt (dynamic evasion/spd refresh)
  35  xuan_thu   — Xuân Thu Nhất Bút (Spring-Autumn rotation refresh)
  40  lieu_nhu   — Liễu Nhứ Tùy Phong (Mộc-DoT drift refresh)
  50  bo_bo      — Bộ Bộ Sinh Liên (heal-taken → mobility refresh)
  60  phu_dao    — Phù Dao Trực Thượng (altitude tier bump)

Adding a new pre-turn aura means dropping a new module here and picking
a priority that places it correctly in the chain — no edits to session.py.
"""
from . import bo_bo, kinh_hoa, lieu_nhu, luu_ly, luu_tinh, phu_dao, xuan_thu  # noqa: F401

__all__ = ["bo_bo", "kinh_hoa", "lieu_nhu", "luu_ly", "luu_tinh", "phu_dao", "xuan_thu"]
