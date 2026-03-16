"""Telos Terminal Dashboard — Textual TUI."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Static,
    TabbedContent,
    TabPane,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _score_color(score: float | None) -> str:
    if score is None:
        return "dim"
    if score >= 0.7:
        return "green"
    if score >= 0.4:
        return "yellow"
    return "red"


def _render_score(score: float | None) -> str:
    if score is None:
        return "[dim]N/A[/]"
    c = _score_color(score)
    return f"[{c}]{score:.2f}[/]"


def _build_score_chart(data: list[dict], width: int = 70, height: int = 16):
    """Render a score-over-time chart using plotext. Returns a Rich Text object."""
    from rich.text import Text
    try:
        import plotext as plt
    except ImportError:
        return Text("plotext not installed — run: pip install plotext")

    if not data:
        return Text("No data yet.")

    xs = [d["loop_number"] for d in data]
    ys = [d["score"] for d in data]

    plt.clf()
    plt.plot_size(width, height)
    plt.theme("dark")
    plt.title("Score Progression")
    plt.xlabel("Loop #")
    plt.ylabel("Score")
    plt.ylim(0, 1)

    # Moving average (window=5)
    window = 5
    ma: list[float] = []
    for i in range(len(ys)):
        start = max(0, i - window + 1)
        ma.append(sum(ys[start : i + 1]) / (i - start + 1))

    plt.scatter(xs, ys, marker="dot", color="white", label="score")
    plt.plot(xs, ma, color="cyan", label="avg(5)")

    buf = plt.build()
    if not buf:
        return Text("Chart unavailable")
    return Text.from_ansi(buf)


def _build_bar(value: float, width: int = 20, label: str = "") -> str:
    filled = int(value * width)
    bar = "█" * filled + "░" * (width - filled)
    c = _score_color(value)
    return f"[{c}]{bar}[/] {value:.2f}  {label}"


# ── widgets ──────────────────────────────────────────────────────────────────

class SummaryBar(Static):
    def update_data(self, summary: dict) -> None:
        total = summary.get("total_loops", 0)
        avg = summary.get("avg_score", 0.0)
        rate = summary.get("high_score_rate", 0.0)
        cost = summary.get("total_cost_usd", 0.0)
        failures = summary.get("failure_count", 0)
        text = (
            f"[bold cyan]Loops:[/] {total}   "
            f"[bold]Avg Score:[/] {_render_score(avg)}   "
            f"[bold green]≥0.7:[/] {rate:.1f}%   "
            f"[bold red]Failures:[/] {failures}   "
            f"[bold]Total Cost:[/] [yellow]${cost:.6f}[/]"
        )
        self.update(text)


class ScoreChart(Static):
    def update_data(self, progression: list[dict]) -> None:
        chart = _build_score_chart(progression)
        self.update(chart)


class BreakdownPanel(Static):
    def update_data(self, averages: dict) -> None:
        if not averages:
            self.update("[dim]No breakdown data yet.[/]")
            return
        lines = ["[bold]Rubric Axis Averages[/]\n"]
        for axis, val in sorted(averages.items()):
            lines.append(_build_bar(val, width=24, label=axis.capitalize()))
        self.update("\n".join(lines))


class GoalTable(DataTable):
    def populate(self, goals: list[dict]) -> None:
        self.clear(columns=True)
        self.add_columns("#", "Score", "Status", "Goal", "Date")
        for i, g in enumerate(goals, 1):
            score_str = f"{g['score']:.2f}" if g["score"] is not None else "N/A"
            goal_txt = g["goal"][:55] + "…" if len(g["goal"]) > 55 else g["goal"]
            date_txt = g["created_at"][:10]
            self.add_row(str(i), score_str, g["status"], goal_txt, date_txt)


class FailureCards(VerticalScroll):
    def populate(self, pairs: list[dict]) -> None:
        self.remove_children()
        if not pairs:
            self.mount(Static("[dim]No failure→improvement pairs detected yet.[/]"))
            return
        for p in pairs:
            f = p["failure"]
            imp = p["improvement"]
            delta = p["score_delta"]
            card_text = (
                f"[bold red]Loop {p['failure_loop_number']}[/]  "
                f"score=[red]{f['score']:.2f}[/]  "
                f"[italic]\"{f['goal'][:60]}\"[/]\n"
                f"  [dim]Lesson:[/] {f['lesson']}\n"
                f"  [bold cyan]↓ +{delta:.2f}[/]\n"
                f"[bold green]Loop {p['failure_loop_number'] + 1}[/]  "
                f"score=[green]{imp['score']:.2f}[/]  "
                f"[italic]\"{imp['goal'][:60]}\"[/]\n"
                f"  [dim]{imp['reasoning'][:120]}[/]\n"
                f"{'─' * 60}"
            )
            self.mount(Static(card_text, classes="failure-card"))


class CostTable(DataTable):
    def populate(self, stats: list[dict]) -> None:
        self.clear(columns=True)
        self.add_columns("Model", "Role", "Loops", "Avg Cost/Loop", "Avg Tokens/Loop")
        for s in stats:
            model_short = s["model"].split("/")[-1][:30]
            cost_str = f"${s['avg_cost_per_loop']:.6f}" if s["avg_cost_per_loop"] > 0 else "$0.00 (local)"
            self.add_row(
                model_short,
                s["agent_type"],
                str(s["loop_count"]),
                cost_str,
                str(s["avg_tokens_per_loop"]),
            )


class ProjectionPanel(Static):
    def update_data(self, stats: list[dict], summary: dict) -> None:
        total_per_loop = sum(s["avg_cost_per_loop"] for s in stats)
        total_tokens_per_loop = sum(s["avg_tokens_per_loop"] for s in stats)
        lines = ["[bold]Cost Projection[/]\n"]
        if total_per_loop > 0:
            lines.append(f"Avg cost / loop:  [yellow]${total_per_loop:.6f}[/]")
            lines.append(f"Next  10 loops:   [yellow]${total_per_loop * 10:.5f}[/]")
            lines.append(f"Next  50 loops:   [yellow]${total_per_loop * 50:.5f}[/]")
            lines.append(f"Next 100 loops:   [yellow]${total_per_loop * 100:.5f}[/]")
        else:
            lines.append("[green]All models are local (cost = $0.00)[/]")
        lines.append(f"\nAvg tokens / loop: {total_tokens_per_loop:,}")
        lines.append(f"Total loops so far: {summary.get('total_loops', 0)}")
        self.update("\n".join(lines))


# ── main app ─────────────────────────────────────────────────────────────────

class TelosDashboard(App):
    CSS = """
    Screen { background: $surface; }

    SummaryBar {
        height: 1;
        padding: 0 1;
        background: $panel;
    }

    ScoreChart {
        height: 18;
        padding: 0 1;
        border: solid $primary-darken-2;
    }

    BreakdownPanel {
        padding: 1 2;
        border: solid $primary-darken-2;
        height: auto;
        min-height: 8;
    }

    GoalTable {
        height: 1fr;
    }

    FailureCards {
        height: 1fr;
        padding: 1 2;
    }

    .failure-card {
        margin-bottom: 1;
    }

    CostTable {
        height: 1fr;
    }

    ProjectionPanel {
        padding: 1 2;
        border: solid $primary-darken-2;
        height: auto;
        min-height: 8;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("e", "export", "Export"),
    ]

    TITLE = "Telos Dashboard"

    def __init__(self):
        super().__init__()
        from ..memory import MemoryStore
        self._memory = MemoryStore()
        self._progression: list[dict] = []
        self._goals: list[dict] = []
        self._pairs: list[dict] = []
        self._cost_stats: list[dict] = []
        self._summary: dict = {}
        self._breakdown: dict = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield SummaryBar(id="summary-bar")
        with TabbedContent():
            with TabPane("Overview", id="tab-overview"):
                yield ScoreChart(id="score-chart")
                yield BreakdownPanel(id="breakdown-panel")
            with TabPane("Goals", id="tab-goals"):
                yield GoalTable(id="goal-table")
            with TabPane("Learning", id="tab-learning"):
                yield FailureCards(id="failure-cards")
            with TabPane("Costs", id="tab-costs"):
                yield CostTable(id="cost-table")
                yield ProjectionPanel(id="projection-panel")
        yield Footer()

    def on_mount(self) -> None:
        self._load_data()
        self.set_interval(5, self._load_data)

    def _load_data(self) -> None:
        try:
            self._summary = self._memory.get_dashboard_summary()
            self._progression = self._memory.get_score_progression(limit=100)
            self._goals = self._memory.get_goal_diversity(limit=200)
            self._pairs = self._memory.get_failure_improvement_pairs()
            self._cost_stats = self._memory.get_model_cost_stats()
            self._breakdown = self._memory.get_score_breakdown_averages()
        except Exception as e:
            self.query_one("#summary-bar", SummaryBar).update(f"[red]DB Error: {e}[/]")
            return

        self.query_one("#summary-bar", SummaryBar).update_data(self._summary)

        # Only update the currently active tab (inactive tabs are not mounted yet)
        try:
            active = self.query_one(TabbedContent).active
            self._update_tab(active)
        except Exception:
            pass

    def _update_tab(self, tab_id: str) -> None:
        """Update widgets for the given tab only."""
        if tab_id == "tab-overview":
            self.query_one("#score-chart", ScoreChart).update_data(self._progression)
            self.query_one("#breakdown-panel", BreakdownPanel).update_data(self._breakdown)
        elif tab_id == "tab-goals":
            self.query_one("#goal-table", GoalTable).populate(self._goals)
        elif tab_id == "tab-learning":
            self.query_one("#failure-cards", FailureCards).populate(self._pairs)
        elif tab_id == "tab-costs":
            self.query_one("#cost-table", CostTable).populate(self._cost_stats)
            self.query_one("#projection-panel", ProjectionPanel).update_data(
                self._cost_stats, self._summary
            )

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        """Populate a tab's widgets when it becomes active."""
        if event.tab:
            self._update_tab(event.tab.id)

    def action_refresh(self) -> None:
        self._load_data()

    def action_export(self) -> None:
        try:
            active = self.query_one(TabbedContent).active
            path = self._export_tab(active)
            self.notify(f"Exported → {path}", title="Export", severity="information")
        except Exception as e:
            self.notify(str(e), title="Export failed", severity="error")

    def _export_tab(self, tab_id: str) -> str:
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        if tab_id == "tab-overview":
            return self._export_overview(ts)
        elif tab_id == "tab-goals":
            return self._export_goals(ts)
        elif tab_id == "tab-learning":
            return self._export_learning(ts)
        elif tab_id == "tab-costs":
            return self._export_costs(ts)
        raise ValueError(f"Unknown tab: {tab_id}")

    def _export_overview(self, ts: str) -> str:
        import csv, io
        lines = [
            f"# Telos Score Progression Export ({ts})\n",
            f"Total loops: {self._summary.get('total_loops', 0)}  ",
            f"Avg score: {self._summary.get('avg_score', 0):.3f}  ",
            f"High-score rate: {self._summary.get('high_score_rate', 0):.1f}%  ",
            f"Total cost: ${self._summary.get('total_cost_usd', 0):.6f}\n",
            "## Score Progression\n",
        ]
        # CSV block
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["loop_number", "score", "status", "goal", "created_at"])
        for d in self._progression:
            w.writerow([d["loop_number"], d["score"], d["status"], d["goal"], d["created_at"]])
        lines.append("```csv")
        lines.append(buf.getvalue().strip())
        lines.append("```\n")
        lines.append("## Rubric Axis Averages\n")
        lines.append("| Axis | Avg Score |")
        lines.append("|---|---|")
        for axis, val in sorted(self._breakdown.items()):
            lines.append(f"| {axis.capitalize()} | {val:.3f} |")
        path = f"telos_overview_{ts}.md"
        with open(path, "w") as f:
            f.write("\n".join(lines))
        return path

    def _export_goals(self, ts: str) -> str:
        import csv
        path = f"telos_goals_{ts}.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["#", "score", "status", "goal", "created_at"])
            for i, g in enumerate(self._goals, 1):
                w.writerow([i, g["score"], g["status"], g["goal"], g["created_at"]])
        return path

    def _export_learning(self, ts: str) -> str:
        lines = [f"# Telos Failure → Improvement Pairs ({ts})\n"]
        if not self._pairs:
            lines.append("No failure→improvement pairs detected yet.")
        for p in self._pairs:
            f, imp = p["failure"], p["improvement"]
            lines += [
                f"## Loop {p['failure_loop_number']} → {p['failure_loop_number'] + 1}  (+{p['score_delta']:.2f})\n",
                f"**Failure** score={f['score']:.2f}  \"{f['goal']}\"",
                f"> Lesson: {f['lesson']}\n",
                f"**Improvement** score={imp['score']:.2f}  \"{imp['goal']}\"",
                f"> {imp['reasoning'][:200]}\n",
                "---\n",
            ]
        path = f"telos_learning_{ts}.md"
        with open(path, "w") as f:
            f.write("\n".join(lines))
        return path

    def _export_costs(self, ts: str) -> str:
        lines = [
            f"# Telos Cost Report ({ts})\n",
            "## Model Cost Breakdown\n",
            "| Model | Role | Loops | Avg Cost/Loop | Avg Tokens/Loop |",
            "|---|---|---|---|---|",
        ]
        for s in self._cost_stats:
            cost_str = f"${s['avg_cost_per_loop']:.6f}" if s["avg_cost_per_loop"] > 0 else "$0.00 (local)"
            lines.append(
                f"| {s['model']} | {s['agent_type']} | {s['loop_count']} "
                f"| {cost_str} | {s['avg_tokens_per_loop']:,} |"
            )
        total_per_loop = sum(s["avg_cost_per_loop"] for s in self._cost_stats)
        lines += [
            "\n## Projections\n",
            f"| Loops | Estimated Cost |",
            f"|---|---|",
            f"| 10 | ${total_per_loop * 10:.5f} |",
            f"| 50 | ${total_per_loop * 50:.5f} |",
            f"| 100 | ${total_per_loop * 100:.5f} |",
        ]
        path = f"telos_costs_{ts}.md"
        with open(path, "w") as f:
            f.write("\n".join(lines))
        return path

    def action_quit(self) -> None:
        self.exit()
