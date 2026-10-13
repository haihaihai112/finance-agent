"""Additive runtime migrations. Back up the database and migrate with writers stopped.

Old pending rows have no trustworthy run association. Keep them visible as
interrupted orphans; never infer an association or replay a financial tool.
MySQL DDL commits independently, so each column addition is restart-safe.
"""

from sqlalchemy import JSON, String, column, inspect, select, table, text, update


def migrate_runtime_requests(engine):
    columns = {item["name"] for item in inspect(engine).get_columns("runtime_requests")}
    additions = {
        "run_id": "VARCHAR(64)",
        "status": "VARCHAR(32)",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    }
    with engine.begin() as connection:
        for name, sql_type in additions.items():
            if name not in columns:
                connection.execute(
                    text(f"ALTER TABLE runtime_requests ADD COLUMN {name} {sql_type}")
                )
        legacy_requests = table(
            "runtime_requests",
            column("user_id", String),
            column("request_id", String),
            column("result", JSON),
            column("status", String),
        )
        # Let the dialect deserialize JSON. SQL NULL and JSON null both become
        # None, independently of database JSON-vs-string comparison semantics.
        pending = (
            connection.execute(select(legacy_requests).where(legacy_requests.c.status.is_(None)))
            .mappings()
            .all()
        )
        for row in pending:
            connection.execute(
                update(legacy_requests)
                .where(
                    legacy_requests.c.user_id == row["user_id"],
                    legacy_requests.c.request_id == row["request_id"],
                    legacy_requests.c.status.is_(None),
                )
                .values(status="interrupted" if row["result"] is None else "completed")
            )
        connection.execute(
            text(
                "UPDATE runtime_requests SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
            )
        )
        connection.execute(
            text("UPDATE runtime_requests SET updated_at = created_at WHERE updated_at IS NULL")
        )
