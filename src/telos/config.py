import os
import yaml
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional
import logging

# --- Cache ---
_settings_cache = None

# --- Paths ---
PROJECT_ROOT = Path.cwd()
LOCAL_CONFIG = PROJECT_ROOT / "config.yaml"
# Default to project-local data folder
TELOS_HOME = Path(os.getenv("TELOS_HOME", PROJECT_ROOT / "data"))
def _safe_exists(p: Path) -> bool:
    try:
        return p.exists()
    except PermissionError:
        return False

CONFIG_PATH = LOCAL_CONFIG if _safe_exists(LOCAL_CONFIG) else (TELOS_HOME / "config.yaml")

# Fallback for creating new config
if not _safe_exists(CONFIG_PATH):
    CONFIG_PATH = LOCAL_CONFIG

OUTPUTS_DIR = PROJECT_ROOT / "outputs"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
LOG_FILE = TELOS_HOME / "agent.log"
PID_FILE = TELOS_HOME / "telos.pid"

class LLMSettings(BaseModel):
    model: str = Field(default="gemini/gemini-flash-latest", description="Default model (fallback)")
    producer_model: str = Field(default="gemini/gemini-flash-latest", description="Model for the Producer agent")
    critic_model: str = Field(default="gemini/gemini-flash-latest", description="Model for the Critic agent")
    goal_gen_model: Optional[str] = Field(default=None, description="Dedicated model for goal generation (falls back to producer_model if None)")
    max_tokens_per_loop: int = Field(default=8000, description="Token limit per loop iteration")

class MemorySettings(BaseModel):
    qdrant_url: str = Field(default="http://localhost:6333", description="Qdrant vector store URL")
    collection_name: str = Field(default="telos_artifacts", description="Name of the Qdrant collection")
    embedding_model: str = Field(default="all-MiniLM-L6-v2", description="Model used for semantic memory")
    embedding_dimensions: Optional[int] = Field(
        default=None,
        description="Vector dimensions for the embedding model. Auto-detected from model name if None."
    )
    workspace_path: str = Field(default="workspace", description="Path to the agent workspace")

class SandboxSettings(BaseModel):
    image: str = Field(default="telos-sandbox:latest", description="Docker image for the sandbox")
    container_name: str = Field(default="telos-agent-sandbox", description="Name for the sandbox container")
    use_docker: bool = Field(default=True, description="Whether to use Docker or local execution")
    memory_limit: str = Field(default="512m", description="Docker memory limit (e.g., 512m, 1g)")
    timeout: int = Field(default=300, description="Hard timeout for sandbox commands in seconds")

class CriticSettings(BaseModel):
    rubric_path: Optional[str] = Field(default=None, description="Path to custom evaluation rubric JSON")

class LoggingSettings(BaseModel):
    level: str = Field(default="INFO", description="Console log level (DEBUG, INFO, WARNING, ERROR)")

class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    critic: CriticSettings = Field(default_factory=CriticSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    daily_loop_limit: int = Field(default=10, description="Max loops per day")
    monthly_cost_limit: float = Field(default=50.0, description="Max USD budget per month")
    rate_limit_delay: float = Field(default=6.0, description="Seconds to wait between LLM calls")
    deduplication_threshold: float = Field(default=0.85, description="Similarity threshold for goal deduplication")
    max_steps: int = Field(default=15, description="Maximum steps per loop execution")
    consecutive_error_limit: int = Field(default=3, description="Abort after N consecutive tool errors")
    max_output_truncation: int = Field(default=8000, description="Truncate tool outputs longer than this")

    @classmethod
    def load(cls) -> "Settings":
        global _settings_cache
        if _settings_cache:
            return _settings_cache
            
        data = {}
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r") as f:
                    data = yaml.safe_load(f) or {}
            except Exception:
                pass
        
        settings = cls(**data)
        
        # Environment Variable Overrides
        if env_model := os.getenv("TELOS_PRODUCER_MODEL"):
            settings.llm.producer_model = env_model
        if env_critic_model := os.getenv("TELOS_CRITIC_MODEL"):
            settings.llm.critic_model = env_critic_model
        if env_embed := os.getenv("TELOS_EMBEDDING_MODEL"):
            settings.memory.embedding_model = env_embed
        if env_qdrant := os.getenv("QDRANT_URL"):
            settings.memory.qdrant_url = env_qdrant
        if env_docker := os.getenv("TELOS_USE_DOCKER"):
            settings.sandbox.use_docker = env_docker.lower() == "true"
        
        _settings_cache = settings
        return settings

    def save(self):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(self.model_dump(), f, sort_keys=False)

# Global settings instance
settings = Settings.load()

def reload_settings() -> "Settings":
    """Bust the settings cache and reload from disk. Useful after config changes."""
    global _settings_cache
    _settings_cache = None
    return Settings.load()


def generate_env_example():
    """Create a .env.example file with common providers."""
    env_example_path = PROJECT_ROOT / ".env.example"
    content = """# Telos Environment Variables Template
# Copy this to .env or .env.local and fill in your keys

# --- Major Providers ---
GEMINI_API_KEY=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# --- Other Secrets ---
HF_TOKEN=

# --- Overrides ---
# TELOS_PRODUCER_MODEL=gemini/gemini-2.0-flash-lite
# TELOS_CRITIC_MODEL=gemini/gemini-1.5-pro
# TELOS_EMBEDDING_MODEL=all-MiniLM-L6-v2
# TELOS_USE_DOCKER=true

# --- Infrastructure ---
# QDRANT_URL=http://localhost:6333
# TELOS_HOME=./data
"""
    if not env_example_path.exists():
        with open(env_example_path, "w") as f:
            f.write(content)

def init_directories(force: bool = False):
    """Initialize Telos home, outputs, and default templates/config."""
    TELOS_HOME.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    
    generate_env_example()
    
    # Save default config if not exists or if forced
    if force or not CONFIG_PATH.exists():
        settings.save()

