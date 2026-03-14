import os
import time
from typing import List, Dict, Any, Optional
import litellm
from litellm import completion
from .config import settings, MAX_TOKENS_PER_LOOP
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

    def chat(self, messages: List[Dict[str, Any]], system: str = "", max_retries: int = 3) -> Any:
        """Send a chat request to the LLM with tool definitions and retry logic."""
        formatted_messages = []
        if system:
            formatted_messages.append({"role": "system", "content": system})
        formatted_messages.extend(messages)

        for attempt in range(1, max_retries + 1):
            try:
                response = completion(
                    model=self.model,
                    messages=formatted_messages,
                    tools=self.tools,
                )
                return response
            except Exception as e:
                error_str = str(e).lower()
                is_retryable = any(kw in error_str for kw in ["rate_limit", "timeout", "429", "503", "500"])
                
                if is_retryable and attempt < max_retries:
                    wait = 2 ** attempt  # exponential backoff: 2, 4, 8 seconds
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
        """Check if we've exceeded the hardcoded MAX_TOKENS_PER_LOOP."""
        return current_loop_tokens <= MAX_TOKENS_PER_LOOP
