"""match tables: resume_embeddings + match_results（Phase 5, FR-19~23）

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-27

"""
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector  # autogenerate 不會自動 import pgvector 型別，手動補上
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resume_embeddings",
        sa.Column("resume_version_id", sa.UUID(), nullable=False),
        sa.Column("resume_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("vector", Vector(dim=768), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["resume_version_id"], ["resume_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("resume_version_id", "kind", name="uq_resume_embeddings_version_kind"),
    )
    op.create_index(
        op.f("ix_resume_embeddings_resume_id"), "resume_embeddings", ["resume_id"], unique=False
    )
    op.create_index(
        op.f("ix_resume_embeddings_user_id"), "resume_embeddings", ["user_id"], unique=False
    )
    # 不建 HNSW 向量索引：resume 向量永遠按 version 取出後在 Python 端比對，
    # 不會成為相似度掃描的目標（與 job_embeddings 不同）。

    op.create_table(
        "match_results",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("resume_id", sa.UUID(), nullable=False),
        sa.Column("resume_version_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=False),
        sa.Column("breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("explanation_error", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("resume_id", "job_id", name="uq_match_results_resume_job"),
    )
    op.create_index(op.f("ix_match_results_user_id"), "match_results", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_match_results_resume_version_id"),
        "match_results",
        ["resume_version_id"],
        unique=False,
    )
    op.create_index(op.f("ix_match_results_job_id"), "match_results", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_match_results_job_id"), table_name="match_results")
    op.drop_index(op.f("ix_match_results_resume_version_id"), table_name="match_results")
    op.drop_index(op.f("ix_match_results_user_id"), table_name="match_results")
    op.drop_table("match_results")
    op.drop_index(op.f("ix_resume_embeddings_user_id"), table_name="resume_embeddings")
    op.drop_index(op.f("ix_resume_embeddings_resume_id"), table_name="resume_embeddings")
    op.drop_table("resume_embeddings")
