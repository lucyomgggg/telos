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
@click.option("--output", "-o", default="REPORT.md", help="Path to save the report.")
@click.option("--top", default=5, help="Number of top-performing loops to highlight.")
def report(output, top):
    """Generate a comprehensive Markdown report of all loop activity."""
    from .memory import MemoryStore
    store = MemoryStore()

    summary = store.get_dashboard_summary()
    if summary["total_loops"] == 0:
        click.echo(click.style("No loops found to report.", fg='yellow'))
        return

    progression = store.get_score_progression(limit=500)
    breakdown_avgs = store.get_score_breakdown_averages()
    all_goals = store.get_goal_diversity(limit=500)
    learning_pairs = store.get_failure_improvement_pairs(limit=10)
    cost_stats = store.get_model_cost_stats()

    s = summary
    lines = [
        "# Telos Execution Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"Initial intent: *{settings.initial_intent}*",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total loops | {s['total_loops']} |",
        f"| Average score | {s['avg_score']:.3f} |",
        f"| High score rate (≥ 0.7) | {s['high_score_rate']}% ({s['high_score_count']} loops) |",
        f"| Failures (≤ 0.3) | {s['failure_count']} loops |",
        f"| Total cost | ${s['total_cost_usd']:.4f} |",
        "",
    ]

    # Score progression table
    lines += [
        "---",
        "",
        "## Score Progression",
        "",
        "| # | Goal | Score | Status |",
        "|---|---|---|---|",
    ]
    for p in progression:
        score_str = f"{p['score']:.2f}"
        goal_short = (p['goal'][:70] + "…") if len(p['goal']) > 70 else p['goal']
        lines.append(f"| {p['loop_number']} | {goal_short} | {score_str} | {p['status']} |")
    lines.append("")

    # Rubric axis breakdown
    if breakdown_avgs:
        lines += [
            "---",
            "",
            "## Rubric Axis Averages",
            "",
            "| Axis | Average Score |",
            "|---|---|",
        ]
        for axis, avg in sorted(breakdown_avgs.items(), key=lambda x: -x[1]):
            bar = "█" * int(avg * 20)
            lines.append(f"| {axis} | {avg:.3f}  `{bar:<20}` |")
        lines.append("")

    # Top performers
    top_loops = sorted(
        [p for p in progression if p["score"] is not None],
        key=lambda x: x["score"],
        reverse=True,
    )[:top]
    if top_loops:
        lines += [
            "---",
            "",
            f"## Top {len(top_loops)} Performers",
            "",
        ]
        for i, lp in enumerate(top_loops, 1):
            loop_detail = store.get_loop(lp["id"])
            lines.append(f"### {i}. {lp['goal']}")
            lines.append(f"**Score:** {lp['score']:.3f} &nbsp; **Loop:** #{lp['loop_number']} &nbsp; **Status:** {lp['status']}")
            if loop_detail and loop_detail.get("score_breakdown"):
                bd = loop_detail["score_breakdown"]
                bd_str = " | ".join(f"{k}: {v:.2f}" for k, v in bd.items())
                lines.append(f"*{bd_str}*")
            if loop_detail and loop_detail.get("reasoning"):
                reasoning = loop_detail["reasoning"][:300]
                if len(loop_detail["reasoning"]) > 300:
                    reasoning += "…"
                lines.append(f"> {reasoning}")
            lines.append("")

    # Learning moments
    if learning_pairs:
        lines += [
            "---",
            "",
            "## Learning Moments (Failure → Improvement)",
            "",
        ]
        for pair in learning_pairs:
            f_loop = pair["failure"]
            i_loop = pair["improvement"]
            delta = pair["score_delta"]
            lines.append(f"### Loop #{pair['failure_loop_number']} → #{pair['failure_loop_number'] + 1}  (+{delta:.2f})")
            lines.append(f"**Failed:** {f_loop['goal']} — score {f_loop['score']:.2f}")
            lines.append(f"> {f_loop['lesson']}")
            lines.append(f"")
            lines.append(f"**Improved:** {i_loop['goal']} — score {i_loop['score']:.2f}")
            if i_loop.get("reasoning"):
                r = i_loop["reasoning"][:200]
                if len(i_loop["reasoning"]) > 200:
                    r += "…"
                lines.append(f"> {r}")
            lines.append("")

    # All goals (collapsed list)
    lines += [
        "---",
        "",
        "## All Goals",
        "",
        "| # | Goal | Score | Status | Date |",
        "|---|---|---|---|---|",
    ]
    for i, g in enumerate(reversed(all_goals), 1):
        score_str = f"{g['score']:.2f}" if g["score"] is not None else "—"
        date_str = g["created_at"][:16] if g.get("created_at") else "—"
        goal_short = (g['goal'][:60] + "…") if len(g['goal']) > 60 else g['goal']
        lines.append(f"| {i} | {goal_short} | {score_str} | {g['status']} | {date_str} |")
    lines.append("")

    # Cost analysis
    if cost_stats:
        lines += [
            "---",
            "",
            "## Cost Analysis",
            "",
            "| Model | Role | Loops | Total Cost | Avg/Loop | Total Tokens |",
            "|---|---|---|---|---|---|",
        ]
        for c in cost_stats:
            model_short = c["model"][-50:] if len(c["model"]) > 50 else c["model"]
            lines.append(
                f"| `{model_short}` | {c['agent_type']} | {c['loop_count']} "
                f"| ${c['total_cost']:.4f} | ${c['avg_cost_per_loop']:.4f} | {c['total_tokens']:,} |"
            )
        lines.append("")

    # Workspace artifacts
    lines += ["---", "", "## Workspace Artifacts", ""]
    workspace_path = Path(settings.memory.workspace_path) / settings.memory.persistent_workspace_name
    if not workspace_path.exists():
        workspace_path = Path("workspace")
    if workspace_path.exists():
        all_files = sorted(workspace_path.rglob("*"), key=lambda x: x.stat().st_mtime, reverse=True)
        files = [f for f in all_files if f.is_file()]
        if files:
            lines.append("| File | Size | Modified |")
            lines.append("|---|---|---|")
            for f in files:
                rel = f.relative_to(workspace_path.parent)
                size = f.stat().st_size
                size_str = f"{size:,} B" if size < 1024 else f"{size // 1024:,} KB"
                mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
                lines.append(f"| `{rel}` | {size_str} | {mtime} |")
        else:
            lines.append("*(empty)*")
    else:
        lines.append("*(workspace directory not found)*")

    Path(output).write_text("\n".join(lines))
    click.echo(click.style(f"Report saved: {output}", fg='green', bold=True))
    click.echo(f"  {s['total_loops']} loops · avg score {s['avg_score']:.3f} · ${s['total_cost_usd']:.4f} total")

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
