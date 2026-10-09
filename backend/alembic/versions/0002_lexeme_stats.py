"""Lexeme document-frequency statistics for IDF-weighted lexical ranking.

Postgres `ts_rank` has no notion of term rarity, so "superhuman" counts the
same as "product". This materialized view stores, per lexeme, how many chunks
contain it in their *spoken text* (title/keywords are repeated on every chunk
of an episode and would inflate document frequency); retrieval uses it to
compute IDF. Refreshed after each ingestion.

Revision ID: 0002_lexeme_stats
Revises: 0001_initial
Create Date: 2026-10-09
"""

from alembic import op

revision = "0002_lexeme_stats"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE MATERIALIZED VIEW kb_lexeme_stats AS
        SELECT word, ndoc FROM ts_stat($$SELECT to_tsvector('english', text) FROM chunks WHERE NOT is_sponsor$$)
        """
    )
    op.execute("CREATE UNIQUE INDEX ix_kb_lexeme_stats_word ON kb_lexeme_stats (word)")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS kb_lexeme_stats")
