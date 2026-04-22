"""Add equipment listing support to market_listings.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-22
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # item_key becomes nullable (equipment listings don't need it)
    op.alter_column("market_listings", "item_key",
                    existing_type=sa.String(64), nullable=True)

    # FK to item_instances so equipment listings track which item is locked
    op.add_column("market_listings",
                  sa.Column("instance_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_market_instance_id",
        "market_listings", "item_instances",
        ["instance_id"], ["id"],
        ondelete="SET NULL",
    )

    # "inventory" or "equipment"
    op.add_column("market_listings",
                  sa.Column("listing_type", sa.String(16),
                            nullable=False, server_default="inventory"))

    # All listings now use merit only
    op.execute("UPDATE market_listings SET currency_type = 'merit'")


def downgrade() -> None:
    op.drop_constraint("fk_market_instance_id", "market_listings", type_="foreignkey")
    op.drop_column("market_listings", "instance_id")
    op.drop_column("market_listings", "listing_type")
    op.alter_column("market_listings", "item_key",
                    existing_type=sa.String(64), nullable=False)
