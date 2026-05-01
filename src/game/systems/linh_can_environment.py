"""Environmental effects for Linh Căn Bí Cảnh dungeons.

Each element's bí cảnh has a signature environmental effect that mutates
the wave's enemy and/or applies persistent debuffs to the player. Effects
scale with the player's qi_realm — the bí cảnh's spirit-vein pressure
grows heavier the deeper the cultivator has progressed.

Strength formula:
    strength = base_strength * (1 + scale_per_realm * qi_realm)

Plumbing: ``apply_environmental_effect(...)`` is called from ``run_dungeon``
right after the wave's enemy combatant is built (and stat-graded). Mutates
the combatant + player in place; returns a one-line log entry that the
dungeon log shows at the top of the wave so the player knows what's
hitting them.
"""
from __future__ import annotations

from src.game.constants.effects import EffectKey
from src.game.systems.combatant import Combatant


# Effect handlers: each takes the player + enemy combatants, the scaled
# effect strength, and returns the human-readable log line that gets
# prepended to the wave log.
def _kim_khi_sat_phat(player: Combatant, enemy: Combatant, strength: float) -> str:
    # Kim cuồng bạo: nâng atk yêu thú và mở on-hit bleed proc.
    enemy.atk = int(enemy.atk * (1 + strength))
    enemy.bleed_on_hit_pct = min(1.0, enemy.bleed_on_hit_pct + strength)
    return (
        f"  ⚙️ **Kim Khí Sát Phạt** kích hoạt — yêu thú +{int(strength * 100)}% Công, "
        f"+{int(strength * 100)}% gây Chảy Máu khi đánh."
    )


def _van_moc_hoi_sinh(player: Combatant, enemy: Combatant, strength: float) -> str:
    # Sinh khí Mộc: yêu thú hồi % HP/lượt.
    enemy.hp_regen_pct += strength
    return (
        f"  🌿 **Vạn Mộc Hồi Sinh** lan tỏa — yêu thú hồi "
        f"{int(strength * 100)}% Sinh Lực mỗi lượt."
    )


def _bang_phong_dai_tran(player: Combatant, enemy: Combatant, strength: float) -> str:
    # Hàn khí: chậm người chơi, tăng tốc yêu thú.
    enemy.spd = int(enemy.spd * (1 + strength))
    player.spd = max(1, int(player.spd * (1 - strength)))
    return (
        f"  💧 **Băng Phong Đại Trận** đóng băng linh khí — bạn -{int(strength * 100)}% Tốc, "
        f"yêu thú +{int(strength * 100)}% Tốc."
    )


def _liet_diem_thieu_thien(player: Combatant, enemy: Combatant, strength: float) -> str:
    # Hỏa khí ngút trời: đốt người chơi nhiều turn, scaling theo realm.
    duration = max(2, int(strength * 2))
    player.apply_effect(EffectKey.DEBUFF_THIEU_DOT, duration)
    return (
        f"  🔥 **Liệt Diễm Thiêu Thiên** thiêu rụi không khí — bạn dính "
        f"Thiêu Đốt {duration} lượt."
    )


def _hau_tho_tran_ap(player: Combatant, enemy: Combatant, strength: float) -> str:
    # Thổ khí: yêu thú +def, người chơi -crit_rating.
    enemy.def_stat = int(enemy.def_stat * (1 + strength))
    crit_loss = int(player.crit_rating * strength)
    player.crit_rating = max(0, player.crit_rating - crit_loss)
    return (
        f"  🪨 **Hậu Thổ Trấn Áp** đè nén — yêu thú +{int(strength * 100)}% Phòng, "
        f"bạn -{crit_loss} Bạo Kích."
    )


def _cuong_phong_loan_vu(player: Combatant, enemy: Combatant, strength: float) -> str:
    # Phong loạn: yêu thú +né, người chơi -né.
    eva_gain = int(80 * strength)
    enemy.evasion_rating += eva_gain
    enemy.spd = int(enemy.spd * (1 + strength * 0.5))
    eva_loss = int(player.evasion_rating * strength)
    player.evasion_rating = max(0, player.evasion_rating - eva_loss)
    return (
        f"  🌪️ **Cuồng Phong Loạn Vũ** cuồng bạo — yêu thú +{eva_gain} Né, "
        f"+{int(strength * 50)}% Tốc; bạn -{eva_loss} Né."
    )


