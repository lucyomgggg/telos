"""
Migration: add instinct-related columns to loops + create instinct_states table.

Idempotent — safe to run multiple times.
Run: python -m telos.migrations.add_instincts
"""
import sqlite3
from pathlib import Path


def _resolve_db_path() -> Path:
    try:
        from ..config import TELOS_HOME
        return Path(TELOS_HOME) / "telos.db"
    except ImportError:
        return Path("data/telos.db")


def run(db_path: str = None) -> None:
    path = Path(db_path) if db_path else _resolve_db_path()
    if not path.exists():
        print(f"Database not found at {path}. Nothing to migrate.")
        return

    conn = sqlite3.connect(str(path))
    cur = conn.cursor()

    # 1. Add new columns to loops table
    existing_cols = [row[1] for row in cur.execute("PRAGMA table_info(loops)")]
    new_columns = [
        ("exit_code", "INTEGER"),
        ("execution_time_ms", "INTEGER"),
        ("memory_peak_bytes", "INTEGER"),
        ("loc", "INTEGER"),
        ("function_count", "INTEGER"),
        ("import_count", "INTEGER"),
        ("builds_on_previous", "BOOLEAN"),
    ]
    for col_name, col_type in new_columns:
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE loops ADD COLUMN {col_name} {col_type}")
            print(f"Added {col_name} column to loops table.")

    # 2. Create instinct_states table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS instinct_states (
            loop_id VARCHAR PRIMARY KEY REFERENCES loops(id),
            curiosity REAL,
            preservation REAL,
            growth REAL,
            order_drive REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    print("instinct_states table ensured.")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    run()
