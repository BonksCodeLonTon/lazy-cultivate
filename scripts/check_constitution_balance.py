"""Authoring-time balance check for a Thể Chất — run BEFORE/WHILE designing.

Prints how much of each tunable band (docs/constitution_balance_bands.json)
a body's cumulative L9 kit consumes, so a new body can be sized against the
roster's envelope up front instead of discovering the overage in the test
suite afterwards.

Usage (from the repo root):
    python scripts/check_constitution_balance.py TheChat_VoCauLuuLy
    python scripts/check_constitution_balance.py --all           # roster sweep
    python scripts/check_constitution_balance.py <key> --fight   # + live combat bench

Exit code 1 when any checked body exceeds a band (script is CI-safe, but the
canonical gates are tests/test_constitution_balance_bands.py and
tests/test_constitution_power_bench.py).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.registry import registry  # noqa: E402

BANDS_PATH = Path(__file__).parent.parent / "docs" / "constitution_balance_bands.json"


def _bar(frac: float, width: int = 20) -> str:
    filled = min(width, int(round(frac * width)))
    return "█" * filled + "░" * (width - filled)


def check_body(const_key: str, bands: dict, *, fight: bool = False) -> bool:
    from tests.test_constitution_balance_bands import (
        _category_total, _kit_layers, _rarity_cap,
    )

    const_data = registry.get_constitution(const_key)
    if const_data is None:
        print(f"❌ unknown constitution: {const_key}")
        return False
    rarity = const_data.get("rarity", "legendary")
    print(f"\n=== {const_data.get('vi', const_key)} ({const_key}) — {rarity} ===")
    ok = True
    for name, spec in bands["categories"].items():
        total = _category_total(const_data, spec)
        cap = _rarity_cap(spec, rarity)
        if total == 0 and cap > 0:
            continue  # unused lane — keep the report scannable
        frac = total / cap if cap else float("inf")
        mark = "✅" if frac <= 1.0 + 1e-9 else "❌ OVER BAND"
        print(f"  {name:20} {total:8.3f} / {cap:<8g} {_bar(min(1.0, frac))} "
              f"{frac * 100:5.1f}%  {mark}")
        ok = ok and frac <= 1.0 + 1e-9
    # immunity/resist chance rule
    chance_cap = float(bands["resist_immunity_chance_max"])
    for sb, _mult in _kit_layers(const_data):
        for key, val in sb.items():
            if (
                any(w in key for w in ("immune", "resist", "negate"))
                and isinstance(val, (int, float)) and not isinstance(val, bool)
                and 0 < float(val) <= 1.0 and float(val) > chance_cap
            ):
                print(f"  ❌ {key}={val} exceeds resist_immunity_chance_max {chance_cap}")
                ok = False
    if fight:
        from src.utils.config import settings
        settings.constitution_process_enabled = True
        from tests.test_constitution_power_bench import _BENCH, run_benchmark

        th = _BENCH["thresholds"]
        wins, avg_ttk, end_pool = run_benchmark(const_key)
        n = len(_BENCH["seeds"])
        ttk_ok = th["avg_ttk_min"] <= avg_ttk <= th["avg_ttk_max"]
        pool_ok = end_pool >= th["end_pool_min"]
        win_ok = wins >= th["min_wins"]
        print(f"  ⚔️  combat: win {wins}/{n}  avg_ttk {avg_ttk:.1f} "
              f"(band {th['avg_ttk_min']}–{th['avg_ttk_max']})  "
              f"end_pool {end_pool * 100:.0f}% (min {th['end_pool_min'] * 100:.0f}%)  "
              f"{'✅' if ttk_ok and pool_ok and win_ok else '❌ OUTSIDE ENVELOPE'}")
        ok = ok and ttk_ok and pool_ok and win_ok
    return ok


def main() -> int:
    args = [a for a in sys.argv[1:]]
    fight = "--fight" in args
    args = [a for a in args if a != "--fight"]
    registry.load()
    bands = json.loads(BANDS_PATH.read_text(encoding="utf-8"))

    if not args or args[0] == "--all":
        keys = sorted(
            c["key"] for c in registry.constitutions.values() if c.get("process")
        )
    else:
        keys = args
    all_ok = all([check_body(k, bands, fight=fight) for k in keys])
    print("\n" + ("✅ all inside the envelope" if all_ok else "❌ band violations — "
          "trim the body or deliberately raise docs/constitution_balance_bands.json"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
