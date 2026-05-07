"""Currency system constants."""

CURRENCY_CAP = 10_000_000

# Turn system
TURNS_PER_DAY = 1440
BONUS_TURNS = 440           # First 440 turns: merit ×2, 0 karma
MERIT_PER_BONUS_TURN = 2
MERIT_PER_NORMAL_TURN = 3
KARMA_PER_NORMAL_TURN = 7  # Accumulated karma per normal turn

KARMA_ACCUM_CAP = 500_000

# Thresholds for evil titles (Nghiệp Lực Tích Lũy)
KARMA_TITLE_THRESHOLDS = {
    100_000: "van_ac_bat_xa",    # Vạn Ác Bất Xá
    200_000: "vo_gian",          # Vô Gian
    350_000: "cuu_u_ma_ton",     # Cửu U Ma Tôn
    500_000: "diet_the_ma_than", # Diệt Thế Ma Thần (+20% FinalDmg)
}

MERIT_TITLE_THRESHOLDS = {
    50_000: "thien_nhan",        # Thiện Nhân
    200_000: "thanh_nhan",       # Thanh Nhân
    500_000: "tien_nhan",        # Tiên Nhân
    1_000_000: "chung_dao",      # Chứng Đạo Thành Tiên
}

# Per-realm EXP gained per Công Đức spent on Trận Đạo (``study_formation``).
# Late-game realms cost more merit per insight — Khai Huyền is cheap to
# bootstrap, Đế Trận demands sustained investment.
#
# Rates are floats (sub-1.0 from R1 onward) because the previous integer
# table let players dump stockpiled merit (from kills, dungeons, world
# bosses) and skip whole bậc instantly while body/qi cultivators wait
# real-time turns. The 10× nerf below pulls Trận Đạo back in line so the
# axis stays balanced against turn-gated cultivation.
FORMATION_EXP_PER_MERIT_BY_REALM: tuple[float, ...] = (
    1.0,  # R0 Khai Huyền  — entry tier, stays cheap
    0.8,  # R1 Nhập Huyền
    0.6,  # R2 Luyện Huyền
    0.5,  # R3 Dung Huyền
    0.4,  # R4 Tâm Trận    — mid-game pivot
    0.3,  # R5 Thiên Trận
    0.3,  # R6 Thần Trận
    0.3,  # R7 Thánh Trận  — heaviest realm by raw EXP
    0.2,  # R8 Đế Trận     — endgame, smaller table but premium rate
)

# Legacy flat rate — retained for backward compatibility / external imports.
# New code should call ``formation_exp_per_merit(realm)`` instead.
FORMATION_MERIT_COST_BASE = 1_000

# Shop special item
CELESTIAL_DAO_COST = 99_000    # Thiên Đạo Phù Nghịch (×2 merit 30 days)

# P2P trade fee
TRADE_FEE_RATE = 0.10
MARKET_MAX_LISTINGS = 5   # Max active listings per player
MARKET_LISTING_HOURS = 72

SECONDS_PER_TURN = 60