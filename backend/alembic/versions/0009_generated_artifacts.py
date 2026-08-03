"""generated artifacts: generated_artifacts（Phase 7, FR-31~44、FR-54~55）

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-31

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generated_artifacts",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("resume_id", sa.UUID(), nullable=False),
        sa.Column("resume_version_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        # append-only：同 (resume_id, job_id, kind) 允許多 row；unique 只鎖到
        # version_number——同版號重複代表 select-max+1 撞到並發，寫入端捕捉
        # IntegrityError 重讀重試。此 constraint 的前導欄位同時充當
        # 「查某 pair 某 kind 的最新版」的查詢索引（慣例：不另建 index）。
        sa.UniqueConstraint(
            "resume_id",
            "job_id",
            "kind",
            "version_number",
            name="uq_generated_artifacts_pair_kind_version",
        ),
    )
    op.create_index(
        op.f("ix_generated_artifacts_user_id"), "generated_artifacts", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_generated_artifacts_resume_version_id"),
        "generated_artifacts",
        ["resume_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_generated_artifacts_job_id"), "generated_artifacts", ["job_id"], unique=False
    )
    op.create_index(
        op.f("ix_generated_artifacts_run_id"), "generated_artifacts", ["run_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_generated_artifacts_run_id"), table_name="generated_artifacts")
    op.drop_index(op.f("ix_generated_artifacts_job_id"), table_name="generated_artifacts")
    op.drop_index(
        op.f("ix_generated_artifacts_resume_version_id"), table_name="generated_artifacts"
    )
    op.drop_index(op.f("ix_generated_artifacts_user_id"), table_name="generated_artifacts")
    op.drop_table("generated_artifacts")
