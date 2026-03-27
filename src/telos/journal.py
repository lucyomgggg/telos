from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional


class JournalWriter:
    """Appends structured session/loop entries to a per-project JOURNAL.md."""

    def __init__(self, project_path: Path, project_name: str) -> None:
        self.path = project_path / "JOURNAL.md"
        if not self.path.exists():
            self.path.write_text(f"# Telos Journal — {project_name}\n")

    def write_session_header(self, session_id: str, timestamp: str, model: str) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"\n---\n\n## Session {session_id[:8]} | {timestamp} | {model}\n\n")

    def write_loop(
        self,
        loop_num: int,
        goal: str,
        output_path: str,
        instincts_pre: Dict[str, float],
        instincts_post: Dict[str, float],
        output_stats: Optional[Dict] = None,
    ) -> None:
        def fmt(d: Dict) -> str:
            return (
                f"curiosity={d.get('curiosity', 0):.2f} | "
                f"preservation={d.get('preservation', 0):.2f} | "
                f"growth={d.get('growth', 0):.2f} | "
                f"order={d.get('order', 0):.2f}"
            )

        stats_text = ""
        if output_stats:
            loc = output_stats.get("loc", 0)
            funcs = output_stats.get("function_count", 0)
            imports = output_stats.get("import_count", 0)
            builds = output_stats.get("builds_on_previous", False)
            stats_text = f"\n**Output:** {loc} LOC, {funcs} functions, {imports} imports"
            if builds:
                stats_text += ", builds on previous work"

        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"### Loop {loop_num}\n")
            f.write(f"**Goal:** {goal}\n")
            f.write(f"**Pre-instincts:** {fmt(instincts_pre)}\n")
            f.write(f"**Post-instincts:** {fmt(instincts_post)}\n")
            if output_path:
                f.write(f"**Artifact:** {output_path.strip()[:200]}\n")
            if stats_text:
                f.write(f"{stats_text}\n")
            f.write("\n")

    def write_session_summary(
        self,
        loops: int,
        cost_usd: float,
        final_instincts: Optional[Dict[str, float]] = None,
    ) -> None:
        instinct_str = ""
        if final_instincts:
            instinct_str = (
                f" | final instincts: "
                f"C={final_instincts.get('curiosity', 0):.2f} "
                f"P={final_instincts.get('preservation', 0):.2f} "
                f"G={final_instincts.get('growth', 0):.2f} "
                f"O={final_instincts.get('order', 0):.2f}"
            )
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"---\n**Session Summary:** {loops} loops | cost: ${cost_usd:.3f}{instinct_str}\n---\n")
