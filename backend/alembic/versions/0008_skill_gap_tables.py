"""skill gap tables: skill_gap_reports（Phase 6, FR-24~30）

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-28

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_gap_reports",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("resume_id", sa.UUID(), nullable=False),
        sa.Column("resume_version_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("retrieval", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("generation_error", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_version_id"], ["resume_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resume_id", "job_id", name="uq_skill_gap_reports_resume_job"),
    )
    op.create_index(
        op.f("ix_skill_gap_reports_user_id"), "skill_gap_reports", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_skill_gap_reports_resume_version_id"),
        "skill_gap_reports",
        ["resume_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_skill_gap_reports_job_id"), "skill_gap_reports", ["job_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_skill_gap_reports_job_id"), table_name="skill_gap_reports")
    op.drop_index(op.f("ix_skill_gap_reports_resume_version_id"), table_name="skill_gap_reports")
    op.drop_index(op.f("ix_skill_gap_reports_user_id"), table_name="skill_gap_reports")
    op.drop_table("skill_gap_reports")
