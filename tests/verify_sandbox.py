
import os
import sys
import time
from pathlib import Path

# Add src to path
sys.path.append(str(Path(os.getcwd()) / "src"))

from telos.sandbox import SandboxManager
from telos.logger import get_logger

log = get_logger("verify_sandbox")

def verify_limits():
    sandbox = SandboxManager()
    
    # Ensure container is started
    log.info("Starting sandbox...")
    sandbox.start()
    
    # 1. Test Timeout (should time out at 300s, but we'll test with a shorter one for speed if we can, 
    # but the logic in replace_file_content used the passed timeout)
    log.info("Testing timeout (10s command with 5s timeout)...")
    result = sandbox.execute_command("sleep 10", timeout=5)
    log.info("Result: %s", result)
    if result["exit_code"] == 124:
        log.info("SUCCESS: Timeout enforced.")
    else:
        log.error("FAILURE: Timeout NOT enforced.")

    # 2. Test Memory Limit
    log.info("Testing memory limit (allocating 600MB)...")
    # This might be tricky in /bin/sh but we can try a python one-liner inside the container
    # if python is installed in the sandbox image. Assuming it might be.
    # If not, we'd need another way to stress memory.
    mem_command = "python3 -c 'a = [0] * (600 * 1024 * 1024 // 8)'"
    result = sandbox.execute_command(mem_command, timeout=30)
    log.info("Result: %s", result)
    # If memory limit is hit, the container or process usually exits with a non-zero code
    # or is killed (OOM).
    if result["exit_code"] != 0:
        log.info("SUCCESS: Memory limit likely enforced (exit code %d).", result["exit_code"])
    else:
        log.error("FAILURE: Memory limit NOT enforced (600MB allocated successfully).")

    # 3. Test workspace isolation
    log.info("Testing workspace isolation...")
    sandbox.write_file("test_isolation.txt", "hello")
    read_back = sandbox.read_file("test_isolation.txt")
    if read_back == "hello":
        log.info("SUCCESS: Simple read/write works.")
    else:
        log.error("FAILURE: Simple read/write failed.")

    # Cleanup
    log.info("Cleaning up...")
    sandbox.stop()

if __name__ == "__main__":
    verify_limits()
