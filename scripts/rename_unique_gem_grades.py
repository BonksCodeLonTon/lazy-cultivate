"""One-shot migration: replace generic [Hoàng/Huyền/Địa Phẩm] tier suffixes on
grade 1-3 unique gems with distinct Hán Việt names per grade, and rewrite each
tier's ``unique_bonus`` so each grade focuses on a different facet of the gem's
theme rather than being a flat scale-down of grade 4.

Keeps item keys stable (``GemUnique_{Name}_G{N}``) so loot tables don't need
to be touched. Grade-4 entries are left untouched.
"""
from __future__ import annotations
import json
from pathlib import Path

PATH = Path("src/data/items/unique_gems.json")

# Per base-key, per lower-grade: distinct vi/en name, description, and bonus
# set. Each G1-G3 progression picks a different aspect of the theme to
# emphasize — e.g. HuyetSat G1 leans crit-only, G2 leans bleed-application,
# G3 synthesizes both, G4 adds the stack-cap bonus.
OVERRIDES: dict[str, dict[int, dict]] = {
    # ── 1. HuyetSat (Kim, bleed + crit) ─────────────────────────────────
    "GemUnique_HuyetSat": {
        1: {
            "vi": "Sát Khí Ngọc",
            "en": "Killing-Aura Gem",
            "description_vi": "Ngọc hàm chứa sát khí sơ khởi, tăng bạo kích và sát thương bạo kích cho người đeo.",
            "unique_bonus": {"crit_rating": 30, "crit_dmg_rating": 25},
        },
        2: {
            "vi": "Thanh Huyết Ngọc",
            "en": "Cleansing-Blood Gem",
            "description_vi": "Ngọc luyện từ máu tươi của sát thủ ngàn người. Đòn đánh dễ dàng rạch mở da thịt đối thủ.",
            "unique_bonus": {"crit_rating": 40, "bleed_on_hit_pct": 0.08, "bleed_stack_cap_bonus": 1},
        },
        3: {
            "vi": "Chu Sát Ngọc",
            "en": "Crimson-Slaughter Gem",
            "description_vi": "Ngọc đỏ như máu đông cứng. Bạo kích tìm đến nhược điểm của kẻ đang chảy máu một cách vô tình.",
            "unique_bonus": {"crit_rating": 55, "bleed_on_hit_pct": 0.10, "bleed_stack_cap_bonus": 2, "crit_rating_vs_bleed": 40},
        },
    },
    # ── 2. ThieuHon (Hoa, burn) ─────────────────────────────────────────
    "GemUnique_ThieuHon": {
        1: {
            "vi": "Hỏa Tâm Ngọc",
            "en": "Fire-Heart Gem",
            "description_vi": "Ngọc hỏa nhỏ ấp ủ ngọn lửa tâm nguyên. Sát thương tăng nhẹ nhưng ổn định.",
            "unique_bonus": {"final_dmg_bonus": 0.03, "burn_on_hit_pct": 0.05},
        },
        2: {
            "vi": "Hỏa Diễm Ngọc",
            "en": "Fire-Flame Gem",
            "description_vi": "Ngọc hỏa diễm cháy rát, đòn đánh phủ lửa đốt cháy y giáp đối thủ.",
            "unique_bonus": {"final_dmg_bonus": 0.04, "burn_on_hit_pct": 0.12, "fire_res_shred": 0.03},
        },
        3: {
            "vi": "Thiêu Thiên Ngọc",
            "en": "Sky-Burning Gem",
            "description_vi": "Ngọc hỏa cháy lên tận mây xanh, càng đốt lâu càng rát, địch nhân đeo lửa khó lành.",
            "unique_bonus": {"final_dmg_bonus": 0.05, "burn_on_hit_pct": 0.16, "burn_dmg_bonus": 0.10, "fire_res_shred": 0.04},
        },
    },
    # ── 3. LoiToa (Loi, shock + SPD) ───────────────────────────────────
    "GemUnique_LoiToa": {
        1: {
            "vi": "Lôi Quang Ngọc",
            "en": "Thunder-Light Gem",
            "description_vi": "Ngọc lôi mới tụ, chớp sáng nhẹ nhưng đủ để thân pháp nhanh hơn.",
            "unique_bonus": {"crit_rating": 25, "spd_bonus": 3},
        },
        2: {
            "vi": "Lôi Cuồng Ngọc",
            "en": "Thunder-Fury Gem",
            "description_vi": "Ngọc lôi nổ tung khi giáng đòn, thỉnh thoảng làm tê liệt đối thủ.",
            "unique_bonus": {"crit_rating": 40, "shock_on_hit_pct": 0.15, "spd_bonus": 4},
        },
        3: {
            "vi": "Lôi Tỏa Ngọc",
            "en": "Thunder-Shackle Gem",
            "description_vi": "Ngọc lôi khóa chặt đối thủ bằng chín đạo điện lưới, thỉnh thoảng cướp lượt hành động.",
            "unique_bonus": {"crit_rating": 50, "shock_on_hit_pct": 0.20, "turn_steal_pct": 0.03, "spd_bonus": 5},
        },
    },
    # ── 4. AmThuc (Âm, soul drain) ─────────────────────────────────────
    "GemUnique_AmThuc": {
        1: {
            "vi": "Âm Khí Ngọc",
            "en": "Shadow-Aura Gem",
            "description_vi": "Ngọc nhuộm âm khí mờ nhạt, chớm hút sinh lực đối thủ.",
            "unique_bonus": {"crit_rating": 20, "am_res_shred": 0.03},
        },
        2: {
            "vi": "U Hồn Ngọc",
            "en": "Hidden-Soul Gem",
            "description_vi": "Ngọc ẩn chứa hồn phách u ám, thỉnh thoảng ăn mòn HP tối đa của địch.",
            "unique_bonus": {"crit_rating": 30, "soul_drain_on_hit_pct": 0.04, "am_res_shred": 0.04},
        },
        3: {
            "vi": "Âm Nghiệp Ngọc",
            "en": "Shadow-Karma Gem",
            "description_vi": "Ngọc gắn đạo lý âm nghiệp, với địch đã bị bào mòn thì càng dễ tìm nhược điểm.",
            "unique_bonus": {"crit_rating": 40, "soul_drain_on_hit_pct": 0.06, "crit_rating_vs_drained": 60, "am_res_shred": 0.06},
        },
    },
    # ── 5. CuuKhieuHoiXuan (Moc, heal) ─────────────────────────────────
    "GemUnique_CuuKhieuHoiXuan": {
        1: {
            "vi": "Sinh Mạch Ngọc",
            "en": "Life-Meridian Gem",
            "description_vi": "Ngọc mộc khai mở một mạch sinh cơ trong kinh mạch người đeo.",
            "unique_bonus": {"hp_pct": 0.04, "hp_regen_pct": 0.006},
        },
        2: {
            "vi": "Dưỡng Sinh Ngọc",
            "en": "Nurturing-Life Gem",
            "description_vi": "Ngọc mộc thu tinh hoa thảo dược, hồi phục tự nhiên và khuếch đại hiệu lực trị liệu.",
            "unique_bonus": {"hp_pct": 0.05, "heal_pct": 0.10, "hp_regen_pct": 0.009},
        },
        3: {
            "vi": "Hồi Xuân Ngọc",
            "en": "Revival-Spring Gem",
            "description_vi": "Ngọc mộc vận vạn xuân, trị liệu tăng vọt và thân thể tái tạo không ngừng.",
            "unique_bonus": {"hp_pct": 0.06, "heal_pct": 0.20, "hp_regen_pct": 0.012},
        },
    },
    # ── 6. KienThienKhien (Tho, shield) ────────────────────────────────
    "GemUnique_KienThienKhien": {
        1: {
            "vi": "Thạch Bì Ngọc",
            "en": "Stone-Skin Gem",
            "description_vi": "Ngọc thổ tạo da cứng như đá, giảm sát thương đòn đánh trực tiếp.",
            "unique_bonus": {"final_dmg_reduce": 0.03, "shield_cap_pct_bonus": 0.03},
        },
        2: {
            "vi": "Bàn Thạch Ngọc",
            "en": "Boulder Gem",
            "description_vi": "Ngọc thổ nặng như bàn thạch, đòn đánh trúng bị bật ngược.",
            "unique_bonus": {"final_dmg_reduce": 0.05, "thorn_pct": 0.05, "shield_cap_pct_bonus": 0.06},
        },
        3: {
            "vi": "Kiên Thiên Ngọc",
            "en": "Firm-Sky Gem",
            "description_vi": "Ngọc thổ vững như trụ chống trời, khiên hồi phục dần và phản thương mạnh mẽ.",
            "unique_bonus": {"final_dmg_reduce": 0.06, "thorn_pct": 0.08, "shield_regen_pct": 0.015, "shield_cap_pct_bonus": 0.08},
        },
    },
    # ── 7. PhongThanToc (Phong, evasion) ───────────────────────────────
    "GemUnique_PhongThanToc": {
        1: {
            "vi": "Khinh Công Ngọc",
            "en": "Lightfoot Gem",
            "description_vi": "Ngọc phong làm thân pháp nhẹ như lông hồng, né tránh tốt hơn.",
            "unique_bonus": {"spd_bonus": 5, "evasion_rating": 40},
        },
        2: {
            "vi": "Thần Hành Ngọc",
            "en": "Divine-Stride Gem",
            "description_vi": "Ngọc phong ngấm linh khí thần hành, né đòn và gắn dấu lên địch.",
            "unique_bonus": {"spd_bonus": 8, "evasion_rating": 70, "mark_on_hit_pct": 0.08},
        },
        3: {
            "vi": "Phong Ảnh Ngọc",
            "en": "Wind-Shadow Gem",
            "description_vi": "Ngọc phong tan theo bóng gió, né càng cao thì sát thương càng khủng khiếp.",
            "unique_bonus": {"spd_bonus": 10, "evasion_rating": 90, "mark_on_hit_pct": 0.12, "damage_bonus_from_evasion_pct": 0.03},
        },
    },
    # ── 8. ThanhQuangTinhThe (Quang, heal/silence) ─────────────────────
    "GemUnique_ThanhQuangTinhThe": {
        1: {
            "vi": "Quang Tâm Ngọc",
            "en": "Light-Heart Gem",
            "description_vi": "Ngọc quang chiếu sáng trái tim, trị liệu mạnh mẽ hơn người thường.",
            "unique_bonus": {"heal_pct": 0.08, "quang_res_shred": 0.03},
        },
        2: {
            "vi": "Tịnh Hóa Ngọc",
            "en": "Purification Gem",
            "description_vi": "Ngọc quang tự động tẩy debuff nhẹ nhàng và khuếch đại hiệu lực trị liệu.",
            "unique_bonus": {"heal_pct": 0.12, "cleanse_on_turn_pct": 0.05, "quang_res_shred": 0.05},
        },
        3: {
            "vi": "Thánh Quang Ngọc",
            "en": "Holy-Light Gem",
            "description_vi": "Ngọc quang thần thánh, bạo kích làm đối thủ câm lặng và hồi phục của người đeo tăng vọt.",
            "unique_bonus": {"heal_pct": 0.16, "silence_on_crit_pct": 0.12, "cleanse_on_turn_pct": 0.08, "quang_res_shred": 0.06},
        },
    },
    # ── 9. TinhLuuKhiHai (Thuy, MP) ────────────────────────────────────
    "GemUnique_TinhLuuKhiHai": {
        1: {
            "vi": "Thủy Linh Ngọc",
            "en": "Water-Spirit Gem",
            "description_vi": "Ngọc thủy tỏa linh khí nhẹ nhàng, mở rộng dung lượng linh lực của người đeo.",
            "unique_bonus": {"mp_pct": 0.06, "mp_regen_pct": 0.008},
        },
        2: {
            "vi": "Linh Khí Ngọc",
            "en": "Spirit-Qi Gem",
            "description_vi": "Ngọc thủy mang tinh lưu linh khí, hút MP đối thủ khi đánh trúng.",
            "unique_bonus": {"mp_pct": 0.09, "mp_leech_pct": 0.04, "mp_regen_pct": 0.012},
        },
        3: {
            "vi": "Khí Hải Ngọc",
            "en": "Qi-Ocean Gem",
            "description_vi": "Ngọc thủy mang đại hải linh khí, mỗi giọt MP đều trở thành sát thương thêm.",
            "unique_bonus": {"mp_pct": 0.12, "mp_leech_pct": 0.06, "damage_bonus_from_mp_pct": 0.025, "thuy_res_shred": 0.04},
        },
    },
    # ── 10. ThoDiaThuong (Tho, HP/stun) ────────────────────────────────
    "GemUnique_ThoDiaThuong": {
        1: {
            "vi": "Thổ Chấn Ngọc",
            "en": "Earth-Quake Gem",
            "description_vi": "Ngọc thổ làm thân thể vững chãi, chịu đòn tốt hơn người thường.",
            "unique_bonus": {"hp_pct": 0.04},
        },
        2: {
            "vi": "Trấn Địa Ngọc",
            "en": "Earth-Suppress Gem",
            "description_vi": "Ngọc thổ đè nặng vạn vật, đôi khi đòn đánh làm choáng đối thủ.",
            "unique_bonus": {"hp_pct": 0.06, "stun_on_hit_pct": 0.03},
        },
        3: {
            "vi": "Địa Uy Ngọc",
            "en": "Earth-Might Gem",
            "description_vi": "Ngọc thổ oai phong, HP càng nhiều thì đòn đánh càng nặng nề.",
            "unique_bonus": {"hp_pct": 0.08, "stun_on_hit_pct": 0.06, "damage_bonus_from_hp_pct": 0.015, "shield_cap_pct_bonus": 0.04},
        },
    },
    # ── 11. PhaNguyen (Kim, true_dmg + crit) ───────────────────────────
    "GemUnique_PhaNguyen": {
        1: {
            "vi": "Phá Thạch Ngọc",
            "en": "Stone-Breaker Gem",
            "description_vi": "Ngọc kim sắc bén, chém qua lớp giáp mỏng của đối thủ.",
            "unique_bonus": {"final_dmg_bonus": 0.04, "crit_dmg_rating": 40},
        },
        2: {
            "vi": "Phá Quân Ngọc",
            "en": "Army-Breaker Gem",
            "description_vi": "Ngọc kim oai hùng, xuyên qua phòng ngự vạn binh, sát thương tăng vọt.",
            "unique_bonus": {"final_dmg_bonus": 0.06, "crit_dmg_rating": 60, "kim_res_shred": 0.02},
        },
        3: {
            "vi": "Phá Nguyên Ngọc",
            "en": "Origin-Break Gem",
            "description_vi": "Ngọc kim có sát khí phá vỡ bản nguyên, mỗi đòn đều mang theo một ít chân thương.",
            "unique_bonus": {"final_dmg_bonus": 0.08, "crit_dmg_rating": 80, "true_dmg_pct": 0.015, "kim_res_shred": 0.03},
        },
    },
    # ── 12. CuLongTam (Moc, HP scaling) ────────────────────────────────
    "GemUnique_CuLongTam": {
        1: {
            "vi": "Long Tủy Ngọc",
            "en": "Dragon-Marrow Gem",
            "description_vi": "Ngọc luyện từ tủy rồng non, tăng cơ bản sức sống.",
            "unique_bonus": {"hp_pct": 0.06, "hp_regen_flat": 20},
        },
        2: {
            "vi": "Long Cốt Ngọc",
            "en": "Dragon-Bone Gem",
            "description_vi": "Ngọc luyện từ xương rồng trưởng thành, HP càng nhiều thì đòn càng khủng.",
            "unique_bonus": {"hp_pct": 0.10, "damage_bonus_from_hp_pct": 0.012, "hp_regen_flat": 30},
        },
        3: {
            "vi": "Long Tâm Ngọc",
            "en": "Dragon-Heart Gem",
            "description_vi": "Ngọc luyện từ tim rồng cổ đại, mạch huyết nồng đậm nuôi dưỡng sức mạnh hủy diệt.",
            "unique_bonus": {"hp_pct": 0.12, "damage_bonus_from_hp_pct": 0.022, "hp_regen_pct": 0.012, "hp_regen_flat": 32},
        },
    },
    # ── 13. ThienTamQuyDao (neutral, CDR) ──────────────────────────────
    "GemUnique_ThienTamQuyDao": {
        1: {
            "vi": "Quỹ Thạch Ngọc",
            "en": "Orbit-Stone Gem",
            "description_vi": "Ngọc trung tính vận hành nhỏ xoay quanh đan điền, giảm hồi chiêu một ít.",
            "unique_bonus": {"cooldown_reduce": 0.06, "mp_regen_pct": 0.012},
        },
        2: {
            "vi": "Tinh Hà Ngọc",
            "en": "Starstream Gem",
            "description_vi": "Ngọc trung tính kết tinh từ hà sao, kỹ năng xoay vần không ngừng.",
            "unique_bonus": {"cooldown_reduce": 0.09, "mp_regen_pct": 0.018, "final_dmg_bonus": 0.02},
        },
        3: {
            "vi": "Thiên Quỹ Ngọc",
            "en": "Heaven-Orbit Gem",
            "description_vi": "Ngọc trung tính mang quỹ đạo tinh tú, hồi linh và giảm CD sâu như thiên đạo xoay chuyển.",
            "unique_bonus": {"cooldown_reduce": 0.12, "mp_regen_pct": 0.024, "final_dmg_bonus": 0.03, "mp_regen_flat": 15},
        },
    },
    # ── 14. XichHuyetBaoMenh (Hoa, HP/regen) ───────────────────────────
    "GemUnique_XichHuyetBaoMenh": {
        1: {
            "vi": "Xích Tâm Ngọc",
            "en": "Crimson-Heart Gem",
            "description_vi": "Ngọc hỏa đỏ như lửa yếu ớt, tăng một chút HP và hồi phục.",
            "unique_bonus": {"hp_pct": 0.08, "hp_regen_flat": 30},
        },
        2: {
            "vi": "Huyết Linh Ngọc",
            "en": "Blood-Spirit Gem",
            "description_vi": "Ngọc hỏa mang huyết linh khí, hồi máu liên tục và giảm sát thương nhận vào.",
            "unique_bonus": {"hp_pct": 0.12, "hp_regen_flat": 50, "final_dmg_reduce": 0.03},
        },
        3: {
            "vi": "Bảo Mệnh Ngọc",
            "en": "Life-Treasure Gem",
            "description_vi": "Ngọc hỏa bảo hộ sinh mệnh, hồi phục không ngừng nghỉ và thân thể cứng cáp.",
            "unique_bonus": {"hp_pct": 0.16, "hp_regen_flat": 65, "final_dmg_reduce": 0.04, "hp_regen_pct": 0.008},
        },
    },
    # ── 15. SongPhachPhanThan (Loi, crit/paralysis) ────────────────────
    "GemUnique_SongPhachPhanThan": {
        1: {
            "vi": "Song Nguyệt Ngọc",
            "en": "Twin-Moon Gem",
            "description_vi": "Ngọc lôi chia hai ánh trăng, tăng bạo kích cơ bản.",
            "unique_bonus": {"crit_rating": 32, "crit_dmg_rating": 30},
        },
        2: {
            "vi": "Phân Hồn Ngọc",
            "en": "Split-Soul Gem",
            "description_vi": "Ngọc lôi phân tách hồn lực, đòn đánh kèm theo dư chấn.",
            "unique_bonus": {"crit_rating": 48, "crit_dmg_rating": 50, "shock_on_hit_pct": 0.05},
        },
        3: {
            "vi": "Song Phách Ngọc",
            "en": "Dual-Soul Gem",
            "description_vi": "Ngọc lôi chia hai mạch song hành, bạo kích bùng nổ lôi quang khắp nơi.",
            "unique_bonus": {"crit_rating": 64, "crit_dmg_rating": 65, "shock_on_hit_pct": 0.08, "spd_bonus": 3},
        },
    },
    # ── 16. PhongAnPhaGioi (neutral, resistance) ───────────────────────
    "GemUnique_PhongAnPhaGioi": {
        1: {
            "vi": "Cấm Pháp Ngọc",
            "en": "Forbidden-Law Gem",
            "description_vi": "Ngọc trung tính mang dấu cổ pháp, kháng một phần debuff.",
            "unique_bonus": {"debuff_immune_pct": 0.06, "res_all": 15},
        },
        2: {
            "vi": "Phá Ấn Ngọc",
            "en": "Seal-Break Gem",
            "description_vi": "Ngọc trung tính phá vỡ phong ấn, miễn nhiễm nhiều loại trói buộc.",
            "unique_bonus": {"debuff_immune_pct": 0.10, "res_all": 25, "crit_res_rating": 60},
        },
        3: {
            "vi": "Phá Giới Ngọc",
            "en": "Boundary-Break Gem",
            "description_vi": "Ngọc trung tính vượt qua giới luật thiên địa, kháng toàn diện và khó bị bạo kích.",
            "unique_bonus": {"debuff_immune_pct": 0.12, "res_all": 32, "crit_res_rating": 80, "final_dmg_bonus": 0.04},
        },
    },
    # ── 17. HanPhongBangCot (Thuy, freeze/slow) ────────────────────────
    "GemUnique_HanPhongBangCot": {
        1: {
            "vi": "Băng Tâm Ngọc",
            "en": "Ice-Heart Gem",
            "description_vi": "Ngọc thủy lạnh thấm vào tâm, đòn đánh làm chậm đối thủ đôi chút.",
            "unique_bonus": {"slow_on_hit_pct": 0.08, "thuy_res_shred": 0.02},
        },
        2: {
            "vi": "Hàn Phong Ngọc",
            "en": "Cold-Wind Gem",
            "description_vi": "Ngọc thủy thổi hàn phong, địch bị đánh dấu trở nên dễ bạo kích hơn.",
            "unique_bonus": {"slow_on_hit_pct": 0.12, "crit_rating_vs_marked": 30, "thuy_res_shred": 0.04},
        },
        3: {
            "vi": "Băng Cốt Ngọc",
            "en": "Frost-Bone Gem",
            "description_vi": "Ngọc thủy lạnh thấu xương, chiêu thức ngấm hàn khí xóa đi sức nóng của địch.",
            "unique_bonus": {"slow_on_hit_pct": 0.16, "crit_rating_vs_marked": 50, "thuy_res_shred": 0.05},
        },
    },
    # ── 18. DiaHuyenKimCuong (Tho, thorn/shield) ───────────────────────
    "GemUnique_DiaHuyenKimCuong": {
        1: {
            "vi": "Kim Cương Ngọc",
            "en": "Diamond Gem",
            "description_vi": "Ngọc thổ cứng như kim cương, phản một phần sát thương nhận vào.",
            "unique_bonus": {"thorn_pct": 0.08, "shield_regen_flat": 20},
        },
        2: {
            "vi": "Huyền Thạch Ngọc",
            "en": "Mystic-Stone Gem",
            "description_vi": "Ngọc thổ huyền bí, khiên hồi phục và phản thương đều mạnh mẽ.",
            "unique_bonus": {"thorn_pct": 0.12, "shield_regen_flat": 30, "final_dmg_reduce": 0.03},
        },
        3: {
            "vi": "Địa Huyền Ngọc",
            "en": "Earth-Mystic Gem",
            "description_vi": "Ngọc thổ huyền khổng lồ, phản thương kinh hoàng và khiên tự dày lên theo thời gian.",
            "unique_bonus": {"thorn_pct": 0.16, "shield_regen_flat": 40, "final_dmg_reduce": 0.05, "shield_cap_pct_bonus": 0.06},
        },
    },
    # ── 19. VoThuongChanThuong (Kim, true_dmg + bleed) ─────────────────
    "GemUnique_VoThuongChanThuong": {
        1: {
            "vi": "Sắc Kiếm Ngọc",
            "en": "Keen-Blade Gem",
            "description_vi": "Ngọc kim sắc bén, rạch vết thương hở trên da đối thủ.",
            "unique_bonus": {"bleed_on_hit_pct": 0.05, "crit_rating_vs_bleed": 30},
        },
        2: {
            "vi": "Vô Hình Ngọc",
            "en": "Formless Gem",
            "description_vi": "Ngọc kim mang đạo vô hình, một số đòn xuyên thẳng qua phòng ngự.",
            "unique_bonus": {"true_dmg_pct": 0.012, "bleed_on_hit_pct": 0.08, "crit_rating_vs_bleed": 45},
        },
        3: {
            "vi": "Vô Thường Ngọc",
            "en": "Impermanence Gem",
            "description_vi": "Ngọc kim mang đạo lý vô thường, xuyên qua giáp và chảy máu không ngừng.",
            "unique_bonus": {"true_dmg_pct": 0.02, "bleed_on_hit_pct": 0.08, "crit_rating_vs_bleed": 60, "crit_dmg_vs_bleed": 45},
        },
    },
    # ── 20. HonNguyenTinh (neutral, balanced) ──────────────────────────
    "GemUnique_HonNguyenTinh": {
        1: {
            "vi": "Nguyên Khí Ngọc",
            "en": "Origin-Qi Gem",
            "description_vi": "Ngọc trung tính mang nguyên khí thô sơ, tăng sát thương và giảm sát thương nhẹ.",
            "unique_bonus": {"final_dmg_bonus": 0.02, "final_dmg_reduce": 0.02, "res_all": 15},
        },
        2: {
            "vi": "Hỗn Khí Ngọc",
            "en": "Chaos-Qi Gem",
            "description_vi": "Ngọc trung tính ngấm hỗn khí, cân bằng công và thủ ở mức khá.",
            "unique_bonus": {"final_dmg_bonus": 0.03, "final_dmg_reduce": 0.03, "res_all": 25, "crit_rating": 20},
        },
        3: {
            "vi": "Hỗn Nguyên Ngọc",
            "en": "Primordial-Chaos Gem",
            "description_vi": "Ngọc trung tính tiếp cận đạo hỗn nguyên, không nổi trội nhưng phù hợp mọi lộ trình.",
            "unique_bonus": {"final_dmg_bonus": 0.035, "final_dmg_reduce": 0.035, "res_all": 40, "crit_rating": 30, "cooldown_reduce": 0.04},
        },
    },
    # ── 21. VanHoaTuyetDoi (Moc, dot/poison) ───────────────────────────
    "GemUnique_VanHoaTuyetDoi": {
        1: {
            "vi": "Độc Hương Ngọc",
            "en": "Poison-Incense Gem",
            "description_vi": "Ngọc mộc tỏa mùi hương lờ mờ, khuếch đại hiệu lực DoT nhẹ.",
            "unique_bonus": {"dot_dmg_bonus": 0.10, "moc_res_shred": 0.02},
        },
        2: {
            "vi": "Vạn Độc Ngọc",
            "en": "Myriad-Poison Gem",
            "description_vi": "Ngọc mộc ngấm vạn loại độc dược, DoT thấm sâu và lâu tan.",
            "unique_bonus": {"dot_dmg_bonus": 0.15, "poison_dmg_bonus": 0.10, "moc_res_shred": 0.03},
        },
        3: {
            "vi": "Vạn Hoa Ngọc",
            "en": "Myriad-Flower Gem",
            "description_vi": "Ngọc mộc chứa vạn hoa cực độc, DoT thăng hoa thành trào lưu hủy diệt.",
            "unique_bonus": {"dot_dmg_bonus": 0.20, "poison_dmg_bonus": 0.15, "moc_res_shred": 0.05, "burn_dmg_bonus": 0.10},
        },
    },
    # ── 22. TuyetLongCamDao (Thuy, anti-heal/slow) ─────────────────────
    "GemUnique_TuyetLongCamDao": {
        1: {
            "vi": "Tuyết Linh Ngọc",
            "en": "Snow-Spirit Gem",
            "description_vi": "Ngọc thủy ngấm linh khí tuyết, làm chậm đối thủ đôi chút.",
            "unique_bonus": {"slow_on_hit_pct": 0.08, "heal_reduce_on_hit_pct": 0.10},
        },
        2: {
            "vi": "Cấm Tuyết Ngọc",
            "en": "Forbidden-Snow Gem",
            "description_vi": "Ngọc thủy mang phong ấn tuyết cấm, cắt đứt nguồn hồi phục của địch.",
            "unique_bonus": {"slow_on_hit_pct": 0.12, "heal_reduce_on_hit_pct": 0.18, "thuy_res_shred": 0.03},
        },
        3: {
            "vi": "Tuyết Long Ngọc",
            "en": "Snow-Dragon Gem",
            "description_vi": "Ngọc thủy hóa tuyết long, chiêu thức đóng băng một phần và anti-heal mạnh mẽ.",
            "unique_bonus": {"slow_on_hit_pct": 0.14, "heal_reduce_on_hit_pct": 0.22, "thuy_res_shred": 0.04, "final_dmg_bonus": 0.03},
        },
    },
}


def main() -> None:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    by_key = {g["key"]: g for g in data}
    updated = 0
    for base_key, grades in OVERRIDES.items():
        for grade, fields in grades.items():
            key = f"{base_key}_G{grade}"
            entry = by_key.get(key)
            if not entry:
                print(f"  !! missing: {key}")
                continue
            entry["vi"] = fields["vi"]
            entry["en"] = fields["en"]
            entry["description_vi"] = fields["description_vi"]
            entry["unique_bonus"] = fields["unique_bonus"]
            updated += 1
    PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Updated {updated} lower-grade unique gems")


if __name__ == "__main__":
    main()
