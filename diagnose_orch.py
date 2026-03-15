import sys
from pathlib import Path

# Add src to path
sys.path.append(str(Path.cwd() / "src"))

try:
    print("Checking Orchestrator initialization...")
    from telos.telos_core import Orchestrator
    orch = Orchestrator()
    print("Initialization success.")
    
    print("Checking safety checks...")
    orch._check_safety()
    print("Safety checks success.")
    
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
