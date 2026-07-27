"""job tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector  # autogenerate 不會自動 import pgvector 型別，手動補上

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'jobs',
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('raw_text', sa.Text(), nullable=False),
        sa.Column('company', sa.String(length=255), nullable=True),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('parsed_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('parse_status', sa.String(length=16), nullable=False),
        sa.Column('parse_error', sa.Text(), nullable=True),
        sa.Column('index_status', sa.String(length=16), nullable=False),
        sa.Column('index_error', sa.Text(), nullable=True),
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_jobs_user_id'), 'jobs', ['user_id'], unique=False)

    op.create_table(
        'job_chunks',
        sa.Column('job_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('section', sa.String(length=32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_job_chunks_job_id'), 'job_chunks', ['job_id'], unique=False)
    op.create_index(op.f('ix_job_chunks_user_id'), 'job_chunks', ['user_id'], unique=False)

    op.create_table(
        'job_embeddings',
        sa.Column('chunk_id', sa.UUID(), nullable=False),
        sa.Column('job_id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('model', sa.String(length=128), nullable=False),
        sa.Column('vector', Vector(dim=768), nullable=False),
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['chunk_id'], ['job_chunks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chunk_id'),
    )
    op.create_index(op.f('ix_job_embeddings_job_id'), 'job_embeddings', ['job_id'], unique=False)
    op.create_index(op.f('ix_job_embeddings_user_id'), 'job_embeddings', ['user_id'], unique=False)
    # HNSW cosine 索引：空表可建、小資料集召回穩定。
    op.create_index(
        'ix_job_embeddings_vector',
        'job_embeddings',
        ['vector'],
        unique=False,
        postgresql_using='hnsw',
        postgresql_ops={'vector': 'vector_cosine_ops'},
    )


def downgrade() -> None:
    op.drop_index('ix_job_embeddings_vector', table_name='job_embeddings')
    op.drop_index(op.f('ix_job_embeddings_user_id'), table_name='job_embeddings')
    op.drop_index(op.f('ix_job_embeddings_job_id'), table_name='job_embeddings')
    op.drop_table('job_embeddings')
    op.drop_index(op.f('ix_job_chunks_user_id'), table_name='job_chunks')
    op.drop_index(op.f('ix_job_chunks_job_id'), table_name='job_chunks')
    op.drop_table('job_chunks')
    op.drop_index(op.f('ix_jobs_user_id'), table_name='jobs')
    op.drop_table('jobs')
