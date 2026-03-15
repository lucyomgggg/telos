
from sentence_transformers import SentenceTransformer, util
from .logger import get_logger

log = get_logger("deduplicator")

_MODEL_CACHE = {}

class GoalDeduplicator:
    def __init__(self, threshold: float = 0.9, model_name: str = 'all-MiniLM-L6-v2'):
        self.threshold = threshold
        try:
            if model_name not in _MODEL_CACHE:
                _MODEL_CACHE[model_name] = SentenceTransformer(model_name)
            self.model = _MODEL_CACHE[model_name]
            log.info("GoalDeduplicator initialized with model: %s", model_name)
        except Exception as e:
            log.error("Failed to load SentenceTransformer: %s", e)
            self.model = None

    def is_duplicate(self, new_goal: str, past_goals: list[str]) -> bool:
        """
        Check if the new goal is too similar to any of the past goals.
        Returns True if a duplicate is found (similarity >= threshold).
        """
        if not self.model or not past_goals:
            return False
        
        # Encode goals
        new_emb = self.model.encode(new_goal, convert_to_tensor=True)
        past_embs = self.model.encode(past_goals, convert_to_tensor=True)
        
        # Calculate cosine similarities
        cosine_scores = util.cos_sim(new_emb, past_embs)[0]
        
        max_score = float(cosine_scores.max())
        log.debug("Max similarity score for new goal: %.4f", max_score)
        
        return max_score >= self.threshold
