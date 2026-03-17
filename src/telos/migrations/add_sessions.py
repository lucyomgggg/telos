"""
Migration: add sessions table and session_id column to loops.

Idempotent — safe to run multiple times.
Run once against an existing database:
    python -m telos.migrations.add_sessions
"""
import uuid
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

    # 1. Create sessions table if it does not exist
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id VARCHAR PRIMARY KEY,
            name VARCHAR,
            created_at DATETIME,
            completed_at DATETIME,
            producer_model VARCHAR,
            critic_model VARCHAR,
            goal_gen_model VARCHAR,
            intended_loops INTEGER DEFAULT 0,
            completed_loops INTEGER DEFAULT 0,
            status VARCHAR DEFAULT 'running',
            total_cost_usd FLOAT DEFAULT 0.0,
            avg_score FLOAT
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_sessions_created_at ON sessions(created_at)"
    )

    # 2. Add session_id column to loops if missing
    existing_cols = [row[1] for row in cur.execute("PRAGMA table_info(loops)")]
    if "session_id" not in existing_cols:
        cur.execute("ALTER TABLE loops ADD COLUMN session_id VARCHAR")
        print("Added session_id column to loops table.")
    else:
        print("session_id column already exists in loops table.")

    # 3. Create a legacy session for all existing loops that lack a session_id
    row = cur.execute(
        "SELECT MIN(created_at), MAX(created_at), COUNT(*), SUM(cost_usd), AVG(score) "
        "FROM loops WHERE session_id IS NULL"
    ).fetchone()
    min_ts, max_ts, count, total_cost, avg_score = row

    if count and count > 0:
        legacy_id = str(uuid.uuid4())
        cur.execute("""
            INSERT OR IGNORE INTO sessions
              (id, name, created_at, completed_at, producer_model, critic_model,
               goal_gen_model, intended_loops, completed_loops, status, total_cost_usd, avg_score)
            VALUES (?, 'legacy', ?, ?, 'unknown', 'unknown', NULL, ?, ?, 'completed', ?, ?)
        """, (
            legacy_id, min_ts, max_ts,
            count, count,
            round(total_cost or 0.0, 6),
            round(avg_score or 0.0, 4),
        ))
        cur.execute(
            "UPDATE loops SET session_id = ? WHERE session_id IS NULL",
            (legacy_id,)
        )
        print(f"Migrated {count} existing loop(s) into legacy session {legacy_id[:8]}.")
    else:
        print("No unassigned loops found — nothing to migrate into legacy session.")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    run()
