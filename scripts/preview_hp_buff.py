"""Preview HP totals at several cultivation milestones after the BASE_HP buff."""
from src.game.constants.balance import BASE_HP_PER_LEVEL, AXIS_HP_WEIGHT


def hp(body_realm, qi_realm, form_realm, body_lvl=9, qi_lvl=9, form_lvl=9):
    body = body_realm * 9 + body_lvl
    qi = qi_realm * 9 + qi_lvl
    form = form_realm * 9 + form_lvl
    return int(
        body * AXIS_HP_WEIGHT["body"] * BASE_HP_PER_LEVEL
        + qi * AXIS_HP_WEIGHT["qi"] * BASE_HP_PER_LEVEL
        + form * AXIS_HP_WEIGHT["formation"] * BASE_HP_PER_LEVEL
    )


print(f"BASE_HP_PER_LEVEL = {BASE_HP_PER_LEVEL}  (+60% from legacy 500)")
print()
print(f"{'Build':<40} | {'HP':>10}")
print("-" * 55)
print(f"{'Fresh Lv1 body (1/0/0/0/0/0)':<40} | {hp(0, 0, 0, 1, 0, 0):>10,}")
print(f"{'Mid R3/R3/R3 (max levels)':<40} | {hp(3, 3, 3):>10,}")
print(f"{'Late R6/R6/R6 (max levels)':<40} | {hp(6, 6, 6):>10,}")
print(f"{'Endgame R8/R8/R8 max':<40} | {hp(8, 8, 8):>10,}")
print(f"{'Body-tank R8 (8/9/0/0/0/0)':<40} | {hp(8, 0, 0, 9, 0, 0):>10,}")
