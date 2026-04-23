"""Combat effects registry — 23 buffs + 19 debuffs/CC.

Provides:
  EFFECTS                 dict[key → EffectMeta]
  get_combat_modifiers    sum all active effect stat modifiers on a combatant
  get_periodic_damage     list of (effect_key, damage) for active DoTs
  check_cc_skip_turn      returns CC key if combatant should skip turn, else None
  check_prevents_skills   returns CC key if combatant cannot use skills, else None
  default_duration        default turn duration for an effect
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from src.game.constants.effects import EffectKey

if TYPE_CHECKING:
    from src.game.systems.combatant import Combatant


class EffectKind(StrEnum):
    BUFF = "buff"
    DEBUFF = "debuff"
    CC = "cc"


@dataclass(frozen=True)
class EffectMeta:
    key: str
    vi: str                          # Vietnamese name
    en: str                          # English name
    kind: EffectKind
    description_vi: str
    # Stat modifiers while effect is active (see stat key docs below)
    # Positive = bonus, negative = penalty. Applied to the combatant who holds the effect.
    # Stat keys: final_dmg_bonus, final_dmg_reduce, crit_rating, crit_dmg_rating,
    #            evasion_rating, crit_res_rating, spd_pct, hp_regen_pct, res_all
    stat_bonus: dict[str, float] = field(default_factory=dict)
    # Periodic damage per turn as fraction of holder's hp_max (DoT effects)
    dot_pct: float = 0.0
    # Element of the DoT damage — holder's resistance to this element reduces DoT damage
    dot_element: str | None = None
    # Whether this CC effect causes the holder to skip their turn (deterministic)
    skips_turn: bool = False
    # Whether this effect prevents skill usage (silence / interrupt)
    prevents_skills: bool = False
    # Display emoji
    emoji: str = "✨"


# ── Buff definitions (23) ─────────────────────────────────────────────────────

_BUFFS: list[EffectMeta] = [
    EffectMeta(
        key="BuffKiemKhi",
        vi="Kiếm Khí", en="Sword Qi",
        kind=EffectKind.BUFF,
        description_vi="Tụ kiếm khí, tăng sát thương và tỉ lệ bạo kích.",
        stat_bonus={"final_dmg_bonus": 0.15, "crit_rating": 150},
        emoji="⚔️",
    ),
    EffectMeta(
        key="BuffKiemY",
        vi="Kiếm Ý", en="Sword Intent",
        kind=EffectKind.BUFF,
        description_vi="Kiếm ý sung mãn, tăng mạnh sát thương.",
        stat_bonus={"final_dmg_bonus": 0.25},
        emoji="🗡️",
    ),
    EffectMeta(
        key="BuffVoNgaKiemTam",
        vi="Vô Ngã Kiếm Tâm", en="Selfless Sword Heart",
        kind=EffectKind.BUFF,
        description_vi="Đạt cảnh giới vô ngã, tăng tối đa sát thương kiếm.",
        stat_bonus={"final_dmg_bonus": 0.40},
        emoji="✨",
    ),
    EffectMeta(
        key="BuffNhietTinh",
        vi="Nhiệt Tình", en="Blazing Passion",
        kind=EffectKind.BUFF,
        description_vi="Chiến ý bùng cháy, tăng công kích và sát thương bạo kích.",
        stat_bonus={"final_dmg_bonus": 0.20, "crit_dmg_rating": 200},
        emoji="🔥",
    ),
    EffectMeta(
        key="BuffHoaThan",
        vi="Hỏa Thần Giáng Lâm", en="Fire God Descent",
        kind=EffectKind.BUFF,
        description_vi="Hỏa thần tạm hạ phàm, tăng sát thương và xác suất thiêu đốt.",
        stat_bonus={"final_dmg_bonus": 0.20},
        emoji="🔥",
    ),
    EffectMeta(
        key="BuffLietDiem",
        vi="Liệt Diệm Hộ Thể", en="Blazing Flame Guard",
        kind=EffectKind.BUFF,
        description_vi="Lửa thiêu đốt bảo vệ cơ thể, giảm sát thương nhận vào.",
        stat_bonus={"final_dmg_reduce": 0.10},
        emoji="🛡️",
    ),
    EffectMeta(
        key="BuffBangGiap",
        vi="Băng Giáp", en="Ice Armor",
        kind=EffectKind.BUFF,
        description_vi="Băng giáp bao phủ, giảm đáng kể sát thương nhận vào.",
        stat_bonus={"final_dmg_reduce": 0.20},
        emoji="🧊",
    ),
    EffectMeta(
        key="BuffThuyKinh",
        vi="Thủy Kính Thân", en="Water Mirror Body",
        kind=EffectKind.BUFF,
        description_vi="Thân như gương nước, phản chiếu và giảm sát thương.",
        stat_bonus={"final_dmg_reduce": 0.15},
        emoji="💧",
    ),
    EffectMeta(
        key="BuffHanKhi",
        vi="Hàn Khí Tỏa Thân", en="Cold Aura",
        kind=EffectKind.BUFF,
        description_vi="Hàn khí tỏa ra, làm chậm kẻ địch và giảm sát thương nhận.",
        stat_bonus={"final_dmg_reduce": 0.10},
        emoji="❄️",
    ),
    EffectMeta(
        key="BuffLoiThan",
        vi="Lôi Thần Giáng", en="Thunder God Strike",
        kind=EffectKind.BUFF,
        description_vi="Lôi thần gia hộ, mỗi đòn có thể gây tê liệt.",
        stat_bonus={"crit_rating": 100},
        emoji="⚡",
    ),
    EffectMeta(
        key="BuffTocLoi",
        vi="Tốc Lôi", en="Speed Lightning",
        kind=EffectKind.BUFF,
        description_vi="Tốc độ như sét đánh, tăng mạnh tốc độ hành động.",
        stat_bonus={"spd_pct": 0.50},
        emoji="⚡",
    ),
    EffectMeta(
        key="BuffNguPhong",
        vi="Ngự Phong", en="Wind Mastery",
        kind=EffectKind.BUFF,
        description_vi="Điều khiển gió, tăng cao khả năng né tránh.",
        stat_bonus={"evasion_rating": 200},
        emoji="🌬️",
    ),
    EffectMeta(
        key="BuffPhongVu",
        vi="Phong Vũ Tương Hòa", en="Wind Rain Harmony",
        kind=EffectKind.BUFF,
        description_vi="Phong vũ hòa quyện, tăng cả né tránh lẫn bạo kích.",
        stat_bonus={"evasion_rating": 100, "crit_rating": 100},
        emoji="🌧️",
    ),
    EffectMeta(
        key="BuffSinhCo",
        vi="Sinh Cơ Sung Mãn", en="Vitality Overflow",
        kind=EffectKind.BUFF,
        description_vi="Sinh cơ dồi dào, hồi phục HP mỗi lượt.",
        stat_bonus={"hp_regen_pct": 0.05},
        emoji="💚",
    ),
    EffectMeta(
        key="BuffCanCo",
        vi="Căn Cơ Bất Động", en="Immovable Foundation",
        kind=EffectKind.BUFF,
        description_vi="Căn cơ vững chắc như núi, giảm sát thương nhận vào.",
        stat_bonus={"final_dmg_reduce": 0.15},
        emoji="🪨",
    ),
    EffectMeta(
        key="BuffKimCuong",
        vi="Kim Cương Thể", en="Diamond Body",
        kind=EffectKind.BUFF,
        description_vi="Thân như kim cương, giảm mạnh sát thương nhận vào.",
        stat_bonus={"final_dmg_reduce": 0.20},
        emoji="💎",
    ),
    EffectMeta(
        key="BuffHoangKim",
        vi="Hoàng Kim Hộ", en="Golden Guard",
        kind=EffectKind.BUFF,
        description_vi="Khiên vàng bảo hộ, giảm sát thương và tăng kháng cự toàn nguyên tố.",
        stat_bonus={"final_dmg_reduce": 0.15, "res_all": 0.10},
        emoji="🟡",
    ),
    EffectMeta(
        key="BuffDaiDia",
        vi="Đại Địa Thần Hộ", en="Great Earth Divine Guard",
        kind=EffectKind.BUFF,
        description_vi="Đại địa thần hộ trì, giảm tối đa sát thương nhận vào.",
        stat_bonus={"final_dmg_reduce": 0.25},
        emoji="🌍",
    ),
    EffectMeta(
        key="BuffTrongTo",
        vi="Trọng Thổ", en="Heavy Earth",
        kind=EffectKind.BUFF,
        description_vi="Thổ khí nặng trịch, tăng phòng thủ và kháng cự.",
        stat_bonus={"final_dmg_reduce": 0.10, "res_all": 0.05},
        emoji="🪨",
    ),
    EffectMeta(
        key="BuffBatTu",
        vi="Bất Tử", en="Immortality",
        kind=EffectKind.BUFF,
        description_vi="Nhận lấy bất tử nhất thời, ngăn cái chết một lần.",
        stat_bonus={},
        emoji="💫",
    ),
    EffectMeta(
        key="BuffTangToc",
        vi="Tăng Tốc", en="Haste",
        kind=EffectKind.BUFF,
        description_vi="Tốc độ hành động tăng mạnh.",
        stat_bonus={"spd_pct": 0.30},
        emoji="⚡",
    ),
    EffectMeta(
        key="BuffHoPhap",
        vi="Hộ Pháp", en="Dharma Protection",
        kind=EffectKind.BUFF,
        description_vi="Hộ pháp bảo vệ toàn diện, giảm sát thương và tăng kháng bạo kích.",
        stat_bonus={"final_dmg_reduce": 0.25, "crit_res_rating": 100},
        emoji="🔮",
    ),
    EffectMeta(
        key="BuffHuKhong",
        vi="Hư Không Thân", en="Void Body",
        kind=EffectKind.BUFF,
        description_vi="Thân nhập hư không, né tránh cực cao.",
        stat_bonus={"evasion_rating": 250},
        emoji="👻",
    ),
]

# ── Debuff / CC definitions (19) ─────────────────────────────────────────────

_DEBUFFS_CC: list[EffectMeta] = [
    EffectMeta(
        key="DebuffThieuDot",
        vi="Thiêu Đốt", en="Burning",
        kind=EffectKind.DEBUFF,
        description_vi="Lửa đốt cháy cơ thể, gây sát thương mỗi lượt.",
        dot_pct=0.04,
        dot_element="hoa",
        emoji="🔥",
    ),
    EffectMeta(
        key="DebuffTeLiet",
        vi="Tê Liệt", en="Paralysis",
        kind=EffectKind.CC,
        description_vi="Cơ thể tê liệt, 50% mất lượt.",
        emoji="⚡",
    ),
    EffectMeta(
        key="DebuffDotChay",
        vi="Đốt Cháy Nội Tạng", en="Internal Burning",
        kind=EffectKind.DEBUFF,
        description_vi="Lửa đốt nội tạng, gây sát thương nặng mỗi lượt.",
        dot_pct=0.08,
        dot_element="hoa",
        emoji="💥",
    ),
    EffectMeta(
        key="DebuffDocTo",
        vi="Độc Tố", en="Poison",
        kind=EffectKind.DEBUFF,
        description_vi="Độc tố ăn mòn cơ thể, gây sát thương mỗi lượt.",
        dot_pct=0.04,
        dot_element="moc",
        emoji="☠️",
    ),
    EffectMeta(
        key="DebuffBaoMon",
        vi="Bào Mòn", en="Corrosion",
        kind=EffectKind.DEBUFF,
        description_vi="Bào mòn kháng cự, giảm khả năng chịu đòn.",
        stat_bonus={"res_all": -0.05},
        emoji="🧪",
    ),
    EffectMeta(
        key="DebuffTroBuoc",
        vi="Trói Buộc", en="Bind",
        kind=EffectKind.DEBUFF,
        description_vi="Bị trói buộc, giảm tốc độ và không thể tháo chạy.",
        stat_bonus={"spd_pct": -0.50},
        emoji="⛓️",
    ),
    EffectMeta(
        key="DebuffLunDat",
        vi="Lún Đất", en="Quicksand",
        kind=EffectKind.DEBUFF,
        description_vi="Lún xuống đất, giảm mạnh tốc độ.",
        stat_bonus={"spd_pct": -0.30},
        emoji="🌱",
    ),
    EffectMeta(
        key="EffectNgungDong",
        vi="Ngưng Đọng", en="Stagnation",
        kind=EffectKind.DEBUFF,
        description_vi="Khí trường ngưng đọng, giảm tốc độ.",
        stat_bonus={"spd_pct": -0.25},
        emoji="💨",
    ),
    EffectMeta(
        key="DebuffLamCham",
        vi="Làm Chậm", en="Slow",
        kind=EffectKind.DEBUFF,
        description_vi="Tốc độ bị giảm.",
        stat_bonus={"spd_pct": -0.25},
        emoji="🐢",
    ),
    EffectMeta(
        key="DebuffDongBang",
        vi="Đóng Băng", en="Frozen",
        kind=EffectKind.CC,
        description_vi="Bị đông cứng, mất lượt hành động.",
        skips_turn=True,
        emoji="🧊",
    ),
    EffectMeta(
        key="DebuffChayMau",
        vi="Chảy Máu", en="Bleed",
        kind=EffectKind.DEBUFF,
        description_vi="Máu chảy không ngừng, gây sát thương mỗi lượt.",
        dot_pct=0.033,
        dot_element="kim",
        emoji="🩸",
    ),
    EffectMeta(
        key="DebuffPhaGiap",
        vi="Phá Giáp", en="Armor Break",
        kind=EffectKind.DEBUFF,
        description_vi="Giáp phòng thủ bị phá vỡ, giảm khả năng chịu đòn.",
        stat_bonus={"final_dmg_reduce": -0.15},
        emoji="🔨",
    ),
    EffectMeta(
        key="DebuffXeRach",
        vi="Xé Rách", en="Lacerate",
        kind=EffectKind.DEBUFF,
        description_vi="Xé nát kháng cự, giảm mạnh kháng nguyên tố.",
        stat_bonus={"res_all": -0.08},
        emoji="🗡️",
    ),
    EffectMeta(
        key="DebuffCuonBay",
        vi="Cuốn Bay", en="Knock Up",
        kind=EffectKind.CC,
        description_vi="Bị cuốn bay lên, mất lượt hành động.",
        skips_turn=True,
        emoji="💨",
    ),
    EffectMeta(
        key="DebuffCatDut",
        vi="Cắt Đứt Linh Khí", en="Qi Severance",
        kind=EffectKind.DEBUFF,
        description_vi="Linh khí bị cắt đứt, giảm hiệu quả hồi sinh lực.",
        stat_bonus={"hp_regen_pct": -0.03},
        emoji="✂️",
    ),
    EffectMeta(
        key="CCMuted",
        vi="Câm Lặng", en="Silence",
        kind=EffectKind.CC,
        description_vi="Bị câm lặng, không thể sử dụng kỹ năng.",
        prevents_skills=True,
        emoji="🔇",
    ),
    EffectMeta(
        key="CCStun",
        vi="Choáng", en="Stun",
        kind=EffectKind.CC,
        description_vi="Bị choáng, mất lượt hành động.",
        skips_turn=True,
        emoji="💫",
    ),
    EffectMeta(
        key="CCInterrupt",
        vi="Ngắt Kỹ Năng", en="Interrupt",
        kind=EffectKind.CC,
        description_vi="Kỹ năng bị ngắt, lượt này không thể sử dụng kỹ năng.",
        prevents_skills=True,
        emoji="⛔",
    ),
    EffectMeta(
        key="CCLockBreak",
        vi="Khóa Đột Phá", en="Breakthrough Lock",
        kind=EffectKind.CC,
        description_vi="Đột phá bị khóa bởi trạng thái khống chế.",
        emoji="🔒",
    ),
    EffectMeta(
        key="DebuffSetDanh",
        vi="Sét Đánh", en="Lightning Strike",
        kind=EffectKind.DEBUFF,
        description_vi="Bị sét đánh, chịu thêm sát thương sét mỗi lượt.",
        dot_pct=0.05,
        dot_element="loi",
        emoji="⚡",
    ),
]

# ── Build registry ────────────────────────────────────────────────────────────

EFFECTS: dict[str, EffectMeta] = {m.key: m for m in _BUFFS + _DEBUFFS_CC}

# ── Default durations ─────────────────────────────────────────────────────────

_DEFAULT_DURATIONS: dict[str, int] = {
    # Buffs — typically 3–4 turns
    EffectKey.BUFF_KIEM_KHI: 3, EffectKey.BUFF_KIEM_Y: 4, EffectKey.BUFF_VO_NGA_KIEM_TAM: 3,
    EffectKey.BUFF_NHIET_TINH: 3, EffectKey.BUFF_HOA_THAN: 3, EffectKey.BUFF_LIET_DIEM: 3,
    EffectKey.BUFF_BANG_GIAP: 4, EffectKey.BUFF_THUY_KINH: 3, EffectKey.BUFF_HAN_KHI: 3,
    EffectKey.BUFF_LOI_THAN: 3, EffectKey.BUFF_TOC_LOI: 2, EffectKey.BUFF_NGU_PHONG: 3,
    EffectKey.BUFF_PHONG_VU: 3, EffectKey.BUFF_SINH_CO: 4, EffectKey.BUFF_CAN_CO: 3,
    EffectKey.BUFF_KIM_CUONG: 3, EffectKey.BUFF_HOANG_KIM: 3, EffectKey.BUFF_DAI_DIA: 3,
    EffectKey.BUFF_TRONG_TO: 4, EffectKey.BUFF_BAT_TU: 1, EffectKey.BUFF_TANG_TOC: 3,
    EffectKey.BUFF_HO_PHAP: 4, EffectKey.BUFF_HU_KHONG: 2,
    # Debuffs — typically 2–3 turns
    EffectKey.DEBUFF_THIEU_DOT: 3, EffectKey.DEBUFF_TE_LIET: 2, EffectKey.DEBUFF_DOT_CHAY: 3,
    EffectKey.DEBUFF_DOC_TO: 3, EffectKey.DEBUFF_BAO_MON: 3, EffectKey.DEBUFF_TRO_BUOC: 2,
    EffectKey.DEBUFF_LUN_DAT: 2, EffectKey.EFFECT_NGUNG_DONG: 2, EffectKey.DEBUFF_LAM_CHAM: 2,
    EffectKey.DEBUFF_DONG_BANG: 2, EffectKey.DEBUFF_CHAY_MAU: 3, EffectKey.DEBUFF_PHA_GIAP: 3,
    EffectKey.DEBUFF_XE_RACH: 2, EffectKey.DEBUFF_CUON_BAY: 1, EffectKey.DEBUFF_CAT_DUT: 3,
    EffectKey.CC_MUTED: 2, EffectKey.CC_STUN: 1, EffectKey.CC_INTERRUPT: 1,
    EffectKey.CC_LOCK_BREAK: 3, EffectKey.DEBUFF_SET_DANH: 2,
}


def default_duration(effect_key: str) -> int:
    """Return the default turn duration for an effect."""
    return _DEFAULT_DURATIONS.get(effect_key, 3)


# ── Stat computation helpers ──────────────────────────────────────────────────

def get_combat_modifiers(combatant: "Combatant") -> dict[str, float]:
    """Aggregate all active effect stat bonuses/penalties on a combatant.

    Returns a dict with signed float values per stat key:
      final_dmg_bonus, final_dmg_reduce, crit_rating, crit_dmg_rating,
      evasion_rating, crit_res_rating, spd_pct, hp_regen_pct, res_all
    """
    result: dict[str, float] = {}
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if not meta:
            continue
        for stat, val in meta.stat_bonus.items():
            result[stat] = result.get(stat, 0.0) + val
    return result


def get_periodic_damage(combatant: "Combatant") -> list[tuple[str, int]]:
    """Return list of (effect_key, damage) for all active DoT effects.

    Damage is computed as dot_pct × hp_max, floored at 1.
    Poison is skipped if the combatant has poison_immunity.
    If the DoT has a dot_element, the holder's resistance to that element reduces the damage
    by the same flat amount used in the direct-damage pipeline.
    """
    results: list[tuple[str, int]] = []
    for effect_key in list(combatant.effects.keys()):
        meta = EFFECTS.get(effect_key)
        if not meta or meta.dot_pct <= 0:
            continue
        if effect_key == EffectKey.DEBUFF_DOC_TO and combatant.poison_immunity:
            continue
        dmg = max(1, int(combatant.hp_max * meta.dot_pct))
        # Apply holder's elemental resistance to reduce DoT damage (same flat formula as direct hits)
        if meta.dot_element:
            res_pct = max(0.0, min(0.75, combatant.resistances.get(meta.dot_element, 0.0)))
            dmg = max(1, int(dmg * (1.0 - res_pct)))
        results.append((effect_key, dmg))
    return results


def check_cc_skip_turn(
    combatant: "Combatant", rng: random.Random
) -> str | None:
    """Return the CC effect key that causes the combatant to skip their turn.

    Returns None if the combatant can act normally.
    DebuffTeLiet (paralysis) has a 50% chance to skip.
    All other skips_turn effects are deterministic.
    """
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if not meta:
            continue
        if meta.skips_turn:
            return effect_key
        if effect_key == EffectKey.DEBUFF_TE_LIET and rng.random() < 0.50:
            return effect_key
    return None


def check_prevents_skills(combatant: "Combatant") -> str | None:
    """Return the CC effect key that prevents skill use, or None."""
    for effect_key in combatant.effects:
        meta = EFFECTS.get(effect_key)
        if meta and meta.prevents_skills:
            return effect_key
    return None


def format_active_effects(combatant: "Combatant") -> str:
    """Format active effects for display in Discord embeds."""
    if not combatant.effects:
        return "—"
    parts: list[str] = []
    for key, turns in combatant.effects.items():
        meta = EFFECTS.get(key)
        name = meta.vi if meta else key
        emoji = meta.emoji if meta else "❓"
        parts.append(f"{emoji}{name}({turns}t)")
    return " ".join(parts)


# Linh Căn combat procs have moved to src.game.engine.linh_can_effects
# (per-element modules + package-level orchestrators). Combat imports them
# directly from there — nothing in this file references them anymore.
