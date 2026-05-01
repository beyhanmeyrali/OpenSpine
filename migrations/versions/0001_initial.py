"""initial — empty migration to establish the migration chain

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-01

The first real schema lands in 0002 (identity tables) once §4.2 of the v0.1
plan is implemented. Keeping this revision empty means we have a known root
to attach future migrations to without a "no migrations exist yet" race
on first deploy.
"""

from collections.abc import Sequence

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
