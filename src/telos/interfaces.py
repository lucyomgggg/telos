from abc import ABC, abstractmethod
from typing import Dict, Any
from .config import TEMPLATES_DIR
from .logger import get_logger

log = get_logger("base")


class Tool(ABC):
    @abstractmethod
    def execute(self, params: Dict[str, Any]) -> str:
        """Execute the tool's core logic."""
        pass

    @property
    @abstractmethod
    def definition(self) -> Dict[str, Any]:
        """Returns the litellm-compatible tool definition."""
        pass


class TemplateLoader:
    @staticmethod
    def load(template_name: str, fallback_text: str = "") -> str:
        template_path = TEMPLATES_DIR / f"{template_name}.txt"
        if template_path.exists():
            return template_path.read_text().strip()
        log.warning(f"Template {template_name} not found. Using fallback.")
        return fallback_text
