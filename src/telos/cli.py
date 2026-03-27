import os
import signal
from pathlib import Path
import click
from dotenv import load_dotenv

# Load env files BEFORE importing config so TELOS_HOME is set correctly
load_dotenv()
load_dotenv(".env.local")

from .config import init_directories, PID_FILE, LOG_FILE, TELOS_HOME, settings

# Ensure directories and default config exist on import or explicitly via init
init_directories()

@click.group()
def cli():
    """Telos — autonomous AI runtime.

    Typical workflow:

    \b
      telos project list                 # check active project
      telos start --loops 5 --name my-run   # run 5 loops
      telos reset                            # wipe and start over
    """
    pass

@cli.command()
@click.option('--force', is_flag=True, help='Overwrite existing config files.')
def init(force):
    """Set up config files and the default project directory."""
    init_directories(force=force)
    # Ensure the active project directory exists
    TELOS_HOME.mkdir(parents=True, exist_ok=True)
    (TELOS_HOME / "workspace" / "persistent").mkdir(parents=True, exist_ok=True)
    click.echo(f"Initialized. Active project: {click.style(_active_project_name(), fg='cyan', bold=True)}")
    click.echo(f"  Data directory: {TELOS_HOME}")
    click.echo("\nNext steps:")
    click.echo("  1. Copy .env.example to .env and add your API keys.")
    click.echo("  2. Edit config.yaml to set your initial_intent and models.")
    click.echo("  3. Run: telos start --loops 1")

@cli.command()
@click.option('--loops', '-n', default=1, type=int, help='Number of loops to run.  [default: 1]')
@click.option('--name', default=None, help='Session name (auto-generated if omitted).')
@click.option('--model', default=None, help='Override producer model (default: from config.yaml).')
def start(model, loops, name):
    """Run autonomous loops."""
    from .telos_core import AgentLoop
    from .config import PID_FILE

    selected_model = model or settings.llm.producer_model

    agent = AgentLoop(session_name=name, intended_loops=loops)

    PID_FILE.write_text(str(os.getpid()))
    session_cost = 0.0
    completed = 0
    scores = []
    print(f"[Telos] Session started: {agent.session_id[:8]} | {selected_model}")
    try:
        for i in range(loops):
            print(f"[Loop {i+1}] Generating goal...")
            loop_data = agent.run_iteration()

            loop_cost = loop_data.get("cost_usd", 0.0) or 0.0
            session_cost += loop_cost
            completed += 1
            score = loop_data.get("score") or 0.0
            scores.append(score)

            print(f"[Loop {i+1}] Goal: {loop_data['goal']}")
            icon = "✅" if score >= 0.5 else "❌"
            print(f"[Loop {i+1}] {icon} {score:.2f} — {loop_data['goal']}")

    except KeyboardInterrupt:
        print("\n[Telos] Interrupted.")
    except Exception as e:
        print(f"[Loop error] {e}")
    finally:
        avg = sum(scores) / len(scores) if scores else 0.0
        print(f"[Session] Complete: {completed} loops | avg: {avg:.2f} | ${session_cost:.3f} | JOURNAL updated")
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

    click.echo(click.style("\nReset complete. Run 'telos start' to begin fresh.", fg='green', bold=True))


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
