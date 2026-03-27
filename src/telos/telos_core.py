import os
import json
import uuid
import time
from datetime import datetime, timezone
from typing import List, Optional, Any, Dict, Tuple

from .config import settings, TELOS_HOME
from .journal import JournalWriter
from .logger import get_logger
from .memory import MemoryStore, VectorStore
from .db_models import SessionRecord
from .llm import LLMService
from .usage import CostTracker
from .schemas import GoalSchema
from .interfaces import Tool, Critic, TemplateLoader
from .agents import BaseAgent
from .tools import ToolRegistry
from .sandbox import SandboxManager
from .critic import CriticAgent

log = get_logger("core")

class GoalGenerator(BaseAgent):
    def __init__(self, cost_tracker: Any = None):
        super().__init__(agent_type="goal_gen", cost_tracker=cost_tracker)
        from .deduplicator import GoalDeduplicator
        self.deduplicator = GoalDeduplicator()

    def generate(self, initial_intent: str, history: List[Dict], similar: List[Dict],
                 loop_count: int = 0, workspace_state: list = None) -> GoalSchema:
        history_text = "\n".join([f"- Goal: {h['goal']} | Score: {h['score']}" for h in history])
        similar_text = ""
        if similar:
            similar_text = "\nPast artifacts found in vector memory:\n" + "\n".join(
                [f"- {s.get('payload', {}).get('goal', 'N/A')} "
                 f"(score: {s.get('payload', {}).get('score', '?')})"
                 for s in similar]
            )

        workspace_section = ""
        if workspace_state:
            file_lines = "\n".join(f"- {f['path']} (loop {f['loop_id']})" for f in workspace_state)
            workspace_section = (
                f"\n\nCURRENT WORKSPACE\n"
                f"以下のファイルが既に存在する:\n{file_lines}\n\n"
                f"制約:\n"
                f"- 上記のファイルを無視した独立したスクリプトの新規作成は禁止。\n"
                f"- 既存ファイルの拡張、複数ファイルの統合、既存システムの改善のいずれかであること。"
            )

        user_prompt = (
            f"Ambient Intent: {initial_intent}\n\n"
            f"Token Budget: {settings.llm.max_tokens_per_loop} tokens per loop — "
            f"generate ATOMIC goals (single file, <50 lines) that fit within this budget.\n\n"
            f"Recent History:\n{history_text}{similar_text}"
            f"{workspace_section}\n\n"
            f"Decision: Generate the next goal."
        )
        system_prompt = self.load_template("goal_generation_system", "Generate a new and distinct goal.")

        past_titles = [h["goal"] for h in history if h.get("goal")]

        try:
            goal = self.chat_structured(
                messages=[{"role": "user", "content": user_prompt}],
                response_model=GoalSchema,
                system=system_prompt
            )
            if self.deduplicator.is_duplicate(goal.title, past_titles, loop_count=loop_count):
                log.info("Goal '%s' is a duplicate; requesting a more novel goal.", goal.title)
                novel_prompt = (user_prompt +
                                f"\n\nNOTE: The proposed goal '{goal.title}' is too similar to recent history. "
                                "Please generate a more distinct and novel goal.")
                goal = self.chat_structured(
                    messages=[{"role": "user", "content": novel_prompt}],
                    response_model=GoalSchema,
                    system=system_prompt
                )
            return goal
        except Exception as e:
            log.error(f"Goal generation failed: {e}")
            return GoalSchema(
                title="Explore the system",
                success_criteria=["Any file is created"],
                output_path="output.txt"
            )

