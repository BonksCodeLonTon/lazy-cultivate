"""Add super_material_key column to item_instances.

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-24
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "item_instances",
        sa.Column("super_material_key", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("item_instances", "super_material_key")
