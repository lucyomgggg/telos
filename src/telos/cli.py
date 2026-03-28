import os
import sys
import signal
import subprocess
import warnings
import logging

os.environ.setdefault("LITELLM_LOG", "ERROR")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

warnings.filterwarnings("ignore", module="litellm")
logging.getLogger("litellm").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)

from pathlib import Path
import click
from dotenv import load_dotenv

# Load env files BEFORE importing config so TELOS_HOME is set correctly
load_dotenv()
load_dotenv(".env.local")

from .config import (
    init_directories,
    PID_FILE, LOG_FILE, TELOS_HOME, PROJECT_CONFIG, settings,
)

# Ensure directories and default config exist on import
init_directories()

@click.group()
def cli():
    """Telos — autonomous AI runtime.

    Typical workflow:

    \b
      # Edit telos.yaml (model, intent) and .env (API key)
      telos run --loops 5            # run 5 loops
      telos project list             # check active project
      telos reset                    # wipe and start over
    """
    pass


def _key_for(model: str) -> str:
    """Derive the API key env var name from a model string."""
    return f"{model.split('/')[0].upper()}_API_KEY"


def _format_api_error(e: Exception) -> str:
    """Convert raw LLM/API exceptions to a human-readable message."""
    import json, re
    msg = str(e)
    try:
        match = re.search(r'\{.*\}', msg, re.DOTALL)
        if match:
            data = json.loads(match.group())
            err = data.get("error", {})
            code = err.get("code") or err.get("status_code") or ""
            message = err.get("message", "")
            if str(code) == "401" or "401" in msg:
                return (
                    f"Authentication failed (401): {message}\n"
                    f"  → Check OPENROUTER_API_KEY in .env\n"
                    f"  → Get a key at: https://openrouter.ai/keys"
                )
            if str(code) == "429" or "429" in msg:
                return "Rate limit hit (429). Wait a moment and try again."
            if str(code) == "402" or "billing" in msg.lower() or "insufficient" in msg.lower():
                return "Insufficient credits (402). Top up at: https://openrouter.ai/credits"
            if message:
                return f"API error ({code}): {message}"
    except Exception:
        pass
    return msg


def _preflight_check():
    """Verify API keys are set for all configured models."""
    load_dotenv()
    load_dotenv(".env.local")

    try:
        from .config import reload_settings
        s = reload_settings()
        models = [m for m in [s.llm.producer_model, s.llm.goal_gen_model] if m is not None]
    except Exception:
        click.echo("❌  telos.yaml not found or invalid.")
        sys.exit(1)

    missing = [(_key_for(m), m) for m in set(models) if not os.getenv(_key_for(m))]
    if missing:
        for key_name, model in missing:
            click.echo(f"❌  {key_name} is not set (needed for {model})")
        click.echo("   Add it to .env and re-run.")
        sys.exit(1)


def _ensure_env() -> None:
    """Auto-copy .env.example → .env if .env doesn't exist, then exit with instructions."""
    env_path = Path(".env")
    if env_path.exists():
        return
    import shutil
    example_path = Path(".env.example")
    if example_path.exists():
        shutil.copy(example_path, env_path)
        click.echo("✓ Created .env from .env.example — fill in your API key and re-run.")
    else:
        click.echo("❌  .env not found. Create it with your API key (e.g. OPENROUTER_API_KEY=...).")
    sys.exit(0)


def _docker_available() -> bool:
    """Return True if Docker daemon is reachable."""
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _docker_start() -> None:
    """Start Qdrant and build the sandbox image if needed. No-op if Docker is unavailable."""
    if not _docker_available():
        click.echo("⚠️  Docker not found — running in local sandbox mode (no isolation).")
        return

    from .config import reload_settings
    image_name = reload_settings().sandbox.image

    # Build sandbox image if not already present
    inspect = subprocess.run(["docker", "image", "inspect", image_name], capture_output=True)
    if inspect.returncode != 0:
        click.echo(f"Building sandbox image {image_name} (first time ~2 min)...", nl=False)
        build = subprocess.run(
            ["docker", "build", "-t", image_name, str(Path.cwd())],
            capture_output=True,
        )
        if build.returncode == 0:
            click.echo(" done ✓")
        else:
            click.echo(" ⚠️  Build failed. Check Dockerfile.")
            click.echo(build.stderr.decode(errors="replace")[:400])

    # Start Qdrant
    click.echo("Starting Qdrant...", nl=False)
    r = subprocess.run(["docker", "compose", "up", "-d"], capture_output=True)
    click.echo(" done ✓" if r.returncode == 0 else " ⚠️  (failed, vector memory disabled)")



