"""Verify scroll drop-share targets across zones."""
from src.data.registry import registry

registry.load()

print("Zone | static     | g3 # | w3      | g4 # | w4      | any G3 | any G4")
print("-" * 72)

for zone in range(1, 11):
    table = registry.get_loot_table(f"LootZone_{zone}")
    if not table:
        continue
    grand = sum(int(e.get("weight", 0)) for e in table)
    g3_total = g4_total = 0
    g3_cnt = g4_cnt = 0
    w3 = w4 = 0
    for entry in table:
        item = registry.items.get(entry["item_key"])
        if not item or item.get("type") != "scroll":
            continue
        g = int(item.get("grade", 0))
        w = int(entry.get("weight", 0))
        if g == 3:
            g3_total += w
            g3_cnt += 1
            w3 = w
        elif g == 4:
            g4_total += w
            g4_cnt += 1
            w4 = w
    p3 = g3_total / grand * 100 if grand else 0
    p4 = g4_total / grand * 100 if grand else 0
    static = grand - g3_total - g4_total
    print(
        f"{zone:>4} | {static:>10,} | {g3_cnt:>4} | {w3:>7,} | "
        f"{g4_cnt:>4} | {w4:>7,} | {p3:>5.2f}% | {p4:>5.2f}%"
    )
