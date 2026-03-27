"""Tests for the 2-file configuration system (config.yaml + telos.yaml)."""
import yaml
import pytest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------

class TestDeepMerge:
    def test_override_wins_scalar(self):
        from src.telos.config import _deep_merge
        result = _deep_merge({"a": 1}, {"a": 2})
        assert result["a"] == 2

    def test_base_keys_preserved(self):
        from src.telos.config import _deep_merge
        result = _deep_merge({"a": 1, "b": 2}, {"a": 99})
        assert result["b"] == 2

    def test_nested_merge(self):
        from src.telos.config import _deep_merge
        base = {"llm": {"model": "base", "tokens": 100}}
        override = {"llm": {"model": "new"}}
        result = _deep_merge(base, override)
        assert result["llm"]["model"] == "new"
        assert result["llm"]["tokens"] == 100

    def test_does_not_mutate_base(self):
        from src.telos.config import _deep_merge
        base = {"a": {"x": 1}}
        _deep_merge(base, {"a": {"y": 2}})
        assert "y" not in base["a"]

    def test_does_not_mutate_override(self):
        from src.telos.config import _deep_merge
        override = {"a": {"y": 2}}
        _deep_merge({"a": {"x": 1}}, override)
        assert "x" not in override["a"]


# ---------------------------------------------------------------------------
# load_settings — 5 cases
# ---------------------------------------------------------------------------

class TestLoadSettings:
    def _write(self, path: Path, data: dict):
        path.write_text(yaml.dump(data))
        return path

    def test_infra_config_only(self, tmp_path):
        """Settings loaded from config.yaml when telos.yaml absent."""
        infra = self._write(tmp_path / "config.yaml", {"logging": {"level": "DEBUG"}})
        project = tmp_path / "telos.yaml"  # does not exist

        with patch("src.telos.config.INFRA_CONFIG", infra), \
             patch("src.telos.config.PROJECT_CONFIG", project), \
             patch("src.telos.config.TELOS_HOME", tmp_path / "data"):
            from src.telos.config import load_settings
            s = load_settings()
        assert s.logging.level == "DEBUG"

    def test_project_config_only(self, tmp_path):
        """Settings loaded from telos.yaml when config.yaml absent."""
        infra = tmp_path / "config.yaml"  # does not exist
        project = self._write(tmp_path / "telos.yaml", {"llm": {"producer_model": "project-model"}})

        with patch("src.telos.config.INFRA_CONFIG", infra), \
             patch("src.telos.config.PROJECT_CONFIG", project), \
             patch("src.telos.config.TELOS_HOME", tmp_path / "data"):
            from src.telos.config import load_settings
            s = load_settings()
        assert s.llm.producer_model == "project-model"

    def test_project_overrides_infra(self, tmp_path):
        """telos.yaml values override config.yaml on the same key; other keys survive."""
        infra = self._write(tmp_path / "config.yaml", {
            "memory": {"qdrant_url": "http://infra:6333"},
            "daily_loop_limit": 5,
        })
        project = self._write(tmp_path / "telos.yaml", {
            "llm": {"producer_model": "project-model"},
            "daily_loop_limit": 999,
        })

        with patch("src.telos.config.INFRA_CONFIG", infra), \
             patch("src.telos.config.PROJECT_CONFIG", project), \
             patch("src.telos.config.TELOS_HOME", tmp_path / "data"):
            from src.telos.config import load_settings
            s = load_settings()
        assert s.llm.producer_model == "project-model"   # from project
        assert s.memory.qdrant_url == "http://infra:6333"  # from infra (not overridden)
        assert s.daily_loop_limit == 999                  # project wins

    def test_env_var_wins_over_both(self, tmp_path):
        """Env vars override both config.yaml and telos.yaml."""
        infra = self._write(tmp_path / "config.yaml", {})
        project = self._write(tmp_path / "telos.yaml", {"llm": {"producer_model": "project-model"}})

        with patch("src.telos.config.INFRA_CONFIG", infra), \
             patch("src.telos.config.PROJECT_CONFIG", project), \
             patch("src.telos.config.TELOS_HOME", tmp_path / "data"), \
             patch.dict("os.environ", {"TELOS_PRODUCER_MODEL": "env-model"}):
            from src.telos.config import load_settings
            s = load_settings()
        assert s.llm.producer_model == "env-model"

    def test_no_files_uses_defaults(self, tmp_path):
        """When neither file exists, Pydantic defaults are used."""
        with patch("src.telos.config.INFRA_CONFIG", tmp_path / "config.yaml"), \
             patch("src.telos.config.PROJECT_CONFIG", tmp_path / "telos.yaml"), \
             patch("src.telos.config.TELOS_HOME", tmp_path / "data"):
            from src.telos.config import load_settings
            s = load_settings()
        assert s.history_limit == 20
        assert s.daily_loop_limit == 10
