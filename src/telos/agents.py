from typing import Any, Dict, List, Optional, Type, TypeVar
from pydantic import BaseModel
from .llm import LLMService
from .config import settings
from .interfaces import TemplateLoader
from .logger import get_logger

T = TypeVar("T", bound=BaseModel)

class BaseAgent:
    def __init__(self, agent_type: str, model: Optional[str] = None, cost_tracker: Any = None):
        self.agent_type = agent_type
        self.settings = settings.load()
        self.cost_tracker = cost_tracker
        self.log = get_logger(agent_type)
        self.templates = TemplateLoader()
        
        # Centralized LLM Service
        selected_model = model or getattr(self.settings.llm, f"{agent_type}_model", self.settings.llm.model)
        self.llm = LLMService(model=selected_model, cost_tracker=self.cost_tracker)

    def chat(self, messages: List[Dict[str, Any]], system: str = "", **kwargs) -> Any:
        return self.llm.chat(
            messages=messages,
            system=system,
            agent_type=self.agent_type,
            **kwargs
        )

    def chat_structured(self, messages: List[Dict[str, Any]], response_model: Type[T], system: str = "", **kwargs) -> T:
        return self.llm.chat_structured(
            messages=messages,
            response_model=response_model,
            system=system,
            agent_type=self.agent_type,
            **kwargs
        )

    def load_template(self, template_name: str, fallback: str = "") -> str:
        return self.templates.load(template_name, fallback)
