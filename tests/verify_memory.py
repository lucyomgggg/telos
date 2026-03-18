
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add src to path
sys.path.append(str(Path(os.getcwd()) / "src"))

from telos.memory import MemoryStore
from telos.db_models import LoopRecord
from telos.logger import get_logger

log = get_logger("verify_memory")

def verify_memory():
    # Use a temporary db for testing
    db_path = "test_telos.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    
    memory = MemoryStore(db_path=db_path)
    
    # 1. Test Saving and History Retrieval
    log.info("Saving 25 loops...")
    for i in range(25):
        loop_id = str(uuid.uuid4())
        memory.save_loop({
            "id": loop_id,
            "goal": f"Goal {i}",
            "score": float(i) / 10.0,
            "cost_usd": 0.01,
            "status": "completed"
        })
    
    history = memory.get_recent_history(limit=20)
    log.info("Retrieved history length: %d", len(history))
    if len(history) == 20:
        log.info("SUCCESS: Retrieved exactly 20 history records.")
    else:
        log.error("FAILURE: Retrieved %d records instead of 20.", len(history))

    log.info("First history goal: %s", history[0]["goal"])
    log.info("Last history goal: %s", history[-1]["goal"])
    
    if history[0]["goal"] == "Goal 5" and history[-1]["goal"] == "Goal 24":
        log.info("SUCCESS: History is in correct chronological order (Goal 5 to Goal 24).")
    else:
        log.error("FAILURE: History order or content incorrect.")

    # 2. Test Spend Calculation
    since = datetime.now(timezone.utc) - timedelta(days=1)
    total_spend = memory.get_total_spend(since)
    log.info("Total spend since yesterday: $%.2f", total_spend)
    if abs(total_spend - 0.25) < 0.001:
        log.info("SUCCESS: Spend calculation correct.")
    else:
        log.error("FAILURE: Spend calculation incorrect ($%.2f instead of $0.25).", total_spend)

    # Cleanup
    if os.path.exists(db_path):
        os.remove(db_path)

if __name__ == "__main__":
    verify_memory()
