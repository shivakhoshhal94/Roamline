"""Track the exact chunk content represented by each embedding."""
from alembic import op
import sqlalchemy as sa
revision = "0007_embedding_content_hash"
down_revision = "0006_destination_knowledge"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("knowledge_embeddings", sa.Column("content_hash", sa.String(64), nullable=True))
    op.execute("UPDATE knowledge_embeddings e SET content_hash=c.content_hash FROM knowledge_chunks c WHERE c.id=e.chunk_id")
    op.alter_column("knowledge_embeddings", "content_hash", nullable=False)


def downgrade():
    op.drop_column("knowledge_embeddings", "content_hash")