def _ensure_embedding() -> None:
    """Download the embedding model if not already cached (no-op after first run)."""
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    try:
        from sentence_transformers import SentenceTransformer
        SentenceTransformer("all-MiniLM-L6-v2")
    except Exception as e:
        click.echo(f"⚠️  Embedding model unavailable: {e}")


@cli.command()
@click.option('--loops', '-n', default=1, type=int, help='Number of loops to run.  [default: 1]')
@click.option('--name', default=None, help='Session name (auto-generated if omitted).')
@click.option('--model', default=None, help='Override producer model (default: from telos.yaml).')
def run(model, loops, name):
    """Run autonomous loops."""
    _ensure_env()
    _preflight_check()
    _docker_start()
    _ensure_embedding()

    from .telos_core import AgentLoop

    selected_model = model or settings.llm.producer_model

    agent = AgentLoop(session_name=name, intended_loops=loops)

    PID_FILE.write_text(str(os.getpid()))
    session_cost = 0.0
    completed = 0
    click.echo(f"Session {agent.session_id[:8]}  model={selected_model}  project={TELOS_HOME.name}")
    click.echo("")
    try:
        for i in range(loops):
            click.echo(f"[{i+1}/{loops}] Generating goal...")
            loop_data = agent.run_iteration()

            loop_cost = loop_data.get("cost_usd", 0.0) or 0.0
            session_cost += loop_cost
            completed += 1

            status = loop_data.get("status", "completed")
            icon = "✓" if status == "completed" else "✗"
            post = loop_data.get("instincts_post", {})
            instinct_str = (
                f"C={post.get('curiosity', 0):.2f} "
                f"P={post.get('preservation', 0):.2f} "
                f"G={post.get('growth', 0):.2f} "
                f"O={post.get('order', 0):.2f}"
            )
            click.echo(f"[{i+1}/{loops}] {icon} {loop_data['goal']}")
            click.echo(f"      instincts: {instinct_str}  cost: ${loop_cost:.4f}")

    except KeyboardInterrupt:
        click.echo("\nInterrupted.")
    except Exception as e:
        click.echo(f"\n❌  {_format_api_error(e)}", err=True)
    finally:
        click.echo(f"\nDone: {completed}/{loops} loops  total cost: ${session_cost:.4f}")
        agent.shutdown()
        PID_FILE.unlink(missing_ok=True)


@cli.command()
def stop():
    """Stop a running loop gracefully."""
    if not PID_FILE.exists():
        click.echo("No running Telos process found.")
        return

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        click.echo(f"Sent stop signal to Telos process (PID: {pid}).")
    except ProcessLookupError:
        click.echo("Telos process is no longer running. Cleaning up PID file.")
        PID_FILE.unlink(missing_ok=True)
    except ValueError:
        click.echo("Invalid PID file. Removing.")
        PID_FILE.unlink(missing_ok=True)
    except PermissionError:
        click.echo(f"Permission denied sending signal to PID. Try: kill $(cat {PID_FILE})")

@cli.command()
@click.option('--yes', is_flag=True, help='Skip confirmation prompt.')
def reset(yes):
    """Wipe the current project's data and start fresh from loop 1.

    Deletes the database, workspace files, agent log, and journal for the active project.
    The project directory itself is kept. Use 'telos project delete' to remove a project entirely.
    """
    current = _active_project_name()
    click.echo(f"Active project: {click.style(current, fg='cyan', bold=True)}")

    if not yes:
        click.echo(click.style("⚠️  This will permanently delete:", fg='yellow'))
        click.echo("   • database (all sessions, loops, audit logs)")
        click.echo("   • workspace files")
        click.echo("   • agent log")
        click.echo("   • JOURNAL.md")
        if not click.confirm(click.style("\nAre you sure?", fg='red')):
            click.echo("Aborted.")
            return

    import shutil

    db_file = TELOS_HOME / "telos.db"
    if db_file.exists():
        db_file.unlink()
        click.echo("Database deleted.")

    workspace_path = Path(settings.memory.workspace_path)
    if workspace_path.exists():
        shutil.rmtree(workspace_path)
        workspace_path.mkdir(parents=True, exist_ok=True)
        click.echo("Workspace cleared.")

    if LOG_FILE.exists():
        LOG_FILE.unlink()
        click.echo("Log cleared.")

    journal_file = TELOS_HOME / "JOURNAL.md"
    if journal_file.exists():
        journal_file.unlink()
        click.echo("Journal cleared.")

    click.echo(click.style("\nReset complete. Run 'telos run' to begin fresh.", fg='green', bold=True))


# ---------------------------------------------------------------------------
# Project management
# ---------------------------------------------------------------------------

def _projects_dir() -> Path:
    return Path.cwd() / "projects"


