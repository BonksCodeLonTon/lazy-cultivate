"""Cultivation realm definitions for all 3 axes."""
from __future__ import annotations

from dataclasses import dataclass

LEVELS_PER_REALM = 9


@dataclass(frozen=True)
class Realm:
    index: int   # 0-based realm index
    key: str
    vi: str
    en: str


BODY_REALMS: list[Realm] = [
    Realm(0, "luyen_huyet", "Luyện Huyết", "Blood Tempering"),
    Realm(1, "luyen_bi", "Luyện Bì", "Skin Tempering"),
    Realm(2, "luyen_can", "Luyện Cân", "Tendon Tempering"),
    Realm(3, "luyen_cot", "Luyện Cốt", "Bone Tempering"),
    Realm(4, "luyen_phu", "Luyện Phủ", "Organ Tempering"),
    Realm(5, "phap_tuong", "Pháp Tướng", "Dharma Form"),
    Realm(6, "kim_than", "Kim Thân", "Golden Body"),
    Realm(7, "sieu_pham", "Siêu Phàm", "Transcendent"),
    Realm(8, "nhap_thanh", "Nhập Thánh", "Saint Entry"),
]

QI_REALMS: list[Realm] = [
    Realm(0, "luyen_khi", "Luyện Khí", "Qi Refining"),
    Realm(1, "truc_co", "Trúc Cơ", "Foundation Establishment"),
    Realm(2, "kim_dan", "Kim Đan", "Golden Core"),
    Realm(3, "nguyen_anh", "Nguyên Anh", "Nascent Soul"),
    Realm(4, "hoa_than", "Hóa Thần", "Spirit Transformation"),
    Realm(5, "luyen_hu", "Luyện Hư", "Void Refinement"),
    Realm(6, "hop_dao", "Hợp Đạo", "Dao Integration"),
    Realm(7, "dai_thua", "Đại Thừa", "Mahayana"),
    Realm(8, "dang_tien", "Đăng Tiên", "Immortal Ascension"),
]

FORMATION_REALMS: list[Realm] = [
    Realm(0, "khai_huyen", "Khai Huyền", "Mystery Opening"),
    Realm(1, "nhap_huyen", "Nhập Huyền", "Mystery Entry"),
    Realm(2, "luyen_huyen", "Luyện Huyền", "Mystery Tempering"),
    Realm(3, "dung_huyen", "Dung Huyền", "Mystery Fusion"),
    Realm(4, "hoa_huyen", "Hóa Huyền", "Mystery Transformation"),
    Realm(5, "thong_huyen", "Thông Huyền", "Mystery Mastery"),
    Realm(6, "dong_huyen", "Động Huyền", "Mystery Actualization"),
    Realm(7, "chi_huyen", "Chí Huyền", "Mystery Pinnacle"),
    Realm(8, "quy_nhat", "Quy Nhất", "Return to One"),
]


def realm_label(realms: list[Realm], realm_idx: int, level: int) -> str:
    if realm_idx < 0 or realm_idx >= len(realms):
        return "Chưa Tu Luyện"
    r = realms[realm_idx]
    return f"{r.vi} Cấp {level}"
