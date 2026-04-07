"""Linh Căn (Spiritual Root) constants and helpers."""
from __future__ import annotations

# All possible Linh Căn keys
ALL_LINH_CAN = ["kim", "moc", "thuy", "hoa", "tho", "phong", "loi", "quang", "am"]

# Full data for each Linh Căn
LINH_CAN_DATA: dict[str, dict] = {
    "kim": {
        "vi": "Kim",
        "emoji": "⚙️",
        "description": "+5% sát thương",
        "passive_bonus": {"final_dmg_bonus": 0.05},
        "combat_effect": "xuyen_thau",
        "proc_chance": 0.20,
        "effect_desc": "Xuyên Thấu (20%): Bỏ qua 30% giáp mục tiêu",
    },
    "moc": {
        "vi": "Mộc",
        "emoji": "🌿",
        "description": "+8% Sinh Lực tối đa",
        "passive_bonus": {"hp_pct": 0.08},
        "combat_effect": "hoi_xuan",
        "proc_chance": 1.0,
        "effect_desc": "Hồi Xuân (100%): Cuối lượt hồi 4% HP tối đa",
    },
    "thuy": {
        "vi": "Thủy",
        "emoji": "💧",
        "description": "+5% giảm sát thương",
        "passive_bonus": {"final_dmg_reduce": 0.05},
        "combat_effect": "ngung_dong",
        "proc_chance": 0.15,
        "effect_desc": "Ngưng Đọng (15%): Giảm 25% tốc đối thủ 2 lượt",
    },
    "hoa": {
        "vi": "Hỏa",
        "emoji": "🔥",
        "description": "+5% sát thương",
        "passive_bonus": {"final_dmg_bonus": 0.05},
        "combat_effect": "bao_liet",
        "proc_chance": 0.20,
        "effect_desc": "Bạo Liệt (20%): Thiêu Đốt 4% HP/lượt × 2 lượt",
    },
    "tho": {
        "vi": "Thổ",
        "emoji": "🪨",
        "description": "+8% giảm sát thương",
        "passive_bonus": {"final_dmg_reduce": 0.08},
        "combat_effect": "ho_the",
        "proc_chance": 1.0,
        "effect_desc": "Hộ Thể (khi HP<35%): Khiên 20% HP tối đa (1 lần/trận)",
    },
    "phong": {
        "vi": "Phong",
        "emoji": "🌪️",
        "description": "+8% tốc độ",
        "passive_bonus": {"spd_pct": 0.08},
        "combat_effect": "toc_bien",
        "proc_chance": 0.10,
        "effect_desc": "Tốc Biến (10%): Né tránh hoàn toàn đòn tấn công",
    },
    "loi": {
        "vi": "Lôi",
        "emoji": "⚡",
        "description": "+5% tỷ lệ bạo kích",
        "passive_bonus": {"crit_rating": 65},
        "combat_effect": "te_liet",
        "proc_chance": 0.12,
        "effect_desc": "Tê Liệt (12% khi bạo kích): Choáng đối thủ 1 lượt",
    },
    "quang": {
        "vi": "Quang",
        "emoji": "✨",
        "description": "+10% hiệu quả trị liệu",
        "passive_bonus": {"heal_pct": 0.10},
        "combat_effect": "thanh_tay",
        "proc_chance": 0.15,
        "effect_desc": "Thanh Tẩy (15%): Giải 1 hiệu ứng xấu + hồi 5% MP",
    },
    "am": {
        "vi": "Ám",
        "emoji": "🌑",
        "description": "+5% né tránh",
        "passive_bonus": {"evasion_rating": 68},
        "combat_effect": "hu_the",
        "proc_chance": 0.15,
        "effect_desc": "Hủ Thể (15%): Hút máu 30% sát thương gây ra",
    },
}


def compute_linh_can_bonuses(linh_can_list: list[str]) -> dict:
    """Return merged passive bonus dict for the given Linh Căn list."""
    merged: dict = {}
    for key in linh_can_list:
        data = LINH_CAN_DATA.get(key, {})
        for k, v in data.get("passive_bonus", {}).items():
            if isinstance(v, (int, float)):
                merged[k] = merged.get(k, type(v)(0)) + v
            else:
                merged[k] = v
    return merged


def parse_linh_can(raw: str) -> list[str]:
    """Parse comma-separated linh_can string from DB."""
    if not raw:
        return []
    return [lc.strip() for lc in raw.split(",") if lc.strip() in LINH_CAN_DATA]


def format_linh_can(linh_can_list: list[str]) -> str:
    """Format linh_can list to comma-separated string for DB storage."""
    return ",".join(linh_can_list)
