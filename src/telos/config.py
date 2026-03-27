import os
import warnings
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


def _find_project_config(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from `start` (default: CWD) looking for telos.yaml.
    Falls back to config.yaml with a DeprecationWarning for backward compat."""
    current = Path(start or Path.cwd()).resolve()
    while True:
        candidate = current / "telos.yaml"
        if _safe_exists(candidate):
            return candidate
        legacy = current / "config.yaml"
        if _safe_exists(legacy):
            warnings.warn(
                f"'{legacy}' is deprecated. Rename to 'telos.yaml'.",
                DeprecationWarning,
                stacklevel=2,
            )
            return legacy
        parent = current.parent
        if parent == current:  # reached filesystem root
            return None
        current = parent


def _get_global_config_path() -> Path:
    """Return the global config path, overridable via TELOS_GLOBAL_CONFIG env var."""
    default = Path.home() / ".config" / "telos" / "config.yaml"
    return Path(os.getenv("TELOS_GLOBAL_CONFIG", str(default)))


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins on conflicts."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# --- Module-level globals (computed once at import) ---
_project_config_path: Optional[Path] = _find_project_config()
_project_dir: Path = _project_config_path.parent if _project_config_path else PROJECT_ROOT

# TELOS_HOME: env var > telos.yaml dir/data > legacy projects/main (backward compat)
_legacy_home = PROJECT_ROOT / "projects" / "main"
TELOS_HOME = Path(os.getenv(
    "TELOS_HOME",
    str(_legacy_home) if _safe_exists(_legacy_home) else str(_project_dir / "data")
))

# CONFIG_PATH: where Settings.save() writes (always project config, never global)
CONFIG_PATH = _project_config_path if _project_config_path is not None else (_project_dir / "telos.yaml")

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
    persistent_workspace_name: str = Field(default="persistent", description="Name of the persistent workspace folder")

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
    initial_intent: str = Field(default="Establish existence and evolve.", description="Default ambient intent for the agent")
    history_limit: int = Field(default=20, description="Max recent loops to consider for context")
    similar_artifacts_limit: int = Field(default=3, description="Max similar artifacts to retrieve from vector memory")
    failure_threshold: float = Field(default=0.3, description="Scores below this are considered failures for lesson learning")
    max_lessons: int = Field(default=2, description="Maximum number of lessons to inject into the producer prompt")
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
        """Write to the project config file (telos.yaml), never to global config."""
        path = CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.model_dump(), f, sort_keys=False)


def load_settings() -> Settings:
    """Load settings by merging: global config -> project config -> env vars."""
    data: dict = {}

    # 1. Load global config as base
    global_path = _get_global_config_path()
    if global_path.exists():
        try:
            with open(global_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            pass

    # 2. Deep-merge project config on top
    project_path = _find_project_config()
    if project_path and project_path.exists():
        try:
            with open(project_path) as f:
                project_data = yaml.safe_load(f) or {}
            data = _deep_merge(data, project_data)
        except Exception:
            pass

    s = Settings(**data)

    # 3. Apply environment variable overrides last
    if v := os.getenv("TELOS_PRODUCER_MODEL"):
        s.llm.producer_model = v
    if v := os.getenv("TELOS_CRITIC_MODEL"):
        s.llm.critic_model = v
    if v := os.getenv("TELOS_EMBEDDING_MODEL"):
        s.memory.embedding_model = v
    if v := os.getenv("QDRANT_URL"):
        s.memory.qdrant_url = v
    if v := os.getenv("TELOS_USE_DOCKER"):
        s.sandbox.use_docker = v.lower() == "true"

    # Workspace is always scoped to the active project (TELOS_HOME).
    s.memory.workspace_path = str(TELOS_HOME / "workspace")

    return s


# Global settings instance
settings = Settings.load()


def reload_settings() -> Settings:
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


def init_global_directories(force: bool = False):
    """Initialize the global config file at ~/.config/telos/config.yaml."""
    global_path = _get_global_config_path()
    global_path.parent.mkdir(parents=True, exist_ok=True)
    if force or not global_path.exists():
        global_defaults = {
            "memory": {"qdrant_url": "http://localhost:6333"},
            "sandbox": {
                "image": "telos-sandbox:latest",
                "container_name": "telos-agent-sandbox",
                "use_docker": True,
                "memory_limit": "1024m",
                "timeout": 300,
            },
            "logging": {"level": "INFO"},
            "daily_loop_limit": 1000,
            "monthly_cost_limit": 20.0,
        }
        with open(global_path, "w") as f:
            yaml.dump(global_defaults, f, sort_keys=False)


def init_project_directories(force: bool = False):
    """Create project directory structure. telos.yaml is only written if absent (or forced)."""
    TELOS_HOME.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    generate_env_example()
    # Only write telos.yaml when explicitly initializing (not on every import)
    project_yaml = _project_dir / "telos.yaml"
    if force or not project_yaml.exists():
        settings.save()


def init_directories(force: bool = False):
    """Backward-compat wrapper: initialize project directories only."""
    TELOS_HOME.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    generate_env_example()
    # Do NOT auto-create telos.yaml here — only `telos init` should do that.
    # For backward compat: if config.yaml exists we've already loaded it; nothing to do.
