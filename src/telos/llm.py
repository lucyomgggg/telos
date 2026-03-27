import os
import warnings
import logging

os.environ.setdefault("LITELLM_LOG", "ERROR")
warnings.filterwarnings("ignore", module="litellm")
logging.getLogger("litellm").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)

import time
import json
import re
from typing import List, Dict, Any, Optional, Type, TypeVar
import litellm
from litellm import completion
from pydantic import BaseModel
from .config import settings
from .logger import get_logger
from .utils import repair_json

log = get_logger("llm")

# Disable litellm telemetry and ensure compatibility
litellm.telemetry = False
litellm.drop_params = True

# Apply user-defined cost overrides for models not in litellm's database
def _apply_cost_overrides():
    try:
        overrides = settings.model_cost_overrides
        for model_id, costs in overrides.items():
            litellm.model_cost[model_id] = {
                "input_cost_per_token": costs.get("input_cost_per_million", 0) / 1_000_000,
                "output_cost_per_token": costs.get("output_cost_per_million", 0) / 1_000_000,
            }
            log.debug("Registered custom cost for model '%s'", model_id)
    except Exception as e:
        log.warning("Failed to apply model_cost_overrides: %s", e)

_apply_cost_overrides()

T = TypeVar("T", bound=BaseModel)

class LLMService:
    def __init__(self, model: Optional[str] = None, cost_tracker: Any = None):
        self.model = model or settings.llm.model
        self.cost_tracker = cost_tracker
        log.debug("LLM initialized with model: %s", self.model)
        
        # Tools initialized on demand to avoid circular imports
        self._tools = None

    @property
    def tools(self):
        if self._tools is None:
            from .tools import get_standard_tool_definitions
            self._tools = get_standard_tool_definitions()
        return self._tools

    def chat(self, 
             messages: List[Dict[str, Any]], 
             system: str = "", 
             max_retries: int = 5, 
             loop_id: str = "system",
             agent_type: str = "other",
             **kwargs) -> Any:
        """Send a chat request with robust retry and automatic cost tracking."""
        formatted_messages = []
        if system:
            formatted_messages.append({"role": "system", "content": system})
        formatted_messages.extend(messages)

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
                
                if self.cost_tracker:
                    self.cost_tracker.record_usage(response, agent_type, loop_id)
                
                return response
            except Exception as e:
                if self._handle_error(e, attempt, max_retries):
                    continue
                raise

    def chat_structured(self, 
                        messages: List[Dict[str, Any]], 
                        response_model: Type[T],
                        system: str = "",
                        loop_id: str = "system",
                        agent_type: str = "other",
                        max_retries: int = 3) -> T:
        """Send a chat request and force the response into a Pydantic model."""
        
        # Create a tool definition for the schema if the model doesn't support json_mode effectively
        tool_name = f"submit_{response_model.__name__.lower()}"
        structured_tool = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": f"Submit structured data for {response_model.__name__}",
                "parameters": response_model.model_json_schema()
            }
        }

        for attempt in range(1, max_retries + 1):
            try:
                response = self.chat(
                    messages=messages,
                    system=system,
                    loop_id=loop_id,
                    agent_type=agent_type,
                    tools=[structured_tool],
                    tool_choice={"type": "function", "function": {"name": tool_name}}
                )

                msg = response.choices[0].message
                raw_args = None

                if msg.tool_calls:
                    raw_args = msg.tool_calls[0].function.arguments
                elif msg.content:
                    # Fallback: extract JSON from content
                    match = re.search(r'\{.*\}', msg.content, re.DOTALL)
                    if match:
                        raw_args = match.group()
                
                if not raw_args:
                    raise ValueError("No structured data found in LLM response")

                repaired = repair_json(raw_args)
                data = json.loads(repaired)
                
                # Handle nested 'arguments' or 'evaluation'/'scores' if LLM wrapped it
                if "arguments" in data and isinstance(data["arguments"], dict):
                    data = data["arguments"]
                
                # If Critic nested it into 'scores' against instructions (common fallback)
                if "scores" in data and isinstance(data["scores"], dict):
                    data.update(data.pop("scores"))
                if "evaluation" in data and isinstance(data["evaluation"], dict):
                    data.update(data.pop("evaluation"))

                # Normalize: if response_model expects a 'scores' dict but LLM returned flat
                # numeric fields (common — LLMs naturally flatten tool parameters), collect them.
                if "scores" in response_model.model_fields and "scores" not in data:
                    known_keys = set(response_model.model_fields.keys()) - {"scores"}
                    score_candidates = {
                        k: float(v) for k, v in list(data.items())
                        if k not in known_keys and isinstance(v, (int, float))
                    }
                    if score_candidates:
                        for k in score_candidates:
                            data.pop(k)
                        data["scores"] = score_candidates

                return response_model(**data)

            except Exception as e:
                log.warning(f"Structured chat attempt {attempt} failed: {e}")
                if attempt == max_retries:
                    raise
                time.sleep(2)

    def _handle_error(self, e: Exception, attempt: int, max_retries: int) -> bool:
        error_str = str(e).lower()
        is_fatal_quota = any(kw in error_str for kw in ["spending cap", "budget exceeded", "quota exceeded"])
        if is_fatal_quota:
            log.error("Fatal API Quota Error: %s. Stopping immediately.", e)
            return False

        is_rate_limit = any(kw in error_str for kw in ["rate_limit", "429"])
        is_retryable = is_rate_limit or any(kw in error_str for kw in ["timeout", "503", "500"])
        
        if is_retryable and attempt < max_retries:
            wait = min((2 ** (attempt - 1)) * 5, 60)
            if is_rate_limit:
                wait += 10
                log.warning("Rate limit (429) hit (attempt %d/%d), waiting %ds: %s", attempt, max_retries, wait, e)
            else:
                log.warning("LLM call failed (attempt %d/%d), retrying in %ds: %s", attempt, max_retries, wait, e)
            time.sleep(wait)
            return True
        else:
            log.error("LLM call failed permanently: %s", e)
            return False

    def validate_token_limit(self, current_loop_tokens: int) -> bool:
        return current_loop_tokens <= settings.llm.max_tokens_per_loop
