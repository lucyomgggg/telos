import os
import time
from typing import List, Dict, Any, Optional
import litellm
from litellm import completion
from .config import settings
from .logger import get_logger

log = get_logger("llm")

# Disable litellm telemetry
litellm.telemetry = False

class LLMInterface:
    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.llm.model
        log.debug("LLM initialized with model: %s", self.model)
        
        # Tools available to the agent
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "execute_command",
                    "description": "Execute a shell command inside the secure sandbox environment.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The shell command to execute."
                            }
                        },
                        "required": ["command"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write text content to a file in the sandbox workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The destination path relative to the workspace."
                            },
                            "content": {
                                "type": "string",
                                "description": "The text content to write."
                            }
                        },
                        "required": ["path", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the contents of a file from the sandbox workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "The path to the file relative to the workspace."
                            }
                        },
                        "required": ["path"]
                    }
                }
            }
        ]

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
        """Calculate approximate cost using litellm's cost tracking."""
        try:
            cost = litellm.completion_cost(
                model=self.model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens
            )
            return cost or 0.0
        except Exception:
            return 0.0

    def validate_token_limit(self, current_loop_tokens: int) -> bool:
        """Check if we've exceeded the configured max_tokens_per_loop."""
        return current_loop_tokens <= settings.llm.max_tokens_per_loop
