import os
import sys
from pathlib import Path
from datetime import datetime
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
@click.option('--force', is_flag=True, help='Overwrite existing configuration files.')
def init(force):
    """Initialize Telos configuration and directories."""
    init_directories(force=force)
    click.echo("✅ Initialized Telos directories and configuration.")
    click.echo("\n--- Next Steps ---")
    click.echo("1. API Keys: Copy .env.example to .env and add your API keys.")
    click.echo("   Example: cp .env.example .env")
    click.echo("2. Customization: Edit config.yaml or templates/ to customize agent behavior.")
    click.echo("3. Run: Start the agent with 'telos start --loops 1'")

@cli.command()
@click.option('--model', help='The LLM model to use (default from config.yaml)')
@click.option('--loops', default=1, type=int, help='Number of loops to run (default: 1)')
@click.option('--verbose', is_flag=True, help='Show full results in terminal.')
def start(model, loops, verbose):
    """Start the autonomous loop."""
    from .telos_core import AgentLoop
    from .config import PID_FILE
    
    selected_model = model or settings.llm.producer_model
    click.echo(f"✨ {click.style('Telos Initiated', fg='cyan', bold=True)}")
    click.echo(f"   Model: {click.style(selected_model, fg='white')}")
    click.echo(f"   Mode:  Autonomous ({loops} iterate(s))\n")
    
    agent = AgentLoop()
    
    import os
    PID_FILE.write_text(str(os.getpid()))
    try:
        for i in range(loops):
            click.echo(f"🔄 {click.style(f'Iteration {i+1}/{loops}', bold=True)} {'-'*40}")
            
            # Use a simple "thinking" indicator
            click.echo(f"🤖 {click.style('Agent is thinking...', dim=True)}")
            loop_data = agent.run_iteration()
            
            # Print elegant summary
            click.echo(f"\n✅ {click.style('Iteration Complete', fg='green', bold=True)}")
            click.echo(f"   {click.style('ID:', dim=True)} {loop_data['id'][:8]}")
            click.echo(f"   {click.style('Goal:', dim=True)} {loop_data['goal']}")
            
            score_color = 'green' if loop_data['score'] > 0.7 else 'yellow' if loop_data['score'] > 0.4 else 'red'
            click.echo(f"   {click.style('Score:', dim=True)} {click.style(f'{loop_data['score']:.2f}', fg=score_color, bold=True)} / 1.0")
            
            if verbose:
                click.echo(f"   {click.style('Result:', dim=True)}\n{loop_data['result']}")
            else:
                summary_line = loop_data['result'].split('\n')[0][:80] + "..."
                click.echo(f"   {click.style('Result Snapshot:', dim=True)} {summary_line}")
            
            click.echo(f"{'-'*60}\n")
            
    except Exception as e:
        click.echo(f"\n❌ {click.style('Iteration Failed', fg='red', bold=True)}")
        click.echo(f"   Error: {e}")
    finally:
        PID_FILE.unlink(missing_ok=True)

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
@click.argument("loop_id", required=False)
def show(loop_id):
    """Show details of a specific loop (defaults to latest)."""
    from .memory import MemoryStore
    store = MemoryStore()
    
    if not loop_id:
        latest = store.list_loops(limit=1)
        if not latest:
            click.echo("No loops found.")
            return
        loop_id = latest[0]['id']
        click.echo(f"Showing latest loop: {loop_id}")

    loop = store.get_loop(loop_id)
    if not loop:
        click.echo(f"Loop {loop_id} not found.")
        return

    click.echo(f"\n{'='*60}")
    click.echo(f" LOOP ID: {loop['id']}")
    click.echo(f" STATUS:  {loop['status']}")
    click.echo(f" TIME:    {loop['created_at']}")
    click.echo(f"{'='*60}")
    click.echo(f" GOAL:    {loop['goal']}")
    click.echo(f"{'-'*60}")
    click.echo(f" SCORE:   {loop['score']} / 1.0")
    if loop['score_breakdown']:
        click.echo(f" BREAKDOWN: {loop['score_breakdown']}")
    click.echo(f"{'='*60}")
    click.echo(f" RESULT:\n{loop['result'] or '(No result recorded)'}")
    click.echo(f"{'='*60}\n")

@cli.command()
@click.option("--limit", default=10, help="Number of loops to include.")
def summary(limit):
    """Generate a Markdown summary of recent loop results."""
    from .memory import MemoryStore
    store = MemoryStore()
    loops = store.list_loops(limit=limit)
    
    summary_path = Path.cwd() / "SUMMARY.md"
    content = "# Telos Execution Summary\n\n"
    content += f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    content += "| ID | Goal | Score | Status |\n"
    content += "|---|---|---|---|\n"
    
    for l in loops:
        goal_short = (l['goal'][:50] + '...') if len(l['goal']) > 50 else l['goal']
        content += f"| {l['id'][:8]} | {goal_short} | {l['score'] or 'N/A'} | {l['status']} |\n"
    
    summary_path.write_text(content)
    click.echo(f"Summary generated at {summary_path}")

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

@cli.command()
@click.argument("loop_id", required=False)
def explain(loop_id):
    """Provide a narrative explanation of a loop's actions."""
    from .telos_core import AgentLoop
    from .memory import MemoryStore
    
    store = MemoryStore()
    if not loop_id:
        latest = store.list_loops(limit=1)
        if not latest:
            click.echo("No loops found.")
            return
        loop_id = latest[0]['id']
        click.echo(f"Explaining latest loop: {loop_id}")

    agent = AgentLoop()
    with click.progressbar(length=1, label="🤖 Generating explanation...") as bar:
        explanation = agent.explain_loop(loop_id)
        bar.update(1)
    
    click.echo(f"\n{click.style('📜 LOOP EXPLANATION', fg='cyan', bold=True)}")
    click.echo(f"{'-'*60}")
    click.echo(explanation)
    click.echo(f"{'-'*60}\n")

def main():
    cli()

if __name__ == '__main__':
    main()
