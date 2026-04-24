"""Add Luyện Đan toxicity tracking to players.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-24
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Accumulated pill toxicity ("Đan Độc"). Increases when players consume
    # pills and can be reduced by purification pills.
    op.add_column(
        "players",
        sa.Column("dan_doc", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("players", "dan_doc")