class ProducerAgent(BaseAgent):
    def __init__(self, cost_tracker: Any = None):
        super().__init__(agent_type="producer", cost_tracker=cost_tracker)

    def execute_goal(self, loop_id: str, goal: GoalSchema, registry: ToolRegistry, lessons: List[str]) -> Tuple[List[Dict], str]:
        user_content = (
            f"Goal: {goal.title}\n"
            f"Success Criteria:\n" + "\n".join(f"- {c}" for c in goal.success_criteria) +
            f"\nOutput must be saved to: {goal.output_path}"
        )
        messages = [{"role": "user", "content": user_content}]
        system_prompt = self.load_template("producer_system", "Execute the goal.")
        
        if lessons:
            system_prompt += "\n\nCRITICAL LESSONS FROM RECENT FAILURES:\n" + "\n".join(lessons[:2])

        final_result = ""
        consecutive_errors = 0
        total_tokens = 0
        had_tool_call = False

        for step in range(self.settings.max_steps):
            if step > 0:
                time.sleep(self.settings.rate_limit_delay)

            response = self.chat(
                system=system_prompt,
                messages=messages,
                loop_id=loop_id
            )

            usage = getattr(response, 'usage', None)
            if usage:
                total_tokens += usage.total_tokens
                if not self.llm.validate_token_limit(total_tokens):
                    log.warning(f"Loop {loop_id} exceeded token limit. Aborting.")
                    final_result = "Loop aborted: Exceeded token limit."
                    break

            msg = response.choices[0].message
            messages.append(msg.model_dump())

            if msg.tool_calls:
                had_tool_call = True
                final_result, consecutive_errors = self._handle_tool_calls(
                    msg.tool_calls, messages, registry, consecutive_errors
                )
                if consecutive_errors >= self.settings.consecutive_error_limit or final_result.startswith("TASK_COMPLETE:"):
                    break
            else:
                final_result = msg.content or "Completed."
                if had_tool_call:
                    # Text response after tool use = task is done
                    break
                # No tool calls yet: LLM is in planning phase, continue the loop
        
        return messages, final_result or "Loop reached max steps."

    def _truncate_tool_output(self, result: str) -> str:
        """Truncate tool output, flagging JSON clearly to avoid confusing the agent."""
        limit = self.settings.max_output_truncation
        if len(result) <= limit:
            return result
        stripped = result.lstrip()
        if stripped.startswith('{') or stripped.startswith('['):
            return result[:limit] + "\n... [JSON TRUNCATED — output exceeded max_output_truncation]"
        return result[:limit] + "... (truncated)"

    def _handle_tool_calls(self, tool_calls, messages, registry, consecutive_errors) -> Tuple[str, int]:
        final_result = ""
        for tool_call in tool_calls:
            name = tool_call.function.name
            raw_args = tool_call.function.arguments
            tool = registry.get(name)

            try:
                from .utils import repair_json
                args = json.loads(repair_json(raw_args))
                result = tool.execute(args) if tool else f"Error: Tool {name} not found."
            except Exception as e:
                result = f"Error: {e}"

            if result.startswith("TASK_COMPLETE:"):
                final_result = result

            result = self._truncate_tool_output(result)

            messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": name, "content": result})
            consecutive_errors = (consecutive_errors + 1) if "Error" in result else 0

            if consecutive_errors >= self.settings.consecutive_error_limit:
                final_result = "Loop aborted: Consecutive tool errors exceeded limit."
                break
        return final_result, consecutive_errors

