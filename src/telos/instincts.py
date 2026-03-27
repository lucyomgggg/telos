"""
Instinct Engine — replaces the Critic with environment-derived feedback signals.

Each instinct outputs a normalized float [0.0, 1.0]. Higher = stronger drive (more hunger/need).
The 4 instincts create natural tension that Goal Gen must navigate:
  - Curiosity vs Preservation (explore vs stabilize)
  - Growth vs Order (push complexity vs consolidate)
"""

import math
import re
from typing import Dict, List, Optional

from .logger import get_logger

log = get_logger("instincts")


def _sigmoid(x: float) -> float:
    """Standard sigmoid, clamped to avoid overflow."""
    x = max(-10.0, min(10.0, x))
    return 1.0 / (1.0 + math.exp(-x))


class InstinctEngine:
    def __init__(self, vector_store, memory_store, config=None):
        self.vector = vector_store
        self.sqlite = memory_store
        self.window = 10  # lookback window for preservation/growth

    # ------------------------------------------------------------------
    # 1. Curiosity (新規性飢餓)
    # ------------------------------------------------------------------
    def compute_curiosity(self, output_embedding: Optional[List[float]]) -> float:
        """How novel is recent output relative to the knowledge base?
        High = well-explored territory → hungry for novelty.
        Low = output is already novel.
        """
        if output_embedding is None or not self.vector.available:
            return 0.5

        try:
            results = self.vector.client.query_points(
                collection_name=self.vector.collection_name,
                query=output_embedding,
                limit=10,
            ).points

            if len(results) < 10:
                return 0.5  # not enough data to judge

            mean_sim = sum(r.score for r in results) / len(results)
            return round(1.0 - mean_sim, 4)
        except Exception as e:
            log.warning("Curiosity computation failed: %s", e)
            return 0.5

    # ------------------------------------------------------------------
    # 2. Self-Preservation (システム安定性)
    # ------------------------------------------------------------------
    def compute_preservation(self) -> float:
        """How stable is the system's recent execution?
        High = unstable, crashing → drive to be conservative.
        Low = healthy.
        """
        session = self.sqlite.Session()
        try:
            from .db_models import LoopRecord
            recent = (
                session.query(LoopRecord)
                .order_by(LoopRecord.created_at.desc())
                .limit(self.window)
                .all()
            )
            if not recent:
                return 0.5

            n = len(recent)
            crashes = sum(
                1 for r in recent
                if r.exit_code is not None and r.exit_code != 0
            )
            timeouts = sum(
                1 for r in recent
                if r.status == "timeout"
            )
            # Also count loops that were aborted or failed as instability signals
            failures = sum(
                1 for r in recent
                if r.status == "failed"
            )

            crash_rate = (crashes + failures) / n
            timeout_rate = timeouts / n
            return round(crash_rate * 0.7 + timeout_rate * 0.3, 4)
        except Exception as e:
            log.warning("Preservation computation failed: %s", e)
            return 0.5
        finally:
            session.close()

    # ------------------------------------------------------------------
    # 3. Growth (成長軌道)
    # ------------------------------------------------------------------
    def compute_growth(self, current_stats: Optional[Dict] = None) -> float:
        """Is output complexity increasing over time?
        High = stalled → drive to push harder.
        Low = already growing well.
        """
        session = self.sqlite.Session()
        try:
            from .db_models import LoopRecord
            recent = (
                session.query(LoopRecord)
                .order_by(LoopRecord.created_at.desc())
                .limit(self.window)
                .all()
            )
            if len(recent) < 6:
                return 0.5

            # Split into two halves: recent 5 vs prior 5
            newer = recent[:5]
            older = recent[5:10]

            def avg_complexity(records):
                total = 0.0
                count = 0
                for r in records:
                    loc = r.loc or 0
                    funcs = r.function_count or 0
                    imports = r.import_count or 0
                    # Simple composite: loc + 5*functions + 3*imports
                    total += loc + 5 * funcs + 3 * imports
                    count += 1
                return total / count if count > 0 else 0.0

            newer_avg = avg_complexity(newer)
            older_avg = avg_complexity(older)

            if older_avg == 0:
                return 0.5

            relative_change = (newer_avg - older_avg) / max(older_avg, 1.0)
            # Sigmoid: negative change → high drive, positive change → low drive
            return round(1.0 - _sigmoid(relative_change * 3), 4)
        except Exception as e:
            log.warning("Growth computation failed: %s", e)
            return 0.5
        finally:
            session.close()

    # ------------------------------------------------------------------
    # 4. Order (知識構造)
    # ------------------------------------------------------------------
    def compute_order(self) -> float:
        """How well-organized is the accumulated knowledge?
        High = fragmented/scattered → drive to consolidate.
        Low = well-structured.
        """
        if not self.vector.available:
            return 0.5

        try:
            # Get all points from Qdrant
            points, _offset = self.vector.client.scroll(
                collection_name=self.vector.collection_name,
                limit=200,
                with_vectors=True,
            )

            if len(points) < 5:
                return 0.5

            # Simple clustering: compute pairwise similarities and identify isolated points
            vectors = [p.vector for p in points]
            n = len(vectors)

            # For each point, find its max similarity to any other point
            similarity_threshold = 0.7
            clustered = 0
            isolated = 0

            for i in range(n):
                max_sim = 0.0
                for j in range(n):
                    if i == j:
                        continue
                    # Cosine similarity (vectors are already normalized by Qdrant)
                    sim = sum(a * b for a, b in zip(vectors[i], vectors[j]))
                    if sim > max_sim:
                        max_sim = sim
                if max_sim >= similarity_threshold:
                    clustered += 1
                else:
                    isolated += 1

            noise_ratio = isolated / n
            # Simplified: just use noise_ratio as the order drive
            return round(min(noise_ratio * 1.5, 1.0), 4)
        except Exception as e:
            log.warning("Order computation failed: %s", e)
            return 0.5

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def compute_state(
        self,
        output_embedding: Optional[List[float]] = None,
        output_stats: Optional[Dict] = None,
    ) -> Dict[str, float]:
        """Compute full instinct state. Called after each loop's Docker execution."""
        state = {
            "curiosity": self.compute_curiosity(output_embedding),
            "preservation": self.compute_preservation(),
            "growth": self.compute_growth(output_stats),
            "order": self.compute_order(),
        }
        log.info(
            "Instinct state: curiosity=%.2f preservation=%.2f growth=%.2f order=%.2f",
            state["curiosity"], state["preservation"],
            state["growth"], state["order"],
        )
        return state


