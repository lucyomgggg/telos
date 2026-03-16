import os
import signal
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
    session_cost = 0.0
    try:
        for i in range(loops):
            click.echo(f"🔄 {click.style(f'Iteration {i+1}/{loops}', bold=True)} {'-'*40}")

            # Use a simple "thinking" indicator
            click.echo(f"🤖 {click.style('Agent is thinking...', dim=True)}")
            loop_data = agent.run_iteration()

            loop_cost = loop_data.get("cost_usd", 0.0) or 0.0
            loop_tokens = loop_data.get("tokens_used", 0) or 0
            session_cost += loop_cost

            # Print elegant summary
            click.echo(f"\n✅ {click.style('Iteration Complete', fg='green', bold=True)}")
            click.echo(f"   {click.style('ID:', dim=True)} {loop_data['id'][:8]}")
            click.echo(f"   {click.style('Goal:', dim=True)} {loop_data['goal']}")

            score_val = loop_data['score']
            score_color = 'green' if score_val > 0.7 else 'yellow' if score_val > 0.4 else 'red'
            click.echo(f"   {click.style('Score:', dim=True)} {click.style(f'{score_val:.2f}', fg=score_color, bold=True)} / 1.0")
            click.echo(f"   {click.style('Cost:', dim=True)} ${loop_cost:.4f}  {click.style(f'({loop_tokens:,} tokens)', dim=True)}")

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
        # Session cost summary + model breakdown
        click.echo(f"\n{'='*60}")
        click.echo(f"💰 {click.style('Session Cost Summary', fg='cyan', bold=True)}")
        click.echo(f"   {click.style('This session:', dim=True)} ${session_cost:.4f}")
        try:
            monthly = agent.cost_tracker.get_monthly_cost()
            click.echo(f"   {click.style('Month-to-date:', dim=True)} ${monthly:.4f}")
            stats = agent.sqlite.get_model_cost_stats()
            if stats:
                click.echo(f"\n   {click.style('Model Stack Breakdown:', dim=True)}")
                for s in stats:
                    click.echo(
                        f"   {s['model']:<45} [{s['agent_type']:<9}]"
                        f"  ${s['total_cost']:.4f}  ({s['total_tokens']:>8,} tok)"
                        f"  avg ${s['avg_cost_per_loop']:.4f}/loop"
                    )
        except Exception:
            pass
        click.echo(f"{'='*60}\n")
        agent.shutdown()
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
@click.option('--limit', default=10, help='Number of loops to show.')
def status(limit):
    """Show loop history, scores, and costs."""
    from .memory import MemoryStore
    memory = MemoryStore()
    loops = memory.list_loops(limit=limit)
    
    if not loops:
        click.echo(click.style("No loops recorded yet. Run 'telos start' to begin.", dim=True))
        return
        
    click.echo(f"\n{click.style('📊 TELOS EXECUTION HISTORY', fg='cyan', bold=True)}")
    click.echo(f"{click.style('ID', dim=True):<10} | {click.style('Status', dim=True):<10} | {click.style('Score', dim=True):<7} | {click.style('Cost', dim=True):<8} | {click.style('Goal', dim=True)}")
    click.echo(click.style("-" * 100, dim=True))
    
    for loop in loops:
        loop_id = loop['id'][:8]
        status_color = 'green' if loop['status'] == 'completed' else 'yellow' if loop['status'] == 'running' else 'red'
        status_str = click.style(loop['status'], fg=status_color)
        
        score_val = loop['score'] if loop['score'] is not None else 0.0
        score_color = 'green' if score_val > 0.7 else 'yellow' if score_val > 0.4 else 'red'
        score_str = click.style(f"{score_val:.2f}", fg=score_color) if loop['score'] is not None else click.style("N/A", dim=True)
        
        cost = f"${loop['cost_usd']:.4f}"
        goal = loop['goal'][:50] + "..." if len(loop['goal']) > 50 else loop['goal']
        
        click.echo(f"{loop_id:<10} | {status_str:<10} | {score_str:<7} | {cost:<8} | {goal}")
    click.echo("")

@cli.command()
@click.option('--lines', '-n', default=50, type=int, help='Number of lines to show')
@click.option('--follow', '-f', is_flag=True, help='Follow log output in real time')
def logs(lines, follow):
    """View agent logs."""
    if not LOG_FILE.exists():
        click.echo(click.style("No logs yet. Run 'telos start' first.", fg='yellow'))
        return
    
    if follow:
        import subprocess
        click.echo(f"Following {click.style(str(LOG_FILE), bold=True)} (Ctrl+C to stop)...")
        try:
            subprocess.run(["tail", "-f", "-n", str(lines), str(LOG_FILE)])
        except KeyboardInterrupt:
            pass
    else:
        with open(LOG_FILE, "r") as f:
            all_lines = f.readlines()
            tail = all_lines[-lines:]
            for line in tail:
                click.echo(line, nl=False)

