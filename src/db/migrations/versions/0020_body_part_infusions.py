"""Bách Thể Chú Linh — per-part vital-essence infusions for the Thể Tu rework.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # JSON-encoded ``{part_key: essence_key}`` (max 9 entries — one per body
    # part). Server default keeps pre-migration rows valid; the app layer
    # treats "{}"/NULL identically via ``body_parts.parse_infusions``.
    op.add_column(
        "players",
        sa.Column(
            "body_part_infusions",
            sa.String(length=512),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("players", "body_part_infusions")
