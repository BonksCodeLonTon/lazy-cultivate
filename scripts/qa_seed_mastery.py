"""QA-only dev seed for Skill Mastery manual testing.

Sets up a player so you can exercise the ``/tinh-thong`` UI immediately without
grinding: puts one equipped skill at a normal gate (lvl 5) and a second at the
hidden gate (lvl 20), grants every breakthrough item + both talismans + the
fruit, and unlocks Đạo Thai (so the hidden Đăng Phong path reveals).

Usage:
    python scripts/qa_seed_mastery.py <discord_id>

Requires the dev DB running and ``.env`` configured. NOT for production — this
is a throwaway QA helper. Remember to set ``SKILL_MASTERY_ENABLED=true`` in
``.env`` and restart the bot before testing.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running as `python scripts/qa_seed_mastery.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which can't encode emoji/diacritics — a
# print crash mid-transaction would roll back the seed, so force UTF-8 output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select  # noqa: E402

from src.data.registry import registry  # noqa: E402
from src.db.connection import get_session  # noqa: E402
from src.db.models.player import Player  # noqa: E402
from src.db.models.skill import CharacterSkill  # noqa: E402
from src.db.repositories.inventory_repo import InventoryRepository  # noqa: E402
from src.db.repositories.skill_mastery import get_or_create  # noqa: E402

# All six mastery items, generous QA quantities (gate costs: 4 / 6 / 10 / 1).
_GRANT = {
    "MasteryLinhNgoPhu": 12,        # gate @5
    "MasteryTamDacNgoc": 12,        # gate @10
    "MasteryDaoVanTinh": 20,        # gate @15
    "MasteryThongThienDaoQua": 3,   # hidden gate @20
    "MasteryHoDaoPhu": 5,           # +20% success
    "MasteryDinhDaoChau": 5,        # refund-on-fail
}


async def main(discord_id: int) -> None:
    registry.load()
    async with get_session() as session:
        player = (
            await session.execute(
                select(Player).where(Player.discord_id == discord_id)
            )
        ).scalar_one_or_none()
        if player is None:
            print(f"❌ No player with discord_id={discord_id}. Create a character first.")
            return

        skills = (
            await session.execute(
                select(CharacterSkill)
                .where(CharacterSkill.player_id == player.id)
                .order_by(CharacterSkill.slot_index)
            )
        ).scalars().all()
        if not skills:
            print("❌ Player has no equipped skills — equip at least one (ideally two) first.")
            return

        # Skill #1 → normal gate ceiling at level 5 (ready to Đột Phá).
        m1 = await get_or_create(session, player.id, skills[0].skill_key)
        m1.level, m1.xp, m1.gate_fails = 5, 0, 0
        print(f"⚔️  {skills[0].skill_key} → mastery level 5 (normal gate, ready to Đột Phá)")

        # Skill #2 → hidden gate at level 20 (Viên Mãn, hidden Đăng Phong).
        if len(skills) >= 2:
            m2 = await get_or_create(session, player.id, skills[1].skill_key)
            m2.level, m2.xp, m2.gate_fails, m2.hidden_unlocked = 20, 0, 0, False
            print(f"🌟 {skills[1].skill_key} → mastery level 20 (hidden Đăng Phong gate)")
        else:
            print("ℹ️  Only one equipped skill — equip a second to test the hidden gate.")

        # Unlock Đạo Thai so the hidden path is allowed to reveal.
        player.dao_ti_unlocked = True
        print("🔓 dao_ti_unlocked = True")

        irepo = InventoryRepository(session)
        for key, qty in _GRANT.items():
            item = registry.get_item(key)
            if not item:
                print(f"   ⚠️  {key} not in registry — skipped")
                continue
            await irepo.add_item(player.id, key, item["grade"], qty)
            print(f"   +{qty:>3}× {item['vi']} ({key})")

    print(
        "\n✅ Seed complete. Ensure SKILL_MASTERY_ENABLED=true in .env, "
        "restart the bot, then run /tinh-thong."
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/qa_seed_mastery.py <discord_id>")
        raise SystemExit(1)
    asyncio.run(main(int(sys.argv[1])))
