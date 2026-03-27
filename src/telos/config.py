import os
import yaml
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Dict, Optional

# --- Cache ---
_settings_cache = None

# --- Paths ---
PROJECT_ROOT = Path.cwd()

def _safe_exists(p: Path) -> bool:
    try:
        return p.exists()
    except PermissionError:
        return False

INFRA_CONFIG   = PROJECT_ROOT / "config.yaml"   # インフラ設定（Qdrant、Docker等）— git管理
PROJECT_CONFIG = PROJECT_ROOT / "telos.yaml"    # プロジェクト設定（モデル、intent）— git管理
CONFIG_PATH    = PROJECT_CONFIG                  # Settings.save() の書き込み先

TELOS_HOME   = Path(os.getenv("TELOS_HOME", str(PROJECT_ROOT / "data")))
TEMPLATES_DIR = PROJECT_ROOT / "templates"
LOG_FILE     = TELOS_HOME / "agent.log"
PID_FILE     = TELOS_HOME / "telos.pid"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins on conflicts."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class LLMSettings(BaseModel):
    model: str = Field(default="gemini/gemini-flash-latest", description="Default model (fallback)")
    producer_model: str = Field(default="gemini/gemini-flash-latest", description="Model for the Producer agent")
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
    persistent_workspace_name: str = Field(default="persistent", description="Name of the persistent workspace folder")

class SandboxSettings(BaseModel):
    image: str = Field(default="telos-sandbox:latest", description="Docker image for the sandbox")
    container_name: str = Field(default="telos-agent-sandbox", description="Name for the sandbox container")
    use_docker: bool = Field(default=True, description="Whether to use Docker or local execution")
    memory_limit: str = Field(default="512m", description="Docker memory limit (e.g., 512m, 1g)")
    timeout: int = Field(default=300, description="Hard timeout for sandbox commands in seconds")

class LoggingSettings(BaseModel):
    level: str = Field(default="INFO", description="Console log level (DEBUG, INFO, WARNING, ERROR)")

class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    initial_intent: str = Field(default="Establish existence and evolve.", description="Default ambient intent for the agent")
    history_limit: int = Field(default=20, description="Max recent loops to consider for context")
    similar_artifacts_limit: int = Field(default=3, description="Max similar artifacts to retrieve from vector memory")
    max_lessons: int = Field(default=2, description="Maximum number of failed-loop lessons to inject into the producer prompt")
    daily_loop_limit: int = Field(default=10, description="Max loops per day")
    monthly_cost_limit: float = Field(default=50.0, description="Max USD budget per month")
    rate_limit_delay: float = Field(default=6.0, description="Seconds to wait between LLM calls")
    deduplication_threshold: float = Field(default=0.85, description="Similarity threshold for goal deduplication")
    max_steps: int = Field(default=15, description="Maximum steps per loop execution")
    consecutive_error_limit: int = Field(default=3, description="Abort after N consecutive tool errors")
    max_output_truncation: int = Field(default=8000, description="Truncate tool outputs longer than this")
    model_cost_overrides: Dict[str, Dict[str, float]] = Field(
        default_factory=dict,
        description="Custom per-token costs for models not in litellm's database. "
                    "Keys are model IDs returned in API responses. "
                    "Values: {input_cost_per_million: float, output_cost_per_million: float}"
    )

    @classmethod
    def load(cls) -> "Settings":
        global _settings_cache
        if _settings_cache:
            return _settings_cache
        _settings_cache = load_settings()
        return _settings_cache

    def save(self):
        """Write project settings to telos.yaml."""
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(self.model_dump(), f, sort_keys=False)


def load_settings() -> Settings:
    """Load settings: config.yaml (infra base) → telos.yaml (project overrides) → env vars."""
    data: dict = {}

    # 1. Infra base (config.yaml)
    if INFRA_CONFIG.exists():
        try:
            with open(INFRA_CONFIG) as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            pass

    # 2. Project overrides (telos.yaml)
    if PROJECT_CONFIG.exists():
        try:
            with open(PROJECT_CONFIG) as f:
                project_data = yaml.safe_load(f) or {}
            data = _deep_merge(data, project_data)
        except Exception:
            pass

    s = Settings(**data)

    # 3. Environment variable overrides (highest priority)
    if v := os.getenv("TELOS_PRODUCER_MODEL"):
        s.llm.producer_model = v
    if v := os.getenv("TELOS_EMBEDDING_MODEL"):
        s.memory.embedding_model = v
    if v := os.getenv("QDRANT_URL"):
        s.memory.qdrant_url = v
    if v := os.getenv("TELOS_USE_DOCKER"):
        s.sandbox.use_docker = v.lower() == "true"

    # Workspace is always scoped to TELOS_HOME.
    s.memory.workspace_path = str(TELOS_HOME / "workspace")

    return s


# Global settings instance
settings = Settings.load()


def reload_settings() -> Settings:
    """Bust the settings cache and reload from disk."""
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
OPENROUTER_API_KEY=

# --- Other Secrets ---
HF_TOKEN=

# --- Overrides ---
# TELOS_PRODUCER_MODEL=openrouter/anthropic/claude-sonnet-4-6
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
    """Initialize TELOS_HOME, templates, and telos.yaml (if absent)."""
    TELOS_HOME.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    generate_env_example()
    # Write telos.yaml only when explicitly initializing, not on every import.
    if force or not PROJECT_CONFIG.exists():
        settings.save()
