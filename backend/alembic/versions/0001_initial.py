"""Initial schema: users, sessions, messages, artifacts, knowledge base.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-09
"""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _execute_script(sql: str) -> None:
    # asyncpg cannot run several statements in one prepared statement.
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    _execute_script(
        """
        CREATE TABLE users (
            id            uuid PRIMARY KEY,
            external_id   varchar(128) NOT NULL UNIQUE,
            display_name  varchar(120),
            metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at    timestamptz NOT NULL DEFAULT now(),
            last_seen_at  timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE chat_sessions (
            id          uuid PRIMARY KEY,
            user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title       varchar(200) NOT NULL DEFAULT 'New chat',
            metadata    jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_chat_sessions_user_updated ON chat_sessions (user_id, updated_at DESC);

        CREATE TABLE messages (
            id            uuid PRIMARY KEY,
            session_id    uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            role          varchar(16) NOT NULL CHECK (role IN ('user', 'assistant')),
            content       text NOT NULL DEFAULT '',
            status        varchar(16) NOT NULL DEFAULT 'complete'
                          CHECK (status IN ('complete', 'degraded', 'error')),
            route         varchar(32),
            route_reason  varchar(200),
            provider      varchar(32),
            model         varchar(120),
            sources       jsonb NOT NULL DEFAULT '[]'::jsonb,
            citations     jsonb NOT NULL DEFAULT '[]'::jsonb,
            quality       jsonb NOT NULL DEFAULT '{}'::jsonb,
            metrics       jsonb NOT NULL DEFAULT '{}'::jsonb,
            error         jsonb,
            feedback      smallint CHECK (feedback IN (-1, 1)),
            created_at    timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ix_messages_session_created ON messages (session_id, created_at);

        CREATE TABLE artifacts (
            id                uuid PRIMARY KEY,
            session_id        uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            message_id        uuid REFERENCES messages(id) ON DELETE SET NULL,
            kind              varchar(16) NOT NULL CHECK (kind IN ('markdown', 'html')),
            title             varchar(200) NOT NULL,
            content           text NOT NULL,
            version           integer NOT NULL DEFAULT 1,
            sanitizer_report  jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_artifacts_session_id ON artifacts (session_id);

        CREATE TABLE episodes (
            id                serial PRIMARY KEY,
            slug              varchar(200) NOT NULL UNIQUE,
            title             text NOT NULL,
            guest             varchar(300) NOT NULL,
            publish_date      date,
            youtube_url       text,
            video_id          varchar(32),
            duration_seconds  integer,
            description       text,
            keywords          text[] NOT NULL DEFAULT '{}',
            source_path       text NOT NULL,
            content_hash      varchar(64) NOT NULL,
            word_count        integer NOT NULL DEFAULT 0,
            metadata_verified boolean NOT NULL DEFAULT true,
            updated_at        timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE chunks (
            id             bigserial PRIMARY KEY,
            episode_id     integer NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
            chunk_index    integer NOT NULL,
            speakers       text NOT NULL DEFAULT '',
            start_seconds  integer,
            text           text NOT NULL,
            word_count     integer NOT NULL,
            is_sponsor     boolean NOT NULL DEFAULT false,
            tsv            tsvector NOT NULL,
            embedding      vector(768),
            UNIQUE (episode_id, chunk_index)
        );
        CREATE INDEX ix_chunks_episode_id ON chunks (episode_id);
        CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv);
        CREATE INDEX ix_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);

        CREATE TABLE ingestion_runs (
            id           serial PRIMARY KEY,
            status       varchar(16) NOT NULL,
            trigger      varchar(32) NOT NULL,
            source_ref   varchar(64),
            stats        jsonb NOT NULL DEFAULT '{}'::jsonb,
            error        text,
            started_at   timestamptz NOT NULL DEFAULT now(),
            finished_at  timestamptz
        );
        """
    )


def downgrade() -> None:
    _execute_script(
        """
        DROP TABLE IF EXISTS ingestion_runs, chunks, episodes, artifacts, messages,
                             chat_sessions, users CASCADE;
        """
    )
