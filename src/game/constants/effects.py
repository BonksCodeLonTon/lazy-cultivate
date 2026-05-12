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
    BUFF_HU_VO           = "BuffHuVo"
    BUFF_DOAN_TUYET      = "BuffDoanTuyet"
    AURA_HUY_DIET        = "AuraHuyDiet"

    # ── Debuffs ──────────────────────────────────────────────────────────────
    DEBUFF_THIEU_DOT = "DebuffThieuDot"   # burn (DoT)
    DEBUFF_TE_LIET   = "DebuffTeLiet"     # paralysis (50% skip chance)
    DEBUFF_DOT_CHAY  = "DebuffDotChay"    # blaze (DoT)
    DEBUFF_CHAN_HOA  = "DebuffChanHoa"    # Tam Muội Chân Hỏa stack — fire DoT + per-stack fire-DoT amp
    DEBUFF_NGHIEP_HOA_HONG_LIEN = "DebuffNghiepHoaHongLien"  # Red Lotus Karma Fire — uncleansable marker
    DEBUFF_NGHIEP_HOA           = "DebuffNghiepHoa"          # Karma Fire stack — 3% hp_max/turn fire DoT
    DEBUFF_U_MINH               = "DebuffUMinh"              # U Minh — fire stack, no HP damage; each stack burns 5% MP/turn
    BUFF_PHUONG_HOANG_CHAN_HOA  = "BuffPhuongHoangChanHoa"   # Phoenix True Fire — missing-HP regen + reflect Phượng Hỏa on hit taken
    DEBUFF_PHUONG_HOA           = "DebuffPhuongHoa"          # Phượng Hỏa — fire stack DoT + -10% heal/stack, max 3
    BUFF_LUU_LY_TINH_HOA        = "BuffLuuLyTinhHoa"         # Lapis Pure Fire — per-fire-DoT cleanse rolls + per-cleanse crit_res
    BUFF_CUU_DUONG              = "BuffCuuDuong"             # Nine-Sun Body Guard — +60% res_hoa, 40% dmg-taken→hoa, 90% freeze resist
    BUFF_BAT_DIET_HOA_CHUNG     = "BuffBatDietHoaChung"      # Immortal Fire Seed — +70% DR + self-stun phase before retaliation
    BUFF_LUU_TINH_CAN_NGUYET    = "BuffLuuTinhCanNguyet"     # Shooting Star Blocking Moon — +20% spd + per-fire-DoT evasion/spd scaling
    DEBUFF_HOA_VAN              = "DebuffHoaVan"             # Fire Cloud Mark — stack counter consumed by Hỏa Vân Sậu Thiên Kiếm finisher
    BUFF_PHUONG_HOANG_TRIEN_SI  = "BuffPhuongHoangTrienSi"   # Phoenix Wing Spread — +500 crit_dmg + 20% evade rating, auto-casts Phượng Hoàng Chân Hỏa on evade
    DEBUFF_HOA_XUYEN_THAU = "DebuffHoaXuyenThau"   # fire res shred
    DEBUFF_MOC_XUYEN_THAU = "DebuffMocXuyenThau"   # wood res shred
    DEBUFF_THUY_XUYEN_THAU = "DebuffThuyXuyenThau"  # water res shred
    DEBUFF_NHUOC_THUY_AN   = "DebuffNhuocThuyAn"    # weak-water mark (stacking; detonates at cap)
    DEBUFF_CUU_KHUC        = "DebuffCuuKhuc"        # Nine-Bend mark — formation auto-stamp (3/6/9 thresholds)
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
    DEBUFF_SOC_DIEN  = "DebuffSocDien"    # shock (Loi build) — stacks, takes extra loi damage
    DEBUFF_AN_PHONG  = "DebuffAnPhong"    # wind mark (Phong build) — evasion debuff + crit vulnerable
    DEBUFF_LOA_MAT   = "DebuffLoaMat"     # blind (Âm build) — attacker rolls a chance to miss each strike
    DEBUFF_TAN_DIET  = "DebuffTanDiet"    # annihilation — permanent (in-fight) hp_max + hp_regen reduction
    DEBUFF_VO_DAO    = "DebuffVoDao"      # Vô Đạo — incoming heals reduced 90% for 3 turns (Chung Yên)
    DEBUFF_SUY_KHI    = "DebuffSuyKhi"     # qi drain — % ATK reduction on holder
    DEBUFF_PHAP_NHUOC = "DebuffPhapNhuoc"  # spell weakened — % MATK reduction on holder
    DEBUFF_LUC_HON_CHU = "DebuffLucHonChu"  # Six-Soul Curse — Âm DoT, drains shield + MP, amps DoT taken
    DEBUFF_LINH_LUC_KIET = "DebuffLinhLucKiet"  # spiritual exhaustion — % MP regen reduction on holder
    DEBUFF_AM_THUC_KY    = "DebuffAmThucKy"      # Âm devour mark — % am res reduction on holder
    DEBUFF_PHONG_DO_MA   = "DebuffPhongDoMa"     # Fengdu demonic mist — % am res reduction (Phong Đô Ma Trận aura)
    DEBUFF_XICH_LUYEN_TOA_HON = "DebuffXichLuyenToaHon"  # Red-Refining Soul-Lock — % evasion/mp_regen/hp_regen reduction (Xích Luyện Tỏa Hồn Trận aura)
    BUFF_THIEN_SU_HO_MENH = "BuffThienSuHoMenh"  # Angel guardian — auto-active fight-start aura with one-shot 50% revive
    BUFF_THANH_QUANG_THUAN = "BuffThanhQuangThuan"  # Holy light shield — debuff immunity + cleanse-per-turn + DR (3t)
    BUFF_PHAT_QUANG_PHO_CHIEU = "BuffPhatQuangPhoChieu"  # Buddha light — +30% heal taken bonus (3t)
    BUFF_THAM_PHAN_CHI_NO     = "BuffThamPhanChiNo"      # Wrath of Judgment — +20% MATK self-buff (3t)
    BUFF_PHA_MA_CHAN_NGON     = "BuffPhaMaChanNgon"      # Demon-Breaking Mantra — cleanse-on-turn + heal-on-cleanse (4t)
    BUFF_LUU_QUANG_HUYEN_ANH  = "BuffLuuQuangHuyenAnh"   # Flowing Light Phantom — +10% spd per damage taken (cap 30%) + Cực Quang Trảm on evade
    BUFF_QMTH_AURA            = "BuffQuangMinhTungHoanhAura"  # Bright Light Free-Step — passive: +10 shield_max per 1 spd
    BUFF_QMTH_ACTIVE          = "BuffQuangMinhTungHoanh"      # Bright Light Free-Step — active: +20% spd, +0.5% shield regen (4t)
    BUFF_DAI_THIEN_SU_AURA    = "BuffDaiThienSuAura"          # Great Angel summon aura — +10% all res, +10% heal taken
    BUFF_THUY_VI              = "BuffThuyVi"                  # Subtle Water — next N damage casts pierce X% shield (charges in override)
    BUFF_THUY_MAC_THIEN_HOA   = "BuffThuyMacThienHoa"         # Water-Ink Heaven Flower — 25% incoming dmg redirects to MP, +2% MP regen
    BUFF_KINH_HOA_THUY_NGUYET = "BuffKinhHoaThuyNguyet"       # Mirror Flower Water Moon — per-turn 50% transfer 1 debuff from self to enemy
    BUFF_TU_LUONG_BAT_THIEN_CAN = "BuffTuLuongBatThienCan"    # Four Liang Move Thousand Catties — HP-conditional: >50% HP +20% thuy dmg, <50% HP +20% DR
    BUFF_HAI_THI_THAN_LAU_AURA = "BuffHaiThiThanLauAura"      # Sea-Mirage Tower passive — +20 evasion per 1 spd above attacker's
    BUFF_HAI_THI_THAN_LAU      = "BuffHaiThiThanLau"          # Sea-Mirage Tower active — +20% spd, 60% on-skill-hit slow (-20% spd)
    BUFF_LANG_BA_VI_BO_AURA    = "BuffLangBaViBoAura"         # Wave-Stepping Microsteps passive — +500 evasion, +20% spd
    BUFF_TUYET_DIEU_VO_ANH     = "BuffTuyetDieuVoAnh"         # Sublime Shadowless — immune to spd-reducing debuffs (3t)
    BUFF_THUY_THUONG_PHIEU_AURA = "BuffThuyThuongPhieuAura"   # Water-Walking Drift passive — +20% spd
    BUFF_THUY_THUONG_PHIEU      = "BuffThuyThuongPhieu"       # Water-Walking Drift active — slower → +20% spd; faster → +20% thuy dmg
    BUFF_CAM_LO_TINH_HOA       = "BuffCamLoTinhHoa"           # Sweet Dew Purification — 50% per-turn cleanse + 5% HP heal per cleanse
    BUFF_CAM_LO_LONG_LUC       = "BuffCamLoLongLuc"           # Sweet Dew Dragon Power — +20% thuy dmg for 1 turn (post-cleanse proc)
    BUFF_THIEN_MA        = "BuffThienMa"        # Thiên Ma mode — spd/dmg/cdr buff (auto-cycles)
    DEBUFF_THIEN_MA_POST = "DebuffThienMaPost"  # Thiên Ma vulnerability — takes more damage (auto-cycles)
    # Lục Dục — six-desires buffs (auto-cycle via the Lục Dục Thiên Ma Vũ passive)
    BUFF_LUC_DUC_SAC      = "BuffLucDucSac"       # Sắc / Sight   — crit_rating
    BUFF_LUC_DUC_THANH    = "BuffLucDucThanh"     # Thanh / Sound — spd_pct
    BUFF_LUC_DUC_HUONG    = "BuffLucDucHuong"     # Hương / Smell — evasion_rating
    BUFF_LUC_DUC_VI       = "BuffLucDucVi"        # Vị / Taste    — hp_regen_pct
    BUFF_LUC_DUC_XUC      = "BuffLucDucXuc"       # Xúc / Touch   — final_dmg_bonus
    BUFF_LUC_DUC_PHAP     = "BuffLucDucPhap"      # Pháp / Thought— dmg_bonus_am
    BUFF_LUC_DUC_CONG_MINH = "BuffLucDucCongMinh"  # Cộng Minh — +20% amp marker
    BUFF_VO_TUONG_THIEN_MA = "BuffVoTuongThienMa"  # Vô Tướng Thiên Ma — convert 40% damage taken to Âm + Âm res
    BUFF_MA_KHI_HO_THE     = "BuffMaKhiHoThe"      # Ma Khí Hộ Thể — Âm-hit converts to shield
    BUFF_CHAN_MA_CHI_TAM   = "BuffChanMaChiTam"    # Chân Ma Chi Tâm — debuff immunity + apply bonus
    BUFF_QUY_ANH_ME_TUNG   = "BuffQuyAnhMeTung"    # Quỷ Ảnh Mê Tung — stacking evade-on-evade buff
    BUFF_MA_LONG_XUAT_UYEN = "BuffMaLongXuatUyen"  # Ma Long Xuất Uyên — on-evade counter strike
    # Ngưng Đọng — historical key with an "Effect"-style prefix instead of
    # "Debuff*". Predates the data-driven ``EffectMeta.cleansable`` flag;
    # the kind-based default (DEBUFF → cleansable=True) handles it now, so
    # this naming is harmless. Kept as-is to avoid touching every reference.
    EFFECT_NGUNG_DONG = "EffectNgungDong"  # stagnation (spd debuff)

    # ── Crowd control ────────────────────────────────────────────────────────
    CC_MUTED      = "CCMuted"      # silence — prevents skill use
    CC_STUN       = "CCStun"       # stun — skips turn
    CC_INTERRUPT  = "CCInterrupt"  # interrupt — prevents skill use
    CC_LOCK_BREAK = "CCLockBreak"  # lock break

    # ── Utility / regen ──────────────────────────────────────────────────────
    HP_REGEN = "HpRegen"
    MP_REGEN = "MpRegen"
