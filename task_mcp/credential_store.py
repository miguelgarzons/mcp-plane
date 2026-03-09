from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
from psycopg2 import sql
from psycopg2.errors import InsufficientPrivilege
from psycopg2.pool import SimpleConnectionPool


class CredentialStore:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        schema: str = "mcp",
    ) -> None:
        self.schema = schema.strip() or "mcp"
        self.table_name = "plane_user_credentials"
        self._pool = SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            host=host,
            port=port,
            dbname=database,
            user=user,
            password=password,
        )
        self._ensure_schema_and_table()

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        conn = self._pool.getconn()
        try:
            yield conn
        finally:
            self._pool.putconn(conn)

    def _ensure_schema_and_table(self) -> None:
        with self._connection() as conn:
            with conn.cursor() as cursor:
                try:
                    cursor.execute(
                        sql.SQL("CREATE SCHEMA IF NOT EXISTS {};").format(
                            sql.Identifier(self.schema)
                        )
                    )
                except InsufficientPrivilege:
                    conn.rollback()
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {}.{} (
                            user_id TEXT PRIMARY KEY,
                            plane_api_token TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        );
                        """
                    ).format(
                        sql.Identifier(self.schema),
                        sql.Identifier(self.table_name),
                    )
                )
            conn.commit()

    def upsert_plane_credentials(self, user_id: str, api_token: str) -> dict[str, str]:
        cleaned_user_id = user_id.strip()
        if not cleaned_user_id:
            raise ValueError("user_id is required")

        cleaned_token = api_token.strip()
        if not cleaned_token:
            raise ValueError("plane_api_token is required")

        with self._connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {}.{} (user_id, plane_api_token)
                        VALUES (%s, %s)
                        ON CONFLICT (user_id)
                        DO UPDATE SET
                            plane_api_token = EXCLUDED.plane_api_token,
                            updated_at = NOW();
                        """
                    ).format(
                        sql.Identifier(self.schema),
                        sql.Identifier(self.table_name),
                    ),
                    (cleaned_user_id, cleaned_token),
                )
            conn.commit()
        return {"user_id": cleaned_user_id}

    def get_plane_credentials(self, user_id: str) -> dict[str, str] | None:
        cleaned_user_id = user_id.strip()
        if not cleaned_user_id:
            return None

        with self._connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT plane_api_token FROM {}.{} WHERE user_id = %s"
                    ).format(
                        sql.Identifier(self.schema),
                        sql.Identifier(self.table_name),
                    ),
                    (cleaned_user_id,),
                )
                row = cursor.fetchone()
        if not row:
            return None
        return {"api_token": str(row[0]).strip()}

    def delete_plane_credentials(self, user_id: str) -> bool:
        cleaned_user_id = user_id.strip()
        if not cleaned_user_id:
            return False

        with self._connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DELETE FROM {}.{} WHERE user_id = %s").format(
                        sql.Identifier(self.schema),
                        sql.Identifier(self.table_name),
                    ),
                    (cleaned_user_id,),
                )
                deleted = cursor.rowcount > 0
            conn.commit()
        return deleted

    def list_users(self) -> list[str]:
        with self._connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    sql.SQL("SELECT user_id FROM {}.{} ORDER BY user_id ASC").format(
                        sql.Identifier(self.schema),
                        sql.Identifier(self.table_name),
                    )
                )
                rows = cursor.fetchall()
        return [str(row[0]).strip() for row in rows if row and str(row[0]).strip()]
