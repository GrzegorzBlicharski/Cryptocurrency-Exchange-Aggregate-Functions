"""autonomous missions

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "missions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("goal_id", sa.Integer(), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("success_criteria", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("cadence_hours", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        sa.Column("progress_summary", sa.Text(), nullable=False),
        sa.Column("blocked_reason", sa.String(length=500), nullable=False),
        sa.Column("runs", sa.Integer(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["parent_id"], ["missions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "mission_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mission_id", sa.Integer(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["mission_id"], ["missions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("mission_runs") as b:
        b.create_index("ix_mission_runs_mission_id", ["mission_id"], unique=False)


def downgrade() -> None:
    op.drop_table("mission_runs")
    op.drop_table("missions")
