import os
import signal
from pathlib import Path
from datetime import datetime
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
      telos project current                  # check active project
      telos start --loops 5 --name my-run   # run 5 loops
      telos status                           # see session history
      telos show                             # inspect latest loop
      telos export --format csv             # export latest session
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
@click.option('--verbose', is_flag=True, help='Print full result output for each loop.')
def start(model, loops, verbose, name):
    """Run autonomous loops.

    Each invocation creates a session that groups all loops together.
    Results are stored in data/telos.db and can be exported with 'telos export'.
    """
    from .telos_core import AgentLoop
    from .config import PID_FILE

    selected_model = model or settings.llm.producer_model
    click.echo(f"  Model : {click.style(selected_model, fg='cyan')}")
    click.echo(f"  Loops : {loops}")
    if name:
        click.echo(f"  Name  : {name}")
    click.echo("")

    agent = AgentLoop(session_name=name, intended_loops=loops)

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
@click.option('--limit', '-n', default=20, help='Number of entries to show.  [default: 20]')
@click.option('--loops', 'show_loops', is_flag=True, help='Show individual loop history instead of sessions.')
def status(limit, show_loops):
    """Show run history (sessions by default, loops with --loops).

    \b
    telos status            # session list
    telos status --loops    # individual loop list
    """
    from .memory import MemoryStore
    store = MemoryStore()

    click.echo(click.style(f"Project: {_active_project_name()}", dim=True))

    if show_loops:
        loops = store.list_loops(limit=limit)
        if not loops:
            click.echo(click.style("No loops recorded yet.", dim=True))
            return
        click.echo(f"\n{click.style('LOOP HISTORY', fg='cyan', bold=True)}")
        header = f"{'ID':<10}  {'Status':<10}  {'Score':>6}  {'Cost':>8}  Goal"
        click.echo(click.style(header, dim=True))
        click.echo(click.style("-" * 90, dim=True))
        for loop in loops:
            lid = loop['id'][:8]
            st = loop['status']
            st_color = 'green' if st == 'completed' else 'yellow' if st == 'running' else 'red'
            st_str = click.style(f"{st:<10}", fg=st_color)
            score_val = loop['score'] if loop['score'] is not None else 0.0
            score_color = 'green' if score_val > 0.7 else 'yellow' if score_val > 0.4 else 'red'
            score_str = click.style(f"{score_val:.2f}", fg=score_color) if loop['score'] is not None else click.style("  N/A", dim=True)
            cost = f"${loop['cost_usd']:.4f}"
            goal = loop['goal'][:55] + "..." if len(loop['goal']) > 55 else loop['goal']
            click.echo(f"{lid:<10}  {st_str}  {score_str:>6}  {cost:>8}  {goal}")
        click.echo("")
    else:
        rows = store.list_sessions(limit=limit)
        if not rows:
            click.echo(click.style("No sessions recorded yet. Run 'telos start' to begin.", dim=True))
            return
        click.echo(f"\n{click.style('SESSION HISTORY', fg='cyan', bold=True)}")
        header = f"{'ID':<10}  {'Name':<28}  {'Status':<10}  {'Loops':>7}  {'Score':>6}  {'Cost':>8}  Model"
        click.echo(click.style(header, dim=True))
        click.echo(click.style("-" * 100, dim=True))
        for s in rows:
            sid = s['id'][:8]
            name = (s['name'] or '')[:28]
            st = s['status']
            st_color = 'green' if st == 'completed' else 'yellow' if st == 'running' else 'red'
            st_str = click.style(f"{st:<10}", fg=st_color)
            loops_str = f"{s['completed_loops']}/{s['intended_loops']}"
            avg = f"{s['avg_score']:.2f}" if s['avg_score'] is not None else "  N/A"
            cost = f"${s['total_cost_usd']:.4f}"
            model_str = (s.get('producer_model') or '')[:35]
            click.echo(f"{sid:<10}  {name:<28}  {st_str}  {loops_str:>7}  {avg:>6}  {cost:>8}  {model_str}")
        click.echo("")