def _active_project_name() -> str:
    """Derive the active project name from the current TELOS_HOME env var."""
    # Re-read from env each call so it reflects what was loaded from .env.local
    home = Path(os.getenv("TELOS_HOME", str(TELOS_HOME)))
    try:
        return home.relative_to(_projects_dir()).parts[0]
    except ValueError:
        return home.name


def _project_stats(proj_dir: Path) -> str:
    """Return a human-readable stats string for a project directory."""
    db_file = proj_dir / "telos.db"
    if not db_file.exists():
        return click.style("(empty)", dim=True)
    try:
        from .memory import MemoryStore
        store = MemoryStore(db_path=str(db_file))
        s = store.get_dashboard_summary()
        cost_str = f"  ${s['total_cost_usd']:.4f}" if s['total_cost_usd'] else ""
        return click.style(
            f"{s['total_loops']} loops · avg {s['avg_score']:.2f}{cost_str}",
            dim=True,
        )
    except Exception:
        return click.style("(db present)", dim=True)


def _set_active_project(proj_dir: Path) -> None:
    """Persist the active project by writing TELOS_HOME to .env.local."""
    env_local = Path.cwd() / ".env.local"
    lines = []
    if env_local.exists():
        lines = [l for l in env_local.read_text().splitlines() if not l.startswith("TELOS_HOME=")]
    lines.append(f"TELOS_HOME={proj_dir}")
    env_local.write_text("\n".join(lines) + "\n")


@cli.group()
def project():
    """Manage isolated project environments.

    Each project has its own database, workspace, and logs.

    \b
    telos project list                 # list all projects (★ = active)
    telos project new experiment-v2   # create + switch
    telos project switch main          # switch active project
    telos project delete old-run       # permanently delete a project
    """
    pass


@project.command("new")
@click.argument("name")
def project_new(name):
    """Create a new project and switch to it."""
    if not name.replace("-", "").replace("_", "").isalnum():
        click.echo(click.style("Error: name must be alphanumeric (hyphens/underscores allowed).", fg='red'))
        raise SystemExit(1)

    proj_dir = _projects_dir() / name
    if proj_dir.exists():
        click.echo(click.style(f"Project '{name}' already exists.", fg='yellow'))
    else:
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "workspace" / "persistent").mkdir(parents=True, exist_ok=True)
        click.echo(f"Created project '{click.style(name, bold=True)}'.")

    _set_active_project(proj_dir)
    click.echo(click.style(f"Switched to '{name}'. Restart telos if it is currently running.", fg='green'))


@project.command("list")
def project_list():
    """List all projects, showing the active one."""
    projects_dir = _projects_dir()
    current = _active_project_name()

    click.echo(f"\n{click.style('PROJECTS', fg='cyan', bold=True)}")
    click.echo(click.style("-" * 55, dim=True))

    if not projects_dir.exists() or not any(p.is_dir() for p in projects_dir.iterdir()):
        click.echo(click.style("  No projects yet. Run: telos project new <name>", dim=True))
        click.echo("")
        return

    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        is_active = entry.name == current
        marker = click.style(" ★", fg='cyan') if is_active else "  "
        name_str = click.style(entry.name, bold=True, fg='cyan') if is_active else click.style(entry.name, bold=True)
        stats = _project_stats(entry)
        click.echo(f"{marker} {name_str}  {stats}")
    click.echo("")


@project.command("switch")
@click.argument("name")
def project_switch(name):
    """Switch to an existing project."""
    proj_dir = _projects_dir() / name
    if not proj_dir.exists():
        click.echo(click.style(f"Project '{name}' not found. Create it with: telos project new {name}", fg='red'))
        raise SystemExit(1)

    _set_active_project(proj_dir)
    click.echo(click.style(f"Switched to '{name}'. Restart telos if it is currently running.", fg='green'))


@project.command("delete")
@click.argument("name")
@click.option('--yes', is_flag=True, help='Skip confirmation prompt.')
def project_delete(name, yes):
    """Permanently delete a project and all its data."""
    proj_dir = _projects_dir() / name
    if not proj_dir.exists():
        click.echo(click.style(f"Project '{name}' not found.", fg='red'))
        raise SystemExit(1)

    if _active_project_name() == name:
        click.echo(click.style(f"Cannot delete the active project. Switch first: telos project switch <name>", fg='red'))
        raise SystemExit(1)

    if not yes:
        click.echo(click.style(f"⚠️  This will permanently delete project '{name}' and all its data.", fg='yellow'))
        if not click.confirm(click.style("Are you sure?", fg='red')):
            click.echo("Aborted.")
            return

    import shutil
    shutil.rmtree(proj_dir)
    click.echo(click.style(f"Deleted project '{name}'.", fg='green'))


def main():
    cli()

if __name__ == '__main__':
    main()
