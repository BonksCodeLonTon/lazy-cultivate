"""Cultivation realm definitions - Revamped version with Merit-based Formation"""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class Realm:
    index: int   # Thứ tự cảnh giới lớn (0, 1, 2...)
    key: str
    vi: str
    en: str
    base_exp_rate: int  # Tốc độ EXP nhận được mỗi chu kỳ (Dành cho Body/Qi)

# ─── BẢNG TRA CỨU EXP TÍCH LŨY (Theo ảnh Spec) ──────────────────────
REALM_LEVEL_EXP_TABLE = {
    1: 133,
    2: 267,
    3: 400,
    4: 533,
    5: 667,
    6: 800,
    7: 933,
    8: 1067,
    9: 1200,
}

# Chi phí EXP để thực hiện một lần Độ Kiếp (270,000 EXP)
TRIBULATION_EXP_COST = 270000

MERIT_TO_FORMATION_EXP_RATIO = 10 

LEVELS_PER_REALM = 9


BODY_REALMS: list[Realm] = [
    Realm(0, "luyen_huyet", "Luyện Huyết", "Blood Tempering", 1),
    Realm(1, "luyen_bi", "Luyện Bì", "Skin Tempering", 2),
    Realm(2, "luyen_can", "Luyện Cân", "Tendon Tempering", 3),
    Realm(3, "luyen_cot", "Luyện Cốt", "Bone Tempering", 4),
    Realm(4, "luyen_phu", "Luyện Phủ", "Organ Tempering", 5),
    Realm(5, "phap_tuong", "Pháp Tướng", "Dharma Form", 6),
    Realm(6, "kim_than", "Kim Thân", "Golden Body", 7),
    Realm(7, "sieu_pham", "Siêu Phàm", "Transcendent", 8),
    Realm(8, "nhap_thanh", "Nhập Thánh", "Saint Entry", 10),
]

QI_REALMS: list[Realm] = [
    Realm(0, "luyen_khi", "Luyện Khí", "Qi Refining", 1),
    Realm(1, "truc_co", "Trúc Cơ", "Foundation Establishment", 2),
    Realm(2, "kim_dan", "Kim Đan", "Golden Core", 3),
    Realm(3, "nguyen_anh", "Nguyên Anh", "Nascent Soul", 4),
    Realm(4, "hoa_than", "Hóa Thần", "Spirit Transformation", 5),
    Realm(5, "luyen_hu", "Luyện Hư", "Void Refinement", 6),
    Realm(6, "hop_dao", "Hợp Đạo", "Dao Integration", 7),
    Realm(7, "dai_thua", "Đại Thừa", "Mahayana", 8),
    Realm(8, "dang_tien", "Đăng Tiên", "Immortal Ascension", 10),
]

FORMATION_REALMS: list[Realm] = [
    Realm(0, "khai_huyen", "Khai Huyền", "Mystery Opening", 0),
    Realm(1, "nhap_huyen", "Nhập Huyền", "Mystery Entry", 0),
    Realm(2, "luyen_huyen", "Luyện Huyền", "Mystery Tempering", 0),
    Realm(3, "dung_huyen", "Dung Huyền", "Mystery Fusion", 0),
    Realm(4, "tam_tran", "Tâm Trận", "Heart Formation", 0),
    Realm(5, "thien_tran", "Thiên Trận", "Heavenly Formation", 0),
    Realm(6, "than_tran", "Thần Trận", "Divine Formation", 0),
    Realm(7, "thanh_tran", "Thánh Trận", "Saint Formation", 0),
    Realm(8, "de_tran", "Đế Trận", "Emperor Formation", 0),
]

def get_level_from_exp(exp: int) -> int:
    current_level = 1
    for level, req_exp in REALM_LEVEL_EXP_TABLE.items():
        if exp >= req_exp:
            current_level = level
        else:
            break
    return min(current_level, 9)

def realm_label(axis: str, realm_idx: int, exp: int) -> str:
    """Trả về tên hiển thị: Cảnh giới + Bậc (VD: Luyện Khí (Bậc 5))"""
    realms_map = {
        "qi": QI_REALMS,
        "body": BODY_REALMS,
        "formation": FORMATION_REALMS
    }
    r_list = realms_map.get(axis, QI_REALMS)
    
    if 0 <= realm_idx < len(r_list):
        realm = r_list[realm_idx]
        level = get_level_from_exp(exp)
        return f"{realm.vi} (Bậc {level})"
    return "Vô Cảnh Giới"