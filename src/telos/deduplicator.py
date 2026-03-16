
from sentence_transformers import SentenceTransformer, util
from .logger import get_logger

log = get_logger("deduplicator")

_MODEL_CACHE = {}
_LOCAL_FALLBACK = 'all-MiniLM-L6-v2'

class GoalDeduplicator:
    def __init__(self, threshold: float = None, model_name: str = None):
        from .config import settings
        cfg = settings.load()
        self.threshold = threshold if threshold is not None else cfg.deduplication_threshold

        # Use the configured embedding model. API-style models (containing '/')
        # cannot be used locally by sentence-transformers, so fall back to the
        # default local model in that case.
        resolved = model_name or cfg.memory.embedding_model
        if '/' in resolved and not resolved.startswith('sentence-transformers/'):
            log.info(
                "API embedding model '%s' is not usable locally for deduplication; "
                "falling back to %s.", resolved, _LOCAL_FALLBACK
            )
            resolved = _LOCAL_FALLBACK

        try:
            if resolved not in _MODEL_CACHE:
                _MODEL_CACHE[resolved] = SentenceTransformer(resolved)
            self.model = _MODEL_CACHE[resolved]
            log.info("GoalDeduplicator initialized with model: %s", resolved)
        except Exception as e:
            log.error("Failed to load SentenceTransformer: %s", e)
            self.model = None

    @staticmethod
    def _dynamic_threshold(loop_count: int, base: float, floor: float = 0.65) -> float:
        """Relax the similarity threshold as loop count grows.

        As memory accumulates the embedding space becomes denser, so a static threshold
        would progressively shrink the explorable search space.  Decay rate: -0.0005 per
        loop (100 loops → ~0.80, 200 loops → ~0.75) down to a minimum of `floor`.
        """
        decay = min(base - floor, loop_count * 0.0002)
        return round(max(floor, base - decay), 4)

    def is_duplicate(self, new_goal: str, past_goals: list[str], loop_count: int = 0) -> bool:
        """Check if the new goal is too similar to any of the past goals.

        Args:
            new_goal: The candidate goal title.
            past_goals: List of previously accepted goal titles.
            loop_count: Total number of completed loops so far.  Used to dynamically
                relax the similarity threshold as memory grows.

        Returns:
            True if a duplicate is found (similarity >= effective threshold).
        """
        if not self.model or not past_goals:
            return False

        effective_threshold = self._dynamic_threshold(loop_count, self.threshold)
        log.debug("Dedup threshold: base=%.2f, loop_count=%d, effective=%.4f",
                  self.threshold, loop_count, effective_threshold)

        new_emb = self.model.encode(new_goal, convert_to_tensor=True)
        past_embs = self.model.encode(past_goals, convert_to_tensor=True)

        cosine_scores = util.cos_sim(new_emb, past_embs)[0]
        max_score = float(cosine_scores.max())
        log.debug("Max similarity score for new goal: %.4f (threshold=%.4f)", max_score, effective_threshold)

        return max_score >= effective_threshold
