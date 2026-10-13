"""Additive runtime migrations. Back up the database and migrate with writers stopped.

Old pending rows have no trustworthy run association. Keep them visible as
interrupted orphans; never infer an association or replay a financial tool.
MySQL DDL commits independently, so each column addition is restart-safe.
"""

from sqlalchemy import inspect, text


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
        connection.execute(
            text(
                "UPDATE runtime_requests SET status = CASE WHEN result IS NULL OR result = 'null' THEN 'interrupted' ELSE 'completed' END WHERE status IS NULL"
            )
        )
        connection.execute(
            text(
                "UPDATE runtime_requests SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
            )
        )
        connection.execute(
            text("UPDATE runtime_requests SET updated_at = created_at WHERE updated_at IS NULL")
        )