@cli.command()
@click.argument("loop_id", required=False)
@click.option("--explain", is_flag=True, help="Provide a narrative explanation of the loop.")
def show(loop_id, explain):
    """Show details of a specific loop (defaults to latest)."""
    from .memory import MemoryStore
    from .telos_core import AgentLoop
    store = MemoryStore()
    
    if not loop_id:
        latest = store.list_loops(limit=1)
        if not latest:
            click.echo(click.style("No loops found.", fg='yellow'))
            return
        loop_id = latest[0]['id']
        click.echo(click.style(f"Showing latest loop: {loop_id}", dim=True))

    loop = store.get_loop(loop_id)
    if not loop:
        click.echo(click.style(f"Loop {loop_id} not found.", fg='red'))
        return

    click.echo(f"\n{click.style('═' * 80, fg='cyan')}")
    click.echo(f" {click.style('LOOP ID:', bold=True)} {loop['id']}")
    click.echo(f" {click.style('STATUS:', bold=True)}  {loop['status']}")
    click.echo(f" {click.style('TIME:', bold=True)}    {loop['created_at']}")
    click.echo(f"{click.style('═' * 80, fg='cyan')}")
    click.echo(f" {click.style('GOAL:', bold=True)}    {loop['goal']}")
    click.echo(f"{click.style('─' * 80, dim=True)}")
    
    score_val = loop['score'] if loop['score'] is not None else 0.0
    score_color = 'green' if score_val > 0.7 else 'yellow' if score_val > 0.4 else 'red'
    click.echo(f" {click.style('SCORE:', bold=True)}   {click.style(f'{score_val:.2f}', fg=score_color, bold=True)} / 1.0")
    
    if loop['score_breakdown']:
        click.echo(f" {click.style('BREAKDOWN:', dim=True)} {loop['score_breakdown']}")
    
    click.echo(f"{click.style('═' * 80, fg='cyan')}")
    
    if explain:
        click.echo(f" {click.style('📜 NARRATIVE EXPLANATION', fg='yellow', bold=True)}")
        agent = AgentLoop()
        with click.progressbar(length=1, label="🤖 Thinking...") as bar:
            explanation = agent.explain_loop(loop_id)
            bar.update(1)
        click.echo(explanation)
        click.echo(f"{click.style('═' * 80, fg='cyan')}")
    else:
        click.echo(f" {click.style('📄 EXECUTION RESULT', fg='yellow', bold=True)}")
        click.echo(loop['result'] or "(No result recorded)")
        click.echo(f"{click.style('═' * 80, fg='cyan')}\n")

@cli.command()
@click.option("--limit", default=5, help="Number of loops to include.")
@click.option("--output", "-o", default="REPORT.md", help="Path to save the report.")
def report(limit, output):
    """Generate a Markdown execution report."""
    from .memory import MemoryStore
    store = MemoryStore()
    loops = store.list_loops(limit=limit)
    
    if not loops:
        click.echo(click.style("No loops found to report.", fg='yellow'))
        return

    content = [
        f"# 🤖 Telos Execution Report",
        f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        "## 🔄 Recent Activity",
        "| ID | Created | Goal | Score | Status |",
        "|---|---|---|---|---|",
    ]
    
    for l in loops:
        score_val = f"{l['score']:.2f}" if l['score'] is not None else "N/A"
        goal_short = (l['goal'][:60] + '...') if len(l['goal']) > 60 else l['goal']
        content.append(f"| `{l['id'][:8]}` | {l['created_at']} | {goal_short} | {score_val} | {l['status']} |")
    
    content.append("\n## 📂 Workspace Artifacts")
    workspace_path = Path("workspace")
    if workspace_path.exists():
        files = list(workspace_path.glob("*"))
        if files:
            content.append("```")
            for f in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
                mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                content.append(f"{f.name:<30} | {mtime}")
            content.append("```")
        else:
            content.append("_ (Empty workspace) _")
    else:
        content.append("_ (Workspace directory not found) _")

    Path(output).write_text("\n".join(content))
    click.echo(f"✅ Report generated: {click.style(output, bold=True)}")

@cli.command()
@click.option('--yes', is_flag=True, help='Skip confirmation.')
def clean(yes):
    """Clear workspace files and temporary logs."""
    if not yes:
        if not click.confirm(click.style("⚠️ Are you sure you want to clear the workspace and logs?", fg='red')):
            return

    # Clear workspace
    workspace_path = Path("workspace")
    if workspace_path.exists():
        import shutil
        for item in workspace_path.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        click.echo("🧹 Workspace cleared.")

    # Clear logs
    if LOG_FILE.exists():
        LOG_FILE.unlink()
        click.echo("🧹 logs cleared.")
    
    click.echo(click.style("✨ Cleanup complete.", fg='green', bold=True))

@cli.command()
def dashboard():
    """Launch the interactive TUI dashboard."""
    from .dashboard.tui import TelosDashboard
    app = TelosDashboard()
    app.run()


def main():
    cli()

if __name__ == '__main__':
    main()
