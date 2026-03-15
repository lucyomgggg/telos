import docker
import os
import tarfile
import io
import time
import subprocess
from pathlib import Path
from .logger import get_logger

log = get_logger("sandbox")

from abc import ABC, abstractmethod

class SandboxStrategy(ABC):
    @abstractmethod
    def start(self): pass
    @abstractmethod
    def stop(self): pass
    @abstractmethod
    def execute(self, command: str, timeout: int) -> dict: pass
    @abstractmethod
    def write_file(self, full_path: Path, relative_path: str, content: str): pass
    @abstractmethod
    def read_file(self, full_path: Path, relative_path: str) -> str: pass

class DockerSandboxStrategy(SandboxStrategy):
    def __init__(self, client, image_name, container_name, mem_limit):
        self.client = client
        self.image_name = image_name
        self.container_name = container_name
        self.mem_limit = mem_limit
        self.container = None

    def start(self):
        try:
            self.container = self.client.containers.get(self.container_name)
            if self.container.status != "running":
                self.container.start()
        except docker.errors.NotFound:
            self.container = self.client.containers.run(
                self.image_name,
                name=self.container_name,
                detach=True,
                network_mode="bridge",
                mem_limit=self.mem_limit,
                memswap_limit=self.mem_limit,
            )
        return self.container

    def stop(self):
        if self.container:
            try:
                self.container.stop(timeout=2)
                self.container.remove()
            except Exception: pass
            self.container = None

    def execute(self, command: str, timeout: int) -> dict:
        import threading
        result_container = []
        def run_command():
            try:
                exec_res = self.container.exec_run(
                    cmd=["/bin/sh", "-c", command],
                    workdir="/workspace"
                )
                result_container.append(exec_res)
            except Exception as e:
                result_container.append(e)

        thread = threading.Thread(target=run_command)
        thread.start()
        thread.join(timeout=timeout)
        if thread.is_alive(): return {"exit_code": 124, "output": "Timeout"}
        exec_result = result_container[0]
        if isinstance(exec_result, Exception): return {"exit_code": 1, "output": str(exec_result)}
        return {"exit_code": exec_result.exit_code, "output": exec_result.output.decode('utf-8')}

    def write_file(self, full_path: Path, relative_path: str, content: str):
        if not self.container: self.start()
        parent_dir = os.path.dirname(f"/workspace/{relative_path}")
        if parent_dir and parent_dir != "/workspace":
            self.execute(f"mkdir -p {parent_dir}", 10)

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode='w') as tar:
            tarinfo = tarfile.TarInfo(name=os.path.basename(relative_path))
            tarinfo.size = len(content.encode('utf-8'))
            tarinfo.mtime = int(time.time())
            tar.addfile(tarinfo, io.BytesIO(content.encode('utf-8')))
        tar_stream.seek(0)
        self.container.put_archive(parent_dir, tar_stream)

    def read_file(self, full_path: Path, relative_path: str) -> str:
        if not self.container: self.start()
        try:
            bits, stat = self.container.get_archive(f"/workspace/{relative_path}")
            tar_stream = io.BytesIO()
            for chunk in bits: tar_stream.write(chunk)
            tar_stream.seek(0)
            with tarfile.open(fileobj=tar_stream, mode='r') as tar:
                return tar.extractfile(tar.firstmember).read().decode('utf-8')
        except Exception as e: return f"Error: {e}"

    def build_image(self, dockerfile_path="."):
        log.info("Building Docker image %s...", self.image_name)
        self.client.images.build(path=dockerfile_path, tag=self.image_name)

class LocalSandboxStrategy(SandboxStrategy):
    def __init__(self, workspace):
        self.workspace = workspace

    def start(self): pass
    def stop(self): pass
    def execute(self, command: str, timeout: int) -> dict:
        import shlex
        try:
            # Use shlex.split to safely parse the command into a list of arguments
            # and run without shell=True to prevent command injection.
            cmd_args = shlex.split(command)
            result = subprocess.run(cmd_args, capture_output=True, text=True, timeout=timeout)
            return {"exit_code": result.returncode, "output": result.stdout + (result.stderr or "")}
        except subprocess.TimeoutExpired: return {"exit_code": 124, "output": "Timeout"}
        except Exception as e: return {"exit_code": 1, "output": str(e)}

    def write_file(self, full_path: Path, relative_path: str, content: str):
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "w") as f: f.write(content)

    def read_file(self, full_path: Path, relative_path: str) -> str:
        if not full_path.exists(): return f"Error: File {relative_path} not found."
        with open(full_path, "r") as f: return f.read()

class SandboxManager:
    def __init__(self, image_name=None, container_name=None):
        from .config import settings, PROJECT_ROOT
        self.settings = settings.load()
        self.image_name = image_name or self.settings.sandbox.image
        self.container_name = container_name or self.settings.sandbox.container_name
        self.use_docker = self.settings.sandbox.use_docker
        self._base_workspace = PROJECT_ROOT / self.settings.memory.workspace_path
        self._base_workspace.mkdir(exist_ok=True)
        import uuid
        self.local_workspace = self._base_workspace / f"run_{uuid.uuid4().hex[:8]}"
        self.local_workspace.mkdir(exist_ok=True)
        self.cmd_timeout = self.settings.sandbox.timeout

        if self.use_docker:
            try:
                self.client = docker.from_env()
                self.client.ping()
                self.strategy = DockerSandboxStrategy(self.client, self.image_name, self.container_name, self.settings.sandbox.memory_limit)
                log.info("Docker sandbox strategy initialized.")
            except Exception: self.use_docker = False

        if not self.use_docker:
            self.strategy = LocalSandboxStrategy(self.local_workspace)
            log.info("Local sandbox strategy initialized.")

    def start(self): return self.strategy.start()
    def stop(self):
        self.strategy.stop()
        if self.local_workspace.exists():
            import shutil
            shutil.rmtree(self.local_workspace)

    def execute_command(self, command: str, timeout: int = None) -> dict:
        return self.strategy.execute(command, timeout or self.cmd_timeout)

    def _resolve_safe_path(self, path_str: str) -> Path:
        if os.path.isabs(path_str): path_str = path_str.lstrip('/')
        requested_path = (self.local_workspace / path_str).resolve()
        if not str(requested_path).startswith(str(self.local_workspace.resolve())):
            raise ValueError(f"Security Error: Path {path_str} is outside the workspace.")
        return requested_path

    def write_file(self, dest_path: str, content: str):
        full_path = self._resolve_safe_path(dest_path)
        relative_path = os.path.relpath(full_path, self.local_workspace)
        return self.strategy.write_file(full_path, relative_path, content)

    def read_file(self, file_path: str) -> str:
        full_path = self._resolve_safe_path(file_path)
        relative_path = os.path.relpath(full_path, self.local_workspace)
        return self.strategy.read_file(full_path, relative_path)
