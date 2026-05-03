"""agent decision trace — id_agent_decision_trace (append-only)

Revision ID: 0005_agent_decision_trace
Revises: 0004_master_data
Create Date: 2026-05-03

Adds the third audit-shaped stream per docs/identity/README.md
§"Audit topology": the "why did the agent do X" log. Joins to
id_audit_event ("what") and id_auth_decision_log ("allowed?")
via shared `trace_id`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005_agent_decision_trace"
down_revision: str | Sequence[str] | None = "0004_master_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "id_agent_decision_trace",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_tenant.id", deferrable=True, initially="DEFERRED"),
            nullable=False,
        ),
        sa.Column(
            "principal_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_principal.id", deferrable=True, initially="DEFERRED"),
            nullable=False,
        ),
        sa.Column("trace_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("action_summary", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column(
            "candidates_considered",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "chosen_path",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "related_audit_event_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("id_audit_event.id", deferrable=True, initially="DEFERRED"),
            nullable=True,
            index=True,
        ),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_id_agent_decision_trace_tenant_principal",
        "id_agent_decision_trace",
        ["tenant_id", "principal_id"],
    )
    op.create_index(
        "ix_id_agent_decision_trace_principal",
        "id_agent_decision_trace",
        ["principal_id"],
    )
    op.create_index(
        "ix_id_agent_decision_trace_trace",
        "id_agent_decision_trace",
        ["trace_id"],
    )
    op.create_index(
        "ix_id_agent_decision_trace_created_at",
        "id_agent_decision_trace",
        ["created_at"],
    )

    op.execute("ALTER TABLE id_agent_decision_trace ENABLE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON id_agent_decision_trace
          USING (tenant_id = current_setting('openspine.tenant_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation ON id_agent_decision_trace;"
    )
    op.execute(
        "ALTER TABLE id_agent_decision_trace DISABLE ROW LEVEL SECURITY;"
    )
    op.drop_index(
        "ix_id_agent_decision_trace_created_at",
        table_name="id_agent_decision_trace",
    )
    op.drop_index(
        "ix_id_agent_decision_trace_trace", table_name="id_agent_decision_trace"
    )
    op.drop_index(
        "ix_id_agent_decision_trace_principal", table_name="id_agent_decision_trace"
    )
    op.drop_index(
        "ix_id_agent_decision_trace_tenant_principal",
        table_name="id_agent_decision_trace",
    )
    op.drop_table("id_agent_decision_trace")
