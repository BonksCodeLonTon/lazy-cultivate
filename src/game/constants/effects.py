"""Effect key constants — single source of truth for all buff/debuff/CC keys.

Using StrEnum so values ARE strings: EffectKey.CC_STUN == "CCStun" is True.
This means they drop in anywhere a raw string key was used before.
"""
from __future__ import annotations

from enum import StrEnum


class EffectKey(StrEnum):
    # ── Buffs ────────────────────────────────────────────────────────────────
    BUFF_KIEM_KHI        = "BuffKiemKhi"
    BUFF_KIEM_Y          = "BuffKiemY"
    BUFF_VO_NGA_KIEM_TAM = "BuffVoNgaKiemTam"
    BUFF_NHIET_TINH      = "BuffNhietTinh"
    BUFF_HOA_THAN        = "BuffHoaThan"
    BUFF_LIET_DIEM       = "BuffLietDiem"
    BUFF_BANG_GIAP       = "BuffBangGiap"
    BUFF_THUY_KINH       = "BuffThuyKinh"
    BUFF_HAN_KHI         = "BuffHanKhi"
    BUFF_LOI_THAN        = "BuffLoiThan"
    BUFF_TOC_LOI         = "BuffTocLoi"
    BUFF_NGU_PHONG       = "BuffNguPhong"
    BUFF_PHONG_VU        = "BuffPhongVu"
    BUFF_SINH_CO         = "BuffSinhCo"
    BUFF_CAN_CO          = "BuffCanCo"
    BUFF_KIM_CUONG       = "BuffKimCuong"
    BUFF_HOANG_KIM       = "BuffHoangKim"
    BUFF_DAI_DIA         = "BuffDaiDia"
    BUFF_TRONG_TO        = "BuffTrongTo"
    BUFF_BAT_TU          = "BuffBatTu"
    BUFF_TANG_TOC        = "BuffTangToc"
    BUFF_HO_PHAP         = "BuffHoPhap"
    BUFF_HU_KHONG        = "BuffHuKhong"

    # ── Debuffs ──────────────────────────────────────────────────────────────
    DEBUFF_THIEU_DOT = "DebuffThieuDot"   # burn (DoT)
    DEBUFF_TE_LIET   = "DebuffTeLiet"     # paralysis (50% skip chance)
    DEBUFF_DOT_CHAY  = "DebuffDotChay"    # blaze (DoT)
    DEBUFF_HOA_XUYEN_THAU = "DebuffHoaXuyenThau"   # fire res shred
    DEBUFF_MOC_XUYEN_THAU = "DebuffMocXuyenThau"   # wood res shred
    DEBUFF_THUY_XUYEN_THAU = "DebuffThuyXuyenThau"  # water res shred
    DEBUFF_DOC_TO    = "DebuffDocTo"      # poison (DoT)
    DEBUFF_BAO_MON   = "DebuffBaoMon"     # armor shred
    DEBUFF_TRO_BUOC  = "DebuffTroBuoc"    # bind / root
    DEBUFF_LUN_DAT   = "DebuffLunDat"     # knockdown
    DEBUFF_LAM_CHAM  = "DebuffLamCham"    # slow
    DEBUFF_DONG_BANG = "DebuffDongBang"   # freeze
    DEBUFF_CHAY_MAU  = "DebuffChayMau"    # bleed (DoT)
    DEBUFF_PHA_GIAP  = "DebuffPhaGiap"    # armor break (reduces dmg reduction)
    DEBUFF_XE_RACH   = "DebuffXeRach"     # lacerate
    DEBUFF_CUON_BAY  = "DebuffCuonBay"    # knockup
    DEBUFF_CAT_DUT   = "DebuffCatDut"     # sever
    DEBUFF_SET_DANH  = "DebuffSetDanh"    # mark
    EFFECT_NGUNG_DONG = "EffectNgungDong"  # stagnation (spd debuff)

    # ── Crowd control ────────────────────────────────────────────────────────
    CC_MUTED      = "CCMuted"      # silence — prevents skill use
    CC_STUN       = "CCStun"       # stun — skips turn
    CC_INTERRUPT  = "CCInterrupt"  # interrupt — prevents skill use
    CC_LOCK_BREAK = "CCLockBreak"  # lock break

    # ── Utility / regen ──────────────────────────────────────────────────────
    HP_REGEN = "HpRegen"
    MP_REGEN = "MpRegen"
