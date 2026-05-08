"""Track per-user /reset_character + /legendary_reroll uses.

Lives on its own table keyed by ``discord_id`` (not ``player_id``) so the
counters persist after the player row is deleted by reset — a user cannot
bypass either cap by deleting their character and re-registering.

``count`` enforces the 5-reroll cap on /reset_character.
``legendary_reroll_used`` enforces the 1-shot lifetime cap on
/legendary_reroll (re-roll constitution to a legendary one).

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: ``init_db()`` (``Base.metadata.create_all`` in main.py) may
    # have already created ``reroll_trackers`` from the ORM model on a prior
    # bot startup, before alembic was run. In that case we only need to add
    # the ``legendary_reroll_used`` column. On a fresh database we still
    # create the full table here.
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "reroll_trackers" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("reroll_trackers")}
        if "legendary_reroll_used" not in existing_cols:
            op.add_column(
                "reroll_trackers",
                sa.Column(
                    "legendary_reroll_used",
                    sa.Boolean(),
                    server_default=sa.text("false"),
                    nullable=False,
                ),
            )
        return

    op.create_table(
        "reroll_trackers",
        sa.Column("discord_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "legendary_reroll_used",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "reroll_trackers" not in inspector.get_table_names():
        return
    existing_cols = {c["name"] for c in inspector.get_columns("reroll_trackers")}
    if "legendary_reroll_used" in existing_cols:
        op.drop_column("reroll_trackers", "legendary_reroll_used")
    # Don't drop the whole table — it may have been created outside alembic
    # (init_db) and other operators may not want it removed on downgrade.
