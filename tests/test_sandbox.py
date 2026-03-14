import pytest
import os
from pathlib import Path
from telos.sandbox import SandboxManager


class TestLocalSandbox:
    """Test SandboxManager in local fallback mode (no Docker)."""

    @pytest.fixture
    def sandbox(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sm = SandboxManager.__new__(SandboxManager)
        sm.use_docker = False
        sm.container = None
        sm.local_workspace = tmp_path / "workspace"
        sm.local_workspace.mkdir(exist_ok=True)
        return sm

    def test_write_and_read_file(self, sandbox):
        sandbox.write_file("test.txt", "Hello, world!")
        content = sandbox.read_file("test.txt")
        assert content == "Hello, world!"

    def test_write_file_nested_dir(self, sandbox):
        sandbox.write_file("subdir/nested.txt", "nested content")
        content = sandbox.read_file("subdir/nested.txt")
        assert content == "nested content"

    def test_read_nonexistent_file(self, sandbox):
        result = sandbox.read_file("does_not_exist.txt")
        assert "Error" in result or "not found" in result

    def test_execute_command(self, sandbox):
        result = sandbox.execute_command("echo 'hello from sandbox'")
        assert result["exit_code"] == 0
        assert "hello from sandbox" in result["output"]

    def test_execute_command_failure(self, sandbox):
        result = sandbox.execute_command("false")  # always returns exit code 1
        assert result["exit_code"] != 0

    def test_execute_command_timeout(self, sandbox):
        result = sandbox.execute_command("sleep 10", timeout=1)
        assert result["exit_code"] == 124
        assert "timed out" in result["output"].lower()

    def test_write_overwrites_existing(self, sandbox):
        sandbox.write_file("overwrite.txt", "first")
        sandbox.write_file("overwrite.txt", "second")
        content = sandbox.read_file("overwrite.txt")
        assert content == "second"
