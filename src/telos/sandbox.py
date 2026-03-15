import docker
import os
import tarfile
import io
import time
import subprocess
from pathlib import Path
from .logger import get_logger

log = get_logger("sandbox")

class SandboxManager:
    def __init__(self, image_name=None, container_name=None):
        from .config import settings, PROJECT_ROOT
        self.settings = settings.load()
        
        self.image_name = image_name or self.settings.sandbox.image
        self.container_name = container_name or self.settings.sandbox.container_name
        self.container = None
        self.use_docker = self.settings.sandbox.use_docker
        
        workspace_name = self.settings.memory.workspace_path
        self.local_workspace = PROJECT_ROOT / workspace_name
        self.local_workspace.mkdir(exist_ok=True)
        
        self.mem_limit = self.settings.sandbox.memory_limit
        self.cmd_timeout = self.settings.sandbox.timeout
        
        if self.use_docker:
            try:
                self.client = docker.from_env()
                self.client.ping()
                log.info("Docker connected successfully.")
            except (docker.errors.DockerException, Exception) as e:
                log.warning("Docker not available, falling back to local execution: %s", e)
                self.use_docker = False

    def build_image(self, dockerfile_path="."):
        """Build the sandbox Docker image if it doesn't exist."""
        log.info("Building Docker image %s...", self.image_name)
        try:
            self.client.images.build(path=dockerfile_path, tag=self.image_name)
            log.info("Docker image built successfully.")
        except Exception as e:
            log.error("Failed to build Docker image: %s", e)
            raise

    def start(self):
        """Start the sandbox container."""
        if not self.use_docker:
            log.info("Running in local sandbox mode (workspace: %s)", self.local_workspace)
            return
            
        try:
            self.container = self.client.containers.get(self.container_name)
            if self.container.status != "running":
                self.container.start()
                log.info("Restarted existing container %s", self.container_name)
            else:
                log.info("Container %s already running.", self.container_name)
        except docker.errors.NotFound:
            self.container = self.client.containers.run(
                self.image_name,
                name=self.container_name,
                detach=True,
                network_mode="bridge",
                mem_limit=self.mem_limit,
                memswap_limit=self.mem_limit,
                network_disabled=False,
            )
            log.info("Created and started new container %s", self.container_name)
        return self.container

    def stop(self):
        """Stop and remove the sandbox container."""
        if not self.use_docker:
            return
            
        if self.container:
            try:
                self.container.stop(timeout=2)
                self.container.remove()
                log.info("Container %s stopped and removed.", self.container_name)
            except docker.errors.NotFound:
                pass
            except Exception as e:
                log.error("Error stopping container: %s", e)
            self.container = None

    def execute_command(self, command: str, timeout: int = None) -> dict:
        """Execute a command inside the sandbox."""
        exec_timeout = timeout or self.cmd_timeout
        
        if not self.use_docker:
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(self.local_workspace),
                    capture_output=True,
                    text=True,
                    timeout=exec_timeout
                )
                output = result.stdout
                if result.stderr:
                    output += "\n" + result.stderr
                return {
                    "exit_code": result.returncode,
                    "output": output
                }
            except subprocess.TimeoutExpired:
                log.warning("Command timed out after %ds: %s", exec_timeout, command[:80])
                return {
                    "exit_code": 124,
                    "output": "Command timed out."
                }
            except Exception as e:
                return {
                    "exit_code": 1,
                    "output": str(e)
                }

        if not self.container:
            self.start()
        
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
        thread.join(timeout=exec_timeout)

        if thread.is_alive():
            log.warning("Command timed out after %ds: %s", exec_timeout, command[:80])
            return {
                "exit_code": 124,
                "output": f"Error: Command timed out after {exec_timeout} seconds."
            }

        if not result_container:
            return {"exit_code": 1, "output": "Unknown error: No result captured."}

        exec_result = result_container[0]
        if isinstance(exec_result, Exception):
            return {
                "exit_code": 1,
                "output": str(exec_result)
            }
        
        return {
            "exit_code": exec_result.exit_code,
            "output": exec_result.output.decode('utf-8')
        }

    def write_file(self, dest_path: str, content: str):
        """Write a file to the container workspace."""
        if not self.use_docker:
            file_path = self.local_workspace / dest_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "w") as f:
                f.write(content)
            log.debug("Wrote file locally: %s", file_path)
            return

        if not self.container:
            self.start()
        
        # Ensure parent directory exists
        parent_dir = os.path.dirname(f"/workspace/{dest_path}")
        if parent_dir and parent_dir != "/workspace":
            self.execute_command(f"mkdir -p {parent_dir}")

        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode='w') as tar:
            tarinfo = tarfile.TarInfo(name=os.path.basename(dest_path))
            tarinfo.size = len(content.encode('utf-8'))
            tarinfo.mtime = int(time.time())
            tar.addfile(tarinfo, io.BytesIO(content.encode('utf-8')))
        
        tar_stream.seek(0)
        self.container.put_archive(parent_dir, tar_stream)
        log.debug("Wrote file to container: /workspace/%s", dest_path)

    def read_file(self, file_path: str) -> str:
        """Read a file from the container workspace."""
        if not self.use_docker:
            full_path = self.local_workspace / file_path
            if not full_path.exists():
                return f"Error: File {file_path} not found in workspace."
            try:
                with open(full_path, "r") as f:
                    return f.read()
            except Exception as e:
                return f"Error reading file: {str(e)}"

        if not self.container:
            self.start()
        
        try:
            bits, stat = self.container.get_archive(f"/workspace/{file_path}")
            
            tar_stream = io.BytesIO()
            for chunk in bits:
                tar_stream.write(chunk)
            tar_stream.seek(0)
            
            with tarfile.open(fileobj=tar_stream, mode='r') as tar:
                member = tar.firstmember
                extracted_file = tar.extractfile(member)
                if extracted_file:
                    return extracted_file.read().decode('utf-8')
            return ""
        except docker.errors.NotFound:
            return f"Error: File {file_path} not found in sandbox."
        except Exception as e:
            return f"Error reading file: {str(e)}"
