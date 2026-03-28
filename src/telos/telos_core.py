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
from .db_models import SessionRecord, InstinctState
from .llm import LLMService
from .usage import CostTracker
from .schemas import GoalSchema
from .interfaces import Tool, TemplateLoader
from .agents import BaseAgent
from .tools import ToolRegistry
from .sandbox import SandboxManager
from .instincts import InstinctEngine, extract_output_stats

log = get_logger("core")


def _instinct_label(value: float, high: str, low: str) -> str:
    if value > 0.7:
        return high
    if value < 0.3:
        return low
    return "moderate"


class GoalGenerator(BaseAgent):
    def __init__(self, cost_tracker: Any = None):
        super().__init__(agent_type="goal_gen", cost_tracker=cost_tracker)
        from .deduplicator import GoalDeduplicator
        self.deduplicator = GoalDeduplicator()

    def generate(
        self,
        instinct_state: Dict[str, float],
        history: List[Dict],
        similar: List[Dict],
        loop_count: int = 0,
        workspace_state: list = None,
    ) -> GoalSchema:
        # Format instinct state into the prompt
        curiosity = instinct_state.get("curiosity", 0.5)
        preservation = instinct_state.get("preservation", 0.5)
        growth = instinct_state.get("growth", 0.5)
        order = instinct_state.get("order", 0.5)

        instinct_section = (
            f"Your internal state:\n"
            f"- Curiosity: {curiosity:.2f} — {_instinct_label(curiosity, 'starving for novelty', 'satisfied, already exploring new territory')}\n"
            f"- Preservation: {preservation:.2f} — {_instinct_label(preservation, 'system unstable, be careful', 'system healthy')}\n"
            f"- Growth: {growth:.2f} — {_instinct_label(growth, 'stagnating, need to push harder', 'growing well')}\n"
            f"- Order: {order:.2f} — {_instinct_label(order, 'knowledge is scattered, consolidate', 'well-organized')}\n"
        )

        # Recent history (last 5 loops)
        recent = history[-5:] if len(history) >= 5 else history
        history_lines = []
        for h in recent:
            instincts = h.get("instincts", {})
            if instincts:
                i_str = f" | instincts: C={instincts.get('curiosity', '?'):.2f} P={instincts.get('preservation', '?'):.2f} G={instincts.get('growth', '?'):.2f} O={instincts.get('order', '?'):.2f}"
            else:
                i_str = ""
            history_lines.append(f"- {h['goal']}{i_str}")
        history_text = "\n".join(history_lines) if history_lines else "(none yet)"

        similar_text = ""
        if similar:
            similar_text = "\nSemantically similar past work:\n" + "\n".join(
                f"- {s.get('payload', {}).get('goal', 'N/A')}"
                for s in similar
            )

        workspace_section = ""
        if workspace_state:
            file_lines = "\n".join(f"- {f['path']} (loop {f['loop_id']})" for f in workspace_state)
            workspace_section = (
                f"\n\nCURRENT WORKSPACE\n"
                f"Existing files:\n{file_lines}\n\n"
                f"Constraint: Build upon existing work. Do not create isolated standalone scripts."
            )

        user_prompt = (
            f"{instinct_section}\n"
            f"Recent history (last 5 loops):\n{history_text}"
            f"{similar_text}"
            f"{workspace_section}\n\n"
            f"Based on your internal drives and history, decide what to build next."
        )

        system_prompt = self.load_template("goal_generation_system", "Generate a new and distinct goal.")
        past_titles = [h["goal"] for h in history if h.get("goal")]

        try:
            goal = self.chat_structured(
                messages=[{"role": "user", "content": user_prompt}],
                response_model=GoalSchema,
                system=system_prompt,
            )
            if self.deduplicator.is_duplicate(goal.title, past_titles, loop_count=loop_count):
                log.info("Goal '%s' is a duplicate; requesting a more novel goal.", goal.title)
                novel_prompt = (
                    user_prompt
                    + f"\n\nNOTE: The proposed goal '{goal.title}' is too similar to recent history. "
                    "Generate a more distinct goal that responds more strongly to your dominant drive."
                )
                goal = self.chat_structured(
                    messages=[{"role": "user", "content": novel_prompt}],
                    response_model=GoalSchema,
                    system=system_prompt,
                )
            return goal
        except Exception as e:
            log.error(f"Goal generation failed: {e}")
            return GoalSchema(
                title="Explore the system",
                success_criteria=["Any file is created"],
                output_path="output.txt",
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
                loop_id=loop_id,
            )

            usage = getattr(response, "usage", None)
            if usage:
                if isinstance(usage, dict):
                    step_tokens = usage.get('total_tokens') or (
                        (usage.get('prompt_tokens') or 0) + (usage.get('completion_tokens') or 0)
                    )
                else:
                    step_tokens = getattr(usage, 'total_tokens', None) or (
                        (getattr(usage, 'prompt_tokens', 0) or 0) + (getattr(usage, 'completion_tokens', 0) or 0)
                    )
                total_tokens += step_tokens
                if not self.llm.validate_token_limit(total_tokens):
                    log.warning(f"Loop {loop_id} exceeded token limit. Aborting.")
                    final_result = "Loop aborted: Exceeded token limit."
                    break

            msg = response.choices[0].message
            messages.append(msg.model_dump())

            if getattr(msg, 'tool_calls', None):
                had_tool_call = True
                final_result, consecutive_errors = self._handle_tool_calls(
                    msg.tool_calls, messages, registry, consecutive_errors
                )
                if consecutive_errors >= self.settings.consecutive_error_limit or final_result.startswith("TASK_COMPLETE:"):
                    break
            else:
                final_result = msg.content or "Completed."
                if had_tool_call:
                    break

        return messages, final_result or "Loop reached max steps."

    def _truncate_tool_output(self, result: str) -> str:
        limit = self.settings.max_output_truncation
        if len(result) <= limit:
            return result
        stripped = result.lstrip()
        if stripped.startswith("{") or stripped.startswith("["):
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

        cfg = settings.load()
        persistent_ws = PROJECT_ROOT / cfg.memory.workspace_path / cfg.memory.persistent_workspace_name
        persistent_ws.mkdir(parents=True, exist_ok=True)
        self.sandbox = SandboxManager(workspace_dir=str(persistent_ws))
        self.registry = ToolRegistry(self.sandbox)

        self.goal_gen = GoalGenerator(self.cost_tracker)
        self.producer = ProducerAgent(self.cost_tracker)
        self.instinct_engine = InstinctEngine(self.vector, self.sqlite)

        # Warm instinct state — updated each loop
        self._instinct_state: Dict[str, float] = {
            "curiosity": 0.5,
            "preservation": 0.5,
            "growth": 0.5,
            "order": 0.5,
        }

        self.session_id = str(uuid.uuid4())
        session_ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        session_rec = SessionRecord(
            id=self.session_id,
            name=session_name or f"session-{session_ts}",
            producer_model=cfg.llm.producer_model,
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

    def run_iteration(self) -> Dict:
        loop_id = str(uuid.uuid4())
        _loop_record_created = False
        try:
            self._check_safety()
            self.sandbox.start()

            # 1. Goal Generation — driven by instinct state
            history = self.sqlite.get_quality_history()
            loop_count = self.sqlite.count_loops()

            # Domain-escape heuristic: 3 consecutive failures → query initial_intent
            recent_statuses = [h.get("status") for h in history[-3:]]
            if len(recent_statuses) >= 3 and all(s == "failed" for s in recent_statuses):
                log.info("3 consecutive failures; using initial intent for Qdrant query.")
                qdrant_query = settings.initial_intent
            else:
                qdrant_query = history[-1]["goal"] if history else settings.initial_intent

            similar = self.vector.search_similar(qdrant_query, limit=settings.similar_artifacts_limit)
            workspace_state = self.sandbox.list_files()
            pre_instinct_state = dict(self._instinct_state)

            goal = self.goal_gen.generate(
                instinct_state=pre_instinct_state,
                history=history,
                similar=similar,
                loop_count=loop_count,
                workspace_state=workspace_state,
            )

            if goal.output_path and not goal.output_path.startswith(loop_id[:8]):
                goal.output_path = f"{loop_id[:8]}/{goal.output_path}"

            self.sqlite.save_loop({
                "id": loop_id,
                "goal": goal.title,
                "status": "running",
                "session_id": self.session_id,
            })
            _loop_record_created = True
            log.info("Starting loop %s: %s", loop_id[:8], goal.title)

            # 2. Execution
            # Lessons from failed loops (based on status, not score)
            lessons = [
                f"Goal '{h['goal']}' failed: {h.get('error') or h.get('reasoning', 'No detail')}"
                for h in history
                if h.get("status") == "failed"
            ]
            messages, result = self.producer.execute_goal(loop_id, goal, self.registry, lessons[:settings.max_lessons])

            # 3. Determine execution outcome
            aborted = result.startswith("Loop aborted:")
            final_status = "failed" if aborted else "completed"
            exit_code = 1 if aborted else 0

            # 4. Read artifact content for embedding and stats
            artifact_content = result
            if goal.output_path and not aborted:
                try:
                    artifact_content = self.sandbox.read_file(goal.output_path)
                except Exception:
                    pass

            # 5. Extract output stats for growth instinct
            output_stats = extract_output_stats(artifact_content)

            # 6. Compute instinct state (post-loop)
            output_embedding = None
            if self.vector.available and artifact_content:
                try:
                    output_embedding = self.vector._get_embedding(artifact_content)
                except Exception:
                    pass

            post_instinct_state = self.instinct_engine.compute_state(
                output_embedding=output_embedding,
                output_stats=output_stats,
            )
            self._instinct_state = post_instinct_state

            # 7. Persist instinct state
            db_session = self.sqlite.Session()
            try:
                instinct_rec = InstinctState(
                    loop_id=loop_id,
                    curiosity=post_instinct_state["curiosity"],
                    preservation=post_instinct_state["preservation"],
                    growth=post_instinct_state["growth"],
                    order_drive=post_instinct_state["order"],
                )
                db_session.add(instinct_rec)
                db_session.commit()
            except Exception as e:
                db_session.rollback()
                log.warning("Could not persist instinct state: %s", e)
            finally:
                db_session.close()

            # 8. Store embedding in Qdrant (only for completed loops)
            if not aborted and output_embedding:
                self.vector.embed_and_store(artifact_content, {
                    "loop_id": loop_id,
                    "goal": goal.title,
                })

            # 9. Save full loop record
            loop_data = {
                "id": loop_id,
                "goal": goal.title,
                "goal_detail": goal.model_dump(),
                "status": final_status,
                "result": result,
                "messages": messages,
                "session_id": self.session_id,
                "exit_code": exit_code,
                "loc": output_stats["loc"],
                "function_count": output_stats["function_count"],
                "import_count": output_stats["import_count"],
                "builds_on_previous": output_stats["builds_on_previous"],
                # Keep score=None; no Critic
                "score": None,
                "reasoning": None,
            }
            self.sqlite.save_loop(loop_data)

            saved = self.sqlite.get_loop(loop_id)
            loop_data["cost_usd"] = saved.get("cost_usd", 0.0) if saved else 0.0
            loop_data["tokens_used"] = saved.get("tokens_used", 0) if saved else 0
            loop_data["instincts_pre"] = pre_instinct_state
            loop_data["instincts_post"] = post_instinct_state

            self._loop_num += 1
            try:
                self.journal.write_loop(
                    loop_num=self._loop_num,
                    goal=loop_data["goal"],
                    output_path=(loop_data.get("goal_detail") or {}).get("output_path") or "",
                    instincts_pre=pre_instinct_state,
                    instincts_post=post_instinct_state,
                    output_stats=output_stats,
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
        """Finalize session."""
        try:
            loops = self.sqlite.list_loops_by_session(self.session_id)
            completed = [l for l in loops if l["status"] in ("completed", "failed")]
            total_cost = sum(l.get("cost_usd") or 0.0 for l in loops)
            try:
                self.journal.write_session_summary(
                    loops=len(completed),
                    cost_usd=round(total_cost, 3),
                    final_instincts=self._instinct_state,
                )
            except Exception:
                pass
            self.sqlite.update_session(
                self.session_id,
                status="completed",
                completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                completed_loops=len(completed),
                total_cost_usd=round(total_cost, 6),
                avg_score=None,
            )
            log.info("Session %s finalized: %d loops", self.session_id[:8], len(completed))
        except Exception as e:
            log.warning("Could not finalize session stats: %s", e)
        log.info("Workspace preserved at %s", self.sandbox.local_workspace)


class AgentLoop(Orchestrator):
    """Legacy wrapper for CLI compatibility."""
    def explain_loop(self, loop_id: str) -> str:
        loop = self.sqlite.get_loop(loop_id)
        if not loop:
            return "Loop not found."
        prompt = f"Explain this loop:\nGoal: {loop['goal']}\nResult: {loop['result']}"
        resp = self.producer.chat(messages=[{"role": "user", "content": prompt}], system="You are an analyst.")
        return resp.choices[0].message.content or "No explanation."
