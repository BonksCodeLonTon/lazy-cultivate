"""Add linh_can column to players table.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "players",
        sa.Column("linh_can", sa.String(128), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("players", "linh_can")
