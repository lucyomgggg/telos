import os
import signal
import click
from dotenv import load_dotenv
from .config import init_directories, PID_FILE, LOG_FILE, settings

load_dotenv()
load_dotenv(".env.local")

# Ensure directories and default config exist on import or explicitly via init
init_directories()

@click.group()
def cli():
    """Telos: open-source runtime where the AI decides what to build next."""
    pass

@cli.command()
def init():
    """Initialize Telos configuration and directories."""
    init_directories()
    click.echo("Initialized Telos directories.")

@cli.command()
@click.option('--model', help='The LLM model to use (default from config.yaml)')
@click.option('--loops', default=1, type=int, help='Number of loops to run (default: 1)')
def start(model, loops):
    """Start the autonomous loop."""
    from .loop import AgentLoop
    
    selected_model = model or settings.llm.model
    click.echo(f"Starting Telos loop with model: {selected_model} ({loops} loop(s))...")
    agent = AgentLoop(model=selected_model, max_loops=loops)
    agent.start()

@cli.command()
def stop():
    """Stop the autonomous loop gracefully."""
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
def status():
    """Show current loop status, cost, and scores."""
    from .memory import MemoryStore
    memory = MemoryStore()
    loops = memory.list_loops(limit=5)
    
    if not loops:
        click.echo("No loops recorded yet.")
        return
        
    click.echo(f"{'ID':<38} | {'Status':<10} | {'Score':<6} | {'Cost':<8} | {'Goal'}")
    click.echo("-" * 110)
    for loop in loops:
        cost = f"${loop['cost_usd']:.4f}"
        score = f"{loop['score']:.2f}" if loop['score'] is not None else "N/A"
        goal = loop['goal'][:40] + "..." if len(loop['goal']) > 40 else loop['goal']
        click.echo(f"{loop['id']:<38} | {loop['status']:<10} | {score:<6} | {cost:<8} | {goal}")

@cli.command()
@click.option('--lines', '-n', default=50, type=int, help='Number of lines to show')
@click.option('--follow', '-f', is_flag=True, help='Follow log output in real time')
def logs(lines, follow):
    """View agent logs."""
    if not LOG_FILE.exists():
        click.echo("No logs yet. Run 'telos start' first.")
        return
    
    if follow:
        # Tail -f mode
        import subprocess
        click.echo(f"Following {LOG_FILE} (Ctrl+C to stop)...")
        try:
            subprocess.run(["tail", "-f", "-n", str(lines), str(LOG_FILE)])
        except KeyboardInterrupt:
            pass
    else:
        # Show last N lines
        with open(LOG_FILE, "r") as f:
            all_lines = f.readlines()
            tail = all_lines[-lines:]
            for line in tail:
                click.echo(line, nl=False)

@cli.command()
def outputs():
    """List generated outputs."""
    from .memory import MemoryStore
    memory = MemoryStore()
    loops = memory.list_loops(limit=10)
    
    if not loops:
        click.echo("No outputs recorded yet.")
        return
        
    click.echo(f"{'ID':<38} | {'Score':<6} | {'Output Path/Indicator'}")
    click.echo("-" * 80)
    for loop in filter(lambda l: l['status'] == 'completed', loops):
        score = f"{loop['score']:.2f}" if loop['score'] is not None else "N/A"
        path = loop['output_path'] or "Stored in Memory"
        click.echo(f"{loop['id']:<38} | {score:<6} | {path}")

def main():
    cli()

if __name__ == '__main__':
    main()
