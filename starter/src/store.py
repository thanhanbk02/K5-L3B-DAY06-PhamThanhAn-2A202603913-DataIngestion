"""Persist chunk vectors in PostgreSQL + pgvector and search them exactly."""
import json
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .models import Chunk

_ALLOWED_FILTERS = {"section", "document_id", "page", "source"}


class PgVectorStore:
    """Exact cosine search over one row per strategy and chunk id."""

    def __init__(self, database_url: str, dimensions: int) -> None:
        if dimensions < 1:
            raise ValueError("dimensions must be positive")
        self.database_url = database_url
        self.dimensions = dimensions
        try:
            self._connection = psycopg.connect(database_url)
        except psycopg.Error as exc:
            raise RuntimeError(f"Database connection failed: {exc}") from exc

    def close(self) -> None:
        self._connection.close()

    def create_schema(self) -> None:
        """Create the chunk table. Cosine search is exact (no ANN index)."""
        with self._connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS chunk_embeddings (
                    strategy text NOT NULL,
                    chunk_id text NOT NULL,
                    document_id text NOT NULL,
                    source text NOT NULL,
                    page integer NOT NULL,
                    section text,
                    body text NOT NULL,
                    metadata jsonb NOT NULL,
                    embedding vector({self.dimensions}) NOT NULL,
                    PRIMARY KEY (strategy, chunk_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS chunk_embeddings_strategy_section_idx
                ON chunk_embeddings (strategy, section)
                """
            )
        self._connection.commit()

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Replace this strategy's rows for the documents being written.

        Stable chunk ids are updated in place. Chunks no longer present for a
        document are deleted, so a second load does not leave duplicates.
        """
        if len(chunks) != len(vectors):
            raise ValueError(f"{strategy}: {len(chunks)} chunks but {len(vectors)} vectors")
        grouped: dict[str, list[tuple[Chunk, list[float]]]] = {}
        for chunk, vector in zip(chunks, vectors):
            if len(vector) != self.dimensions:
                raise ValueError(
                    f"{chunk.chunk_id} has {len(vector)} dimensions, expected {self.dimensions}"
                )
            grouped.setdefault(chunk.document_id, []).append((chunk, vector))
        with self._connection.cursor() as cursor:
            for document_id, pairs in grouped.items():
                ids = [chunk.chunk_id for chunk, _ in pairs]
                cursor.execute(
                    """
                    DELETE FROM chunk_embeddings
                    WHERE strategy = %s AND document_id = %s AND NOT (chunk_id = ANY(%s))
                    """,
                    (strategy, document_id, ids),
                )
                for chunk, vector in pairs:
                    metadata = dict(chunk.metadata)
                    metadata["strategy"] = strategy
                    cursor.execute(
                        """
                        INSERT INTO chunk_embeddings (
                            strategy, chunk_id, document_id, source, page, section,
                            body, metadata, embedding
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                        ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                            document_id = EXCLUDED.document_id,
                            source = EXCLUDED.source,
                            page = EXCLUDED.page,
                            section = EXCLUDED.section,
                            body = EXCLUDED.body,
                            metadata = EXCLUDED.metadata,
                            embedding = EXCLUDED.embedding
                        """,
                        (
                            strategy,
                            chunk.chunk_id,
                            chunk.document_id,
                            chunk.source,
                            chunk.page,
                            metadata.get("section"),
                            chunk.text,
                            Json(metadata),
                            _vector_literal(vector),
                        ),
                    )
        self._connection.commit()

    def count(self, strategy: str) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM chunk_embeddings WHERE strategy = %s",
                (strategy,),
            )
            return int(cursor.fetchone()[0])

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter whitelisted metadata in SQL, then rank by cosine similarity."""
        if len(query_vector) != self.dimensions:
            raise ValueError(
                f"Query has {len(query_vector)} dimensions, expected {self.dimensions}"
            )
        if top_k < 1:
            raise ValueError("top_k must be positive")
        literal = _vector_literal(query_vector)
        clauses = ["strategy = %s"]
        params: list[Any] = [literal, strategy]
        for key, value in (filters or {}).items():
            if key not in _ALLOWED_FILTERS:
                raise ValueError(f"Unsupported filter: {key}")
            if value is None:
                continue
            clauses.append(f"{key} = %s")
            params.append(value)
        params.extend([literal, top_k])
        sql = f"""
            SELECT chunk_id, body, page, source, document_id, section, metadata,
                   1 - (embedding <=> %s::vector) AS score
            FROM chunk_embeddings
            WHERE {" AND ".join(clauses)}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        with self._connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        hits: list[dict[str, Any]] = []
        for row in rows:
            metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])
            hits.append(
                {
                    "chunk_id": row["chunk_id"],
                    "text": row["body"],
                    "score": float(row["score"]),
                    "document_id": row["document_id"],
                    "source": row["source"],
                    "page": int(row["page"]),
                    "metadata": metadata,
                }
            )
        return hits


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"
