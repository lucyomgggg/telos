import os
import time
from typing import List, Dict, Any, Optional
import litellm
from litellm import completion
from .config import settings
from .logger import get_logger

log = get_logger("llm")

# Disable litellm telemetry and ensure compatibility
litellm.telemetry = False
litellm.drop_params = True

class LLMInterface:
    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.llm.model
        log.debug("LLM initialized with model: %s", self.model)
        
        # Tools available to the agent - decentralized to tools.py
        from .tools import get_standard_tool_definitions
        self.tools = get_standard_tool_definitions()

    def chat(self, messages: List[Dict[str, Any]], system: str = "", max_retries: int = 5, **kwargs) -> Any:
        """Send a chat request to the LLM with tool definitions and robust retry logic."""
        formatted_messages = []
        if system:
            formatted_messages.append({"role": "system", "content": system})
        formatted_messages.extend(messages)

        # Default tools if not provided in kwargs and not using json_object response format
        # Gemini does not support both simultaneously
        is_json_mode = kwargs.get("response_format", {}).get("type") == "json_object"
        
        if "tools" not in kwargs and not is_json_mode:
            kwargs["tools"] = self.tools
        if "tool_choice" not in kwargs and kwargs.get("tools"):
            kwargs["tool_choice"] = "auto"

        for attempt in range(1, max_retries + 1):
            try:
                response = completion(
                    model=self.model,
                    messages=formatted_messages,
                    **kwargs
                )
                return response
            except Exception as e:
                error_str = str(e).lower()
                
                # Check for fatal, non-retryable quota errors
                is_fatal_quota = any(kw in error_str for kw in ["spending cap", "budget exceeded", "quota exceeded"])
                if is_fatal_quota:
                    log.error("Fatal API Quota Error: %s. Stopping immediately.", e)
                    raise

                # 429 is the key rate limit error
                is_rate_limit = any(kw in error_str for kw in ["rate_limit", "429"])
                is_retryable = is_rate_limit or any(kw in error_str for kw in ["timeout", "503", "500"])
                
                if is_retryable and attempt < max_retries:
                    # Exponential backoff: 5s, 10s, 20s, 40s, 80s
                    # Gemini free tier has a 10 RPM limit, so we need significant backoff
                    wait = (2 ** (attempt - 1)) * 5
                    if is_rate_limit:
                        # Add jitter and ensure substantial wait for 429
                        wait += 10
                        log.warning("Rate limit (429) hit (attempt %d/%d), waiting %ds: %s", 
                                   attempt, max_retries, wait, e)
                    else:
                        log.warning("LLM call failed (attempt %d/%d), retrying in %ds: %s", 
                                   attempt, max_retries, wait, e)
                    time.sleep(wait)
                else:
                    log.error("LLM call failed permanently: %s", e)
                    raise
    
    def calculate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate approximate cost using litellm's cost tracking with fallbacks."""
        try:
            cost = litellm.completion_cost(
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens
            )
            if cost is not None and cost > 0:
                return cost
                
            # Fallback for models not yet in litellm's local database (estimated 0.0)
            # Use a conservative average cost if it's a known expensive model family
            # or a very small default for local models
            if "ollama" in self.model:
                return 0.0 # Local is free
            
            # Default fallback for unknown paid models (e.g. $1 per 1M tokens)
            return (prompt_tokens + completion_tokens) * 0.000001
        except Exception as e:
            log.warning("Cost calculation fallback triggered for %s: %s", self.model, e)
            return 0.0

    def validate_token_limit(self, current_loop_tokens: int) -> bool:
        """Check if we've exceeded the configured max_tokens_per_loop."""
        return current_loop_tokens <= settings.llm.max_tokens_per_loop
