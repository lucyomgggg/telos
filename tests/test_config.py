"""Tests for the 2-tier configuration system (global + project config)."""
import warnings
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
        assert result["llm"]["tokens"] == 100  # preserved from base

    def test_does_not_mutate_base(self):
        from src.telos.config import _deep_merge
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        _deep_merge(base, override)
        assert "y" not in base["a"]

    def test_does_not_mutate_override(self):
        from src.telos.config import _deep_merge
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        _deep_merge(base, override)
        assert "x" not in override["a"]


# ---------------------------------------------------------------------------
# _find_project_config
# ---------------------------------------------------------------------------

class TestFindProjectConfig:
    def test_finds_telos_yaml_in_start_dir(self, tmp_path):
        from src.telos.config import _find_project_config
        telos_yaml = tmp_path / "telos.yaml"
        telos_yaml.write_text("initial_intent: test")
        result = _find_project_config(start=tmp_path)
        assert result == telos_yaml

    def test_finds_telos_yaml_in_parent(self, tmp_path):
        from src.telos.config import _find_project_config
        telos_yaml = tmp_path / "telos.yaml"
        telos_yaml.write_text("initial_intent: test")
        nested = tmp_path / "subdir" / "deeper"
        nested.mkdir(parents=True)
        result = _find_project_config(start=nested)
        assert result == telos_yaml

    def test_returns_none_when_not_found(self, tmp_path):
        from src.telos.config import _find_project_config
        # Use a path with no telos.yaml or config.yaml anywhere up the chain
        isolated = tmp_path / "isolated"
        isolated.mkdir()
        # Patch so upward search stops before hitting real filesystem hits
        with patch("src.telos.config._safe_exists", return_value=False):
            result = _find_project_config(start=isolated)
        assert result is None

    def test_legacy_config_yaml_emits_deprecation(self, tmp_path):
        from src.telos.config import _find_project_config
        legacy = tmp_path / "config.yaml"
        legacy.write_text("history_limit: 7")
        child = tmp_path / "child"
        child.mkdir()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _find_project_config(start=child)
        assert result == legacy
        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 1
        assert "config.yaml" in str(deprecation_warnings[0].message)

    def test_telos_yaml_takes_priority_over_config_yaml(self, tmp_path):
        from src.telos.config import _find_project_config
        telos_yaml = tmp_path / "telos.yaml"
        telos_yaml.write_text("initial_intent: from telos.yaml")
        (tmp_path / "config.yaml").write_text("initial_intent: from config.yaml")
        result = _find_project_config(start=tmp_path)
        assert result == telos_yaml


# ---------------------------------------------------------------------------
# load_settings — 4 required cases + env var override
# ---------------------------------------------------------------------------

class TestLoadSettings:
    def _make_global(self, tmp_path, data: dict) -> Path:
        p = tmp_path / "global_config.yaml"
        p.write_text(yaml.dump(data))
        return p

    def _make_project(self, tmp_path, data: dict) -> Path:
        p = tmp_path / "telos.yaml"
        p.write_text(yaml.dump(data))
        return p

    def test_global_config_only(self, tmp_path):
        """Settings are loaded from global config when no project config exists."""
        global_cfg = self._make_global(tmp_path, {"llm": {"producer_model": "global-model"}})
        nonexistent_project = tmp_path / "no_telos.yaml"

        with patch("src.telos.config._get_global_config_path", return_value=global_cfg), \
             patch("src.telos.config._find_project_config", return_value=nonexistent_project), \
             patch("src.telos.config.TELOS_HOME", tmp_path / "data"):
            from src.telos.config import load_settings
            s = load_settings()
        assert s.llm.producer_model == "global-model"

    def test_project_config_only(self, tmp_path):
        """Settings are loaded from project config when no global config exists."""
        project_cfg = self._make_project(tmp_path, {"llm": {"critic_model": "project-critic"}})
        nonexistent_global = tmp_path / "no_global.yaml"

        with patch("src.telos.config._get_global_config_path", return_value=nonexistent_global), \
             patch("src.telos.config._find_project_config", return_value=project_cfg), \
             patch("src.telos.config.TELOS_HOME", tmp_path / "data"):
            from src.telos.config import load_settings
            s = load_settings()
        assert s.llm.critic_model == "project-critic"

    def test_project_overrides_global(self, tmp_path):
        """Project config values override global config on the same key; other keys survive."""
        global_cfg = self._make_global(tmp_path, {
            "llm": {"producer_model": "global-producer", "critic_model": "global-critic"},
            "history_limit": 5,
        })
        project_cfg = self._make_project(tmp_path, {
            "llm": {"producer_model": "project-producer"},
            "daily_loop_limit": 99,
        })

        with patch("src.telos.config._get_global_config_path", return_value=global_cfg), \
             patch("src.telos.config._find_project_config", return_value=project_cfg), \
             patch("src.telos.config.TELOS_HOME", tmp_path / "data"):
            from src.telos.config import load_settings
            s = load_settings()
        # project overrides producer but global critic survives
        assert s.llm.producer_model == "project-producer"
        assert s.llm.critic_model == "global-critic"
        # top-level keys
        assert s.history_limit == 5        # from global
        assert s.daily_loop_limit == 99    # from project

    def test_telos_yaml_found_in_parent_directory(self, tmp_path):
        """_find_project_config walks up to parent dir to find telos.yaml."""
        telos_yaml = tmp_path / "telos.yaml"
        telos_yaml.write_text(yaml.dump({"history_limit": 42}))
        nested = tmp_path / "subdir" / "deeper"
        nested.mkdir(parents=True)

        from src.telos.config import _find_project_config
        result = _find_project_config(start=nested)
        assert result == telos_yaml

    def test_env_var_overrides_applied_last(self, tmp_path):
        """Env vars win over both global and project config."""
        project_cfg = self._make_project(tmp_path, {"llm": {"producer_model": "project-model"}})
        nonexistent_global = tmp_path / "no_global.yaml"

        with patch("src.telos.config._get_global_config_path", return_value=nonexistent_global), \
             patch("src.telos.config._find_project_config", return_value=project_cfg), \
             patch("src.telos.config.TELOS_HOME", tmp_path / "data"), \
             patch.dict("os.environ", {"TELOS_PRODUCER_MODEL": "env-model"}):
            from src.telos.config import load_settings
            s = load_settings()
        assert s.llm.producer_model == "env-model"

    def test_no_config_files_uses_defaults(self, tmp_path):
        """When neither config exists, Pydantic defaults are used."""
        nonexistent_global = tmp_path / "no_global.yaml"
        nonexistent_project = tmp_path / "no_telos.yaml"

        with patch("src.telos.config._get_global_config_path", return_value=nonexistent_global), \
             patch("src.telos.config._find_project_config", return_value=nonexistent_project), \
             patch("src.telos.config.TELOS_HOME", tmp_path / "data"):
            from src.telos.config import load_settings
            s = load_settings()
        assert s.history_limit == 20  # Pydantic default
        assert s.daily_loop_limit == 10  # Pydantic default