class Orchestrator:
    def __init__(self, session_name: Optional[str] = None, intended_loops: int = 1):
        from .config import PROJECT_ROOT

        self.sqlite = MemoryStore()
        self.vector = VectorStore()
        self.cost_tracker = CostTracker(self.sqlite)

        # Persistent workspace survives across loops within the same session
        cfg = settings.load()
        persistent_ws = PROJECT_ROOT / cfg.memory.workspace_path / cfg.memory.persistent_workspace_name
        persistent_ws.mkdir(parents=True, exist_ok=True)
        self.sandbox = SandboxManager(workspace_dir=str(persistent_ws))
        self.registry = ToolRegistry(self.sandbox)

        self.goal_gen = GoalGenerator(self.cost_tracker)
        self.producer = ProducerAgent(self.cost_tracker)
        self.critic = CriticAgent(cost_tracker=self.cost_tracker)

        # Create session record
        self.session_id = str(uuid.uuid4())
        session_ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        session_rec = SessionRecord(
            id=self.session_id,
            name=session_name or f"session-{session_ts}",
            producer_model=cfg.llm.producer_model,
            critic_model=cfg.llm.critic_model,
            goal_gen_model=cfg.llm.goal_gen_model,
            intended_loops=intended_loops,
            status="running",
        )
        self.sqlite.create_session(session_rec)
        log.info("Session %s started: %s", self.session_id[:8], session_rec.name)

        self._loop_num = 0
        self.journal = JournalWriter(TELOS_HOME, TELOS_HOME.name)
        self.journal.write_session_header(
            self.session_id,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            cfg.llm.producer_model,
        )

    def run_iteration(self, intent: Optional[str] = None) -> Dict:
        if intent is None:
            intent = settings.initial_intent
        loop_id = str(uuid.uuid4())
        _loop_record_created = False
        try:
            self._check_safety()
            self.sandbox.start()

            # 1. Goal Generation
            history = self.sqlite.get_quality_history()
            loop_count = self.sqlite.count_loops()
            # Use the most recent goal title as the Qdrant query to avoid intent-bias amplification.
            # The initial_intent acts as a north star in the GoalGenerator prompt; we don't want it
            # to also act as a Qdrant attractor that reinforces the same domain every loop.
            recent_scores = [h["score"] for h in history[-3:] if h.get("score") is not None]
            if len(recent_scores) >= 3 and all(s < settings.failure_threshold for s in recent_scores):
                log.info("3 consecutive failures detected; escaping domain attractor to initial intent.")
                qdrant_query = intent
            else:
                qdrant_query = history[-1]["goal"] if history else intent
            similar = self.vector.search_similar(qdrant_query, limit=settings.similar_artifacts_limit)
            workspace_state = self.sandbox.list_files()
            goal = self.goal_gen.generate(intent, history, similar, loop_count=loop_count, workspace_state=workspace_state)
            # Prefix output_path with the short loop_id to prevent cross-loop file collisions.
            if goal.output_path and not goal.output_path.startswith(loop_id[:8]):
                goal.output_path = f"{loop_id[:8]}/{goal.output_path}"

            self.sqlite.save_loop({"id": loop_id, "goal": goal.title, "status": "running", "session_id": self.session_id})
            _loop_record_created = True
            log.info(f"Starting loop {loop_id}: {goal.title}")

            # 2. Execution
            lessons = [
                f"Goal '{h['goal']}' failed: {h.get('error') or h.get('reasoning', 'No detail')}"
                for h in history if h['score'] is not None and h['score'] < settings.failure_threshold
            ]

            messages, result = self.producer.execute_goal(loop_id, goal, self.registry, lessons[:settings.max_lessons])

            # 3. Evaluation — skip Critic for aborted loops
            if result.startswith("Loop aborted:"):
                eval_res = {
                    "overall_score": 0.0,
                    "breakdown": {},
                    "criteria_met": [],
                    "reasoning": result,
                    "failed": True,
                }
            else:
                eval_res = self.critic.evaluate(goal, goal.output_path, self.sandbox, loop_id)

            # 4. Finalize — embed actual artifact content, not the control-flow result string
            artifact_content = result
            if goal.output_path:
                try:
                    artifact_content = self.sandbox.read_file(goal.output_path)
                except Exception:
                    pass  # keep result string as fallback

            loop_data = {
                "id": loop_id,
                "goal": goal.title,
                "goal_detail": goal.model_dump(),
                "status": "completed" if not eval_res.get("failed") else "failed",
                "score": eval_res.get("overall_score", 0.0),
                "score_breakdown": eval_res.get("breakdown", {}),
                "reasoning": eval_res.get("reasoning", ""),
                "result": result,
                "messages": messages,
                "session_id": self.session_id,
            }
            self.sqlite.save_loop(loop_data)
            if eval_res.get("overall_score", 0.0) > settings.failure_threshold:
                self.vector.embed_and_store(artifact_content, {
                    "loop_id": loop_id,
                    "goal": goal.title,
                    "score": eval_res.get("overall_score", 0.0),
                })
            # Fetch cost/tokens written by CostTracker into LoopRecord
            saved = self.sqlite.get_loop(loop_id)
            loop_data["cost_usd"] = saved.get("cost_usd", 0.0) if saved else 0.0
            loop_data["tokens_used"] = saved.get("tokens_used", 0) if saved else 0
            self._loop_num += 1
            try:
                self.journal.write_loop(
                    loop_num=self._loop_num,
                    score=loop_data["score"] or 0.0,
                    goal=loop_data["goal"],
                    result=(loop_data.get("goal_detail") or {}).get("output_path") or "",
                    reasoning=loop_data.get("reasoning", ""),
                )
            except Exception:
                pass
            return loop_data

        except Exception as e:
            log.error(f"Loop {loop_id} failed with exception: {e}")
            if _loop_record_created:
                try:
                    self.sqlite.save_loop({"id": loop_id, "status": "failed", "error": str(e)})
                except Exception:
                    pass
            raise

        finally:
            self.sandbox.stop(cleanup=False)

    def _check_safety(self):
        curr = settings.load()
        if self.cost_tracker.get_daily_loop_count() >= curr.daily_loop_limit:
            raise RuntimeError("Daily loop limit reached.")
        if self.cost_tracker.get_monthly_cost() >= curr.monthly_cost_limit:
            raise RuntimeError("Monthly budget exceeded.")

    def shutdown(self):
        """Finalize session. Call once when done."""
        try:
            loops = self.sqlite.list_loops_by_session(self.session_id)
            completed = [l for l in loops if l["status"] in ("completed", "failed")]
            scores = [l["score"] for l in completed if l.get("score") is not None]
            total_cost = sum(l.get("cost_usd") or 0.0 for l in loops)
            avg_score_val = round(sum(scores) / len(scores), 4) if scores else None
            try:
                self.journal.write_session_summary(
                    loops=len(completed),
                    avg_score=round(sum(scores) / len(scores), 2) if scores else 0.0,
                    cost_usd=round(total_cost, 3),
                )
            except Exception:
                pass
            self.sqlite.update_session(
                self.session_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                completed_loops=len(completed),
                total_cost_usd=round(total_cost, 6),
                avg_score=avg_score_val,
            )
            log.info("Session %s finalized: %d loops, avg_score=%s",
                     self.session_id[:8], len(completed),
                     round(sum(scores) / len(scores), 4) if scores else "N/A")
        except Exception as e:
            log.warning("Could not finalize session stats: %s", e)
        log.info("Workspace preserved at %s", self.sandbox.local_workspace)

class AgentLoop(Orchestrator):
    """Legacy wrapper for CLI compatibility."""
    def explain_loop(self, loop_id: str) -> str:
        loop = self.sqlite.get_loop(loop_id)
        if not loop: return "Loop not found."
        prompt = f"Explain this loop:\nGoal: {loop['goal']}\nResult: {loop['result']}"
        resp = self.producer.chat(messages=[{"role": "user", "content": prompt}], system="You are an analyst.")
        return resp.choices[0].message.content or "No explanation."
