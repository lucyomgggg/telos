
import os
import sys
from pathlib import Path

# Add src to path
sys.path.append(str(Path(os.getcwd()) / "src"))

from telos.memory import VectorStore
from telos.logger import get_logger

log = get_logger("verify_vector")

def verify_vector():
    vstore = VectorStore()
    if not vstore.available:
        log.warning("Qdrant not available. Skipping verification.")
        return

    log.info("Storing test vectors...")
    vstore.embed_and_store("The weather is sunny in Tokyo.", {"type": "weather", "city": "Tokyo"})
    vstore.embed_and_store("I love eating sushi.", {"type": "food"})
    
    log.info("Searching for 'Japanese cuisine'...")
    results = vstore.search_similar("Japanese cuisine", limit=2)
    for r in results:
        log.info(f"Match: {r['payload']} (Score: {r['score']:.4f})")

if __name__ == "__main__":
    verify_vector()