def _tu_loi_bao_loan(player: Combatant, enemy: Combatant, strength: float) -> str:
    # Lôi: yêu thú +crit, người chơi -mp_regen_pct.
    crit_gain = int(strength)
    enemy.crit_rating += crit_gain
    mp_loss = min(player.mp_regen_pct, strength * 0.001)  # base*0.001 = small mp regen drain
    # Simpler: fixed % drain capped by current value.
    mp_drain_pct = min(player.mp_regen_pct, 0.04)
    player.mp_regen_pct = max(0.0, player.mp_regen_pct - mp_drain_pct)
    return (
        f"  ⚡ **Tử Lôi Bạo Loạn** trấn không — yêu thú +{crit_gain} Bạo Kích, "
        f"bạn -{int(mp_drain_pct * 100)}% MP/lượt."
    )


def _thanh_quang_ap_bach(player: Combatant, enemy: Combatant, strength: float) -> str:
    # Quang: yêu thú miễn debuff, người chơi giảm heal_pct.
    enemy.debuff_immune_pct = min(1.0, enemy.debuff_immune_pct + strength)
    heal_loss = strength * 0.5
    player.heal_pct = max(-0.95, player.heal_pct - heal_loss)
    return (
        f"  ✨ **Thánh Quang Áp Bách** rọi sáng — yêu thú +{int(strength * 100)}% kháng debuff, "
        f"bạn -{int(heal_loss * 100)}% trị liệu."
    )


def _u_minh_thuc_hon(player: Combatant, enemy: Combatant, strength: float) -> str:
    # Ám: yêu thú soul_drain on hit, người chơi -hp_regen.
    enemy.soul_drain_on_hit_pct = min(1.0, enemy.soul_drain_on_hit_pct + strength)
    hp_loss = min(player.hp_regen_pct, strength * 0.5)
    player.hp_regen_pct = max(0.0, player.hp_regen_pct - hp_loss)
    return (
        f"  🌑 **U Minh Thực Hồn** nuốt linh hồn — yêu thú +{int(strength * 100)}% Hút Hồn, "
        f"bạn -{int(hp_loss * 100)}% HP/lượt."
    )


_HANDLERS: dict[str, callable] = {
    "kim_khi_sat_phat":      _kim_khi_sat_phat,
    "van_moc_hoi_sinh":      _van_moc_hoi_sinh,
    "bang_phong_dai_tran":   _bang_phong_dai_tran,
    "liet_diem_thieu_thien": _liet_diem_thieu_thien,
    "hau_tho_tran_ap":       _hau_tho_tran_ap,
    "cuong_phong_loan_vu":   _cuong_phong_loan_vu,
    "tu_loi_bao_loan":       _tu_loi_bao_loan,
    "thanh_quang_ap_bach":   _thanh_quang_ap_bach,
    "u_minh_thuc_hon":       _u_minh_thuc_hon,
}


def scaled_strength(effect_cfg: dict, qi_realm: int) -> float:
    """Compute realm-scaled effect strength.

    base_strength × (1 + scale_per_realm × qi_realm)
    qi_realm is clamped to [0, 8] so absurd inputs can't blow up multipliers.
    """
    base = float(effect_cfg.get("base_strength", 0.0))
    scale = float(effect_cfg.get("scale_per_realm", 0.0))
    realm = max(0, min(8, qi_realm))
    return base * (1 + scale * realm)


def apply_environmental_effect(
    effect_cfg: dict,
    player: Combatant,
    enemy: Combatant,
    qi_realm: int,
) -> str | None:
    """Apply the dungeon's environmental effect to this wave's combatants.

    Returns a one-line log entry to prepend, or None if the effect is
    unknown/missing. Caller (run_dungeon) appends it to the wave log so
    the player sees the bí cảnh's pressure at the start of every fight.
    """
    if not effect_cfg:
        return None
    handler = _HANDLERS.get(effect_cfg.get("key"))
    if handler is None:
        return None
    strength = scaled_strength(effect_cfg, qi_realm)
    log_line = handler(player, enemy, strength)
    return log_line
