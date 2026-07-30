from __future__ import annotations

import psycopg


def reset_test_database(postgres_url: str) -> None:
    plain_url = postgres_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(plain_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            database_name = cursor.fetchone()[0]
            if not database_name.startswith("atlas_baseline_test_"):
                raise RuntimeError(
                    "PostgreSQL tests require a dedicated atlas_baseline_test_* database."
                )
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public")
