from __future__ import annotations

import re
from pathlib import Path


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
        score: float,
        goal: str,
        result: str,
        reasoning: str,
    ) -> None:
        icon = "✅" if score >= 0.5 else "❌"
        result_text = self._format_result(result, reasoning)
        reasoning_text = self._first_sentences(reasoning, max_chars=200)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"### Loop {loop_num} {icon} {score:.2f}\n")
            f.write(f"**Goal:** {goal}\n")
            f.write(f"**Result:** {result_text}\n")
            f.write(f"**Reasoning:** {reasoning_text}\n\n")

    def write_session_summary(self, loops: int, avg_score: float, cost_usd: float) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(f"---\n**Session Summary:** {loops} loops | avg score: {avg_score:.2f} | cost: ${cost_usd:.3f}\n---\n")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _format_result(self, output_path: str, reasoning: str) -> str:
        """Use output_path if present, otherwise fall back to reasoning excerpt."""
        if output_path and output_path.strip():
            return output_path.strip()[:200]
        return self._first_sentences(reasoning, max_chars=200)

    @staticmethod
    def _first_sentences(text: str, max_chars: int = 200) -> str:
        """Return the first 1-2 sentences of text, truncated at max_chars."""
        if not text:
            return ""
        # Split on sentence-ending punctuation followed by whitespace
        sentences = re.split(r"(?<=[。．.!?！？])\s*", text.strip())
        result = ""
        for s in sentences[:2]:
            candidate = (result + " " + s).strip() if result else s
            if len(candidate) > max_chars:
                return (result or candidate[:max_chars]).rstrip() + "..."
            result = candidate
        if len(result) > max_chars:
            return result[:max_chars].rstrip() + "..."
        return result