@cli.command()
@click.option('--lines', '-n', default=50, type=int, help='Number of lines to show.  [default: 50]')
@click.option('--follow', '-f', is_flag=True, help='Stream new log lines in real time (Ctrl+C to stop).')
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
@click.argument("loop_id", required=False, metavar="[LOOP_ID]")
@click.option("--explain", is_flag=True, help="Generate a narrative explanation using the LLM.")
def show(loop_id, explain):
    """Inspect a loop result in detail.

    LOOP_ID can be the full UUID or the first 8 characters.
    Defaults to the most recent loop if omitted.
    """
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
@click.option("--output", "-o", default=None, help="Output file path (default: report_<project>_<timestamp>.md).")
@click.option("--top", default=5, help="Number of top-scoring loops to feature.  [default: 5]")
def report(output, top):
    """Generate a full Markdown report of all activity."""
    from .memory import MemoryStore
    store = MemoryStore()

    if output is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = f"report_{_active_project_name()}_{ts}.md"

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
@click.argument("session_id", required=False, metavar="[SESSION_ID]")
@click.option("--format", "fmt", type=click.Choice(["json", "csv"]), default="json",
              help="Output format.  [default: json]")
@click.option("--output", "-o", default=None, help="Output file path (default: session_<id>.json/csv).")
def export(session_id, fmt, output):
    """Export session data to JSON or CSV.

    SESSION_ID is optional — defaults to the latest session.
    Accepts the full UUID or just the first 8 characters.

    \b
    telos export                          # latest session → JSON
    telos export abc12345 --format csv    # specific session → CSV
    telos export -o results.json          # save to custom path
    """
    import json as _json
    from .memory import MemoryStore
    store = MemoryStore()

    if not session_id:
        latest = store.list_sessions(limit=1)
        if not latest:
            click.echo(click.style("No sessions found.", fg='yellow'))
            return
        session_id = latest[0]['id']
        click.echo(click.style(f"Exporting latest session: {session_id[:8]}", dim=True), err=True)
    else:
        # Resolve short (8-char) IDs to full UUID via get_session
        resolved = store.get_session(session_id)
        if not resolved:
            click.echo(click.style(f"Session '{session_id}' not found.", fg='red'))
            return
        session_id = resolved['id']

    if fmt == "json":
        data = store.export_session_json(session_id)
        if not data:
            click.echo(click.style(f"Session {session_id[:8]} not found.", fg='red'))
            return
        content = _json.dumps(data, indent=2, default=str)
        default_filename = f"session_{session_id[:8]}.json"
    else:
        content = store.export_session_csv(session_id)
        if not content:
            click.echo(click.style(f"Session {session_id[:8]} not found.", fg='red'))
            return
        default_filename = f"session_{session_id[:8]}.csv"

    out_path = Path(output) if output else Path(default_filename)
    out_path.write_text(content)
    click.echo(click.style(f"Exported to {out_path}", fg='green'))


@cli.command()
def dashboard():
    """Open the interactive TUI dashboard."""
    from .dashboard.tui import TelosDashboard
    app = TelosDashboard()
    app.run()


@cli.command()
@click.option('--yes', is_flag=True, help='Skip confirmation prompt.')
def reset(yes):
    """Wipe the current project's data and start fresh from loop 1.

    Deletes the database, workspace files, and agent log for the active project.
    The project directory itself is kept. Use 'telos project delete' to remove a project entirely.
    """
    current = _active_project_name()
    click.echo(f"Active project: {click.style(current, fg='cyan', bold=True)}")

    if not yes:
        click.echo(click.style("⚠️  This will permanently delete:", fg='yellow'))
        click.echo("   • database (all sessions, loops, audit logs)")
        click.echo("   • workspace files")
        click.echo("   • agent log")
        if not click.confirm(click.style("\nAre you sure?", fg='red')):
            click.echo("Aborted.")
            return

    import shutil

    db_file = TELOS_HOME / "telos.db"
    if db_file.exists():
        db_file.unlink()
        click.echo("🗑  Database deleted.")

    workspace_path = Path(settings.memory.workspace_path)
    if workspace_path.exists():
        shutil.rmtree(workspace_path)
        workspace_path.mkdir(parents=True, exist_ok=True)
        click.echo("🧹 Workspace cleared.")

    if LOG_FILE.exists():
        LOG_FILE.unlink()
        click.echo("🧹 Log cleared.")

    click.echo(click.style("\n✨ Reset complete. Run 'telos start' to begin fresh.", fg='green', bold=True))


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
    telos project current              # show active project
    telos project list                 # list all projects
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


@project.command("current")
def project_current():
    """Show the active project and its stats."""
    name = _active_project_name()
    home = Path(os.getenv("TELOS_HOME", str(TELOS_HOME)))
    click.echo(f"\n{click.style('Active project:', bold=True)} {click.style(name, fg='cyan', bold=True)}")
    click.echo(f"{click.style('Data directory:', dim=True)} {home}")
    stats = _project_stats(home)
    click.echo(f"{click.style('Stats:', dim=True)}          {stats}")
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
