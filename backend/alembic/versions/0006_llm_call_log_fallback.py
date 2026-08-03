"""LLM call log: attempts / repair_used / fallback_used（FR-59）

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-27

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # attempts：協調層發出的「生成請求」次數（含 fallback；不含 tenacity 網路層重試）。
    op.add_column(
        "llm_call_logs",
        sa.Column("attempts", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    # repair_used：最終物件是否經 repair_json 修復而來。
    op.add_column(
        "llm_call_logs",
        sa.Column("repair_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    # fallback_used：是否切換到 fallback model（此時 model 欄記的是 fallback 模型）。
    op.add_column(
        "llm_call_logs",
        sa.Column("fallback_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("llm_call_logs", "fallback_used")
    op.drop_column("llm_call_logs", "repair_used")
    op.drop_column("llm_call_logs", "attempts")
