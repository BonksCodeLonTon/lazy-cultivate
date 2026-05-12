"""Add quality column to item_instances.

Lets equipment views surface the forge/loot rarity tier (hoan/huyen/dia/thien)
with the rarity icon palette already used elsewhere. Existing rows backfill
to ``hoan`` since pre-migration items did not persist a roll.

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("item_instances")}
    if "quality" in existing_cols:
        return
    op.add_column(
        "item_instances",
        sa.Column(
            "quality",
            sa.String(length=8),
            server_default="hoan",
            nullable=False,
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("item_instances")}
    if "quality" in existing_cols:
        op.drop_column("item_instances", "quality")
