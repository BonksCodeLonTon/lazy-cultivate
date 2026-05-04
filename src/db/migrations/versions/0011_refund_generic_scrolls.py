"""Refund old generic skill scrolls to merit and delete the inventory rows.

Generic per-category scrolls (ScrollAtkHoang/Huyen/Dia/Thien, ScrollDef*,
ScrollSup*, ScrollFrmThien) were replaced by per-skill scrolls
(``Scroll_<SkillKey>``). Any old rows still in player inventories are
useless to the new learn flow, so refund them at their original shop
price and remove them.

Refund table (merit per unit):
- *Hoang  → 1000
- *Huyen  → 3000
- *Dia    → 8000
- *Thien  → 10000  (original shop_price was 0, but these were the rarest;
                    a flat 10k matches their effective scarcity)

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-04
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_REFUND_TABLE: dict[str, int] = {
    "ScrollAtkHoang": 1000, "ScrollDefHoang": 1000, "ScrollSupHoang": 1000,
    "ScrollAtkHuyen": 3000, "ScrollDefHuyen": 3000, "ScrollSupHuyen": 3000,
    "ScrollAtkDia":   8000, "ScrollDefDia":   8000, "ScrollSupDia":   8000,
    "ScrollAtkThien":10000, "ScrollDefThien":10000, "ScrollSupThien":10000,
    "ScrollFrmThien":10000,
}


def upgrade() -> None:
    bind = op.get_bind()
    inventory = sa.table(
        "inventory",
        sa.column("id", sa.Integer),
        sa.column("player_id", sa.Integer),
        sa.column("item_key", sa.String),
        sa.column("quantity", sa.Integer),
    )
    players = sa.table(
        "players",
        sa.column("id", sa.Integer),
        sa.column("merit", sa.Integer),
    )

    keys = list(_REFUND_TABLE.keys())
    rows = bind.execute(
        sa.select(inventory.c.id, inventory.c.player_id, inventory.c.item_key, inventory.c.quantity)
        .where(inventory.c.item_key.in_(keys))
    ).fetchall()

    refund_by_player: dict[int, int] = {}
    delete_ids: list[int] = []
    for row in rows:
        unit_price = _REFUND_TABLE[row.item_key]
        refund = unit_price * row.quantity
        refund_by_player[row.player_id] = refund_by_player.get(row.player_id, 0) + refund
        delete_ids.append(row.id)

    for player_id, refund in refund_by_player.items():
        bind.execute(
            sa.update(players)
            .where(players.c.id == player_id)
            .values(merit=players.c.merit + refund)
        )

    if delete_ids:
        bind.execute(sa.delete(inventory).where(inventory.c.id.in_(delete_ids)))


def downgrade() -> None:
    # Refund is one-way — the inventory rows are gone. No-op on downgrade.
    pass
