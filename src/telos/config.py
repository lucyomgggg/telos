import os
import yaml
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional

# --- Paths ---
PROJECT_ROOT = Path.cwd()
LOCAL_CONFIG = PROJECT_ROOT / "config.yaml"
TELOS_HOME = Path(os.getenv("TELOS_HOME", Path.home() / ".telos"))
CONFIG_PATH = LOCAL_CONFIG if LOCAL_CONFIG.exists() else (TELOS_HOME / "config.yaml")

# Fallback for creating new config
if not CONFIG_PATH.exists():
    CONFIG_PATH = LOCAL_CONFIG

OUTPUTS_DIR = PROJECT_ROOT / "outputs"
LOG_FILE = TELOS_HOME / "agent.log"
PID_FILE = TELOS_HOME / "telos.pid"

class SecretSettings(BaseModel):
    openai_api_key: Optional[str] = Field(default=None, description="OpenAI API Key for embeddings/GPT models")
    gemini_api_key: Optional[str] = Field(default=None, description="Gemini API Key")
    anthropic_api_key: Optional[str] = Field(default=None, description="Anthropic API Key")

class LLMSettings(BaseModel):
    model: str = Field(default="gemini/gemini-2.0-flash", description="Default model (fallback)")
    producer_model: str = Field(default="gemini/gemini-2.0-flash", description="Model for the Producer agent")
    critic_model: str = Field(default="gemini/gemini-1.5-pro", description="Model for the Critic agent")
    embedding_model: str = Field(default="text-embedding-3-small", description="Model used for semantic memory")
    max_tokens_per_loop: int = Field(default=100000, description="Token limit per loop iteration")

class MemorySettings(BaseModel):
    qdrant_url: str = Field(default="http://localhost:6333", description="Qdrant vector store URL")
    collection_name: str = Field(default="telos_artifacts", description="Name of the Qdrant collection")

class SandboxSettings(BaseModel):
    image: str = Field(default="telos-sandbox:latest", description="Docker image for the sandbox")
    container_name: str = Field(default="telos-agent-sandbox", description="Name for the sandbox container")
    use_docker: bool = Field(default=True, description="Whether to use Docker or local execution")

class CriticSettings(BaseModel):
    rubric_path: Optional[str] = Field(default=None, description="Path to custom evaluation rubric JSON")

class LoggingSettings(BaseModel):
    level: str = Field(default="INFO", description="Console log level (DEBUG, INFO, WARNING, ERROR)")

class Settings(BaseModel):
    secrets: SecretSettings = Field(default_factory=SecretSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    critic: CriticSettings = Field(default_factory=CriticSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    daily_loop_limit: int = Field(default=10, description="Max loops per day")
    monthly_cost_limit: float = Field(default=50.0, description="Max USD budget per month")

    @classmethod
    def load(cls) -> "Settings":
        data = {}
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r") as f:
                    data = yaml.safe_load(f) or {}
            except Exception:
                pass
        
        # Load from data
        settings = cls(**data)
        
        # Environment Variable Overrides (Legacy & Convenience)
        if env_model := os.getenv("TELOS_MODEL"):
            settings.llm.model = env_model
        if env_embed := os.getenv("TELOS_EMBEDDING_MODEL"):
            settings.llm.embedding_model = env_embed
        if env_qdrant := os.getenv("QDRANT_URL"):
            settings.memory.qdrant_url = env_qdrant
        
        # Apply secrets to environment for litellm and other components
        if settings.secrets.openai_api_key:
            os.environ["OPENAI_API_KEY"] = settings.secrets.openai_api_key
        if settings.secrets.gemini_api_key:
            os.environ["GEMINI_API_KEY"] = settings.secrets.gemini_api_key
        if settings.secrets.anthropic_api_key:
            os.environ["ANTHROPIC_API_KEY"] = settings.secrets.anthropic_api_key
            
        return settings

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(self.model_dump(), f, sort_keys=False)

# Global settings instance
settings = Settings.load()

# Legacy constants for compatibility (can be refactored out later)
MAX_TOKENS_PER_LOOP = settings.llm.max_tokens_per_loop
DAILY_LOOP_LIMIT = settings.daily_loop_limit

def init_directories():
    TELOS_HOME.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        settings.save()

