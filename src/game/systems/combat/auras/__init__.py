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
  40  thai_bach  — Thái Bạch Canh Kim (every-N-turn guaranteed-crit arm)
  40  huyen_am   — Huyền Âm Thiên Ma (every-N-turn auto-Nhập-Ma)
  50  bo_bo      — Bộ Bộ Sinh Liên (heal-taken → mobility refresh)
  60  phu_dao    — Phù Dao Trực Thượng (altitude tier bump)
   5  truong_xuan — Trường Xuân Linh Mộc (Undying Spring cooldown tick)
  34  cuong_phong — Cửu Thiên Cương Phong (L9 Cương Phong storm cadence)
  36  cuu_thien  — Cửu Thiên Huyền Lôi (L9 Thần Lôi Giáng Thế burst cadence)
  37  tieu_dao   — Tiêu Dao Thần (L9 Hóa Bằng / Hóa Côn form cadence)
  38  hoang_co   — Hoàng Cổ Thánh Thể (L6 crit-arm + L9 Saint Realm cadence)
  39  lietdiem   — Liệt Diễm Phần Thiên (L9 Hỏa Thần avatar cadence)
  12  tinh_quang — Tịnh Quang Hộ Pháp (L3 every-3-turn self-cleanse)

Adding a new pre-turn aura means dropping a new module here and picking
a priority that places it correctly in the chain — no edits to session.py.
"""
from . import (  # noqa: F401
    bo_bo, cuong_phong, cuu_thien, hoang_co, huyen_am, kinh_hoa, lietdiem,
    lieu_nhu, luu_ly, luu_tinh, phu_dao, thai_bach, tieu_dao, tinh_quang,
    truong_xuan, xuan_thu,
)

__all__ = [
    "bo_bo", "cuong_phong", "cuu_thien", "hoang_co", "huyen_am", "kinh_hoa",
    "lietdiem", "lieu_nhu", "luu_ly", "luu_tinh", "phu_dao", "thai_bach",
    "tieu_dao", "tinh_quang", "truong_xuan", "xuan_thu",
]