# ------------------------------------------------------------------
# Output stats extraction (used by Orchestrator after Docker execution)
# ------------------------------------------------------------------
def extract_output_stats(content: str) -> Dict:
    """Extract complexity metrics from artifact content.
    Uses regex over AST for simplicity — good enough for instinct signals.
    """
    if not content:
        return {"loc": 0, "function_count": 0, "import_count": 0, "builds_on_previous": False}

    lines = content.split("\n")
    # LOC: non-blank, non-comment lines
    loc = sum(1 for line in lines if line.strip() and not line.strip().startswith("#"))

    # Function/class definitions
    function_count = len(re.findall(r"^\s*(?:def|class|function|const\s+\w+\s*=\s*(?:async\s+)?(?:\(|function))\b", content, re.MULTILINE))

    # Import statements
    import_count = len(re.findall(r"^\s*(?:import |from \S+ import |require\(|const .+ = require)", content, re.MULTILINE))

    # Does it reference previous loop outputs? (heuristic: imports from workspace paths)
    builds_on_previous = bool(re.search(r"(?:open|read|import|require|from)\s*['\"].*(?:workspace|persistent|/[a-f0-9]{8}/)", content))

    return {
        "loc": loc,
        "function_count": function_count,
        "import_count": import_count,
        "builds_on_previous": builds_on_previous,
    }
