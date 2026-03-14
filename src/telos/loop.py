import uuid
import signal
import os
from typing import Optional, List, Dict
from datetime import datetime
from .llm import LLMInterface
from .sandbox import SandboxManager
from .config import PID_FILE, settings
from .memory import MemoryStore, VectorStore
from .critic import CriticAgent
from .logger import get_logger

log = get_logger("loop")

class LoopState:
    """Manages the state and metrics of a single loop execution."""
    def __init__(self, objective: str):
        self.loop_id = str(uuid.uuid4())
        self.objective = objective
        self.start_time = datetime.now()
        self.tokens_used = 0
        self.cost_usd = 0.0
        self.status = "running"  # running, completed, failed, timeout
        self.error_msg: Optional[str] = None
        self.score: Optional[float] = None
        self.score_breakdown: Optional[dict] = None
        self.output_path: Optional[str] = None
        
    def add_tokens(self, prompt: int, completion: int, cost: float):
        self.tokens_used += (prompt + completion)
        self.cost_usd += cost


class AgentLoop:
    def __init__(self, model: Optional[str] = None, max_loops: int = 1):
        self.llm = LLMInterface(model=model)
        self.sandbox = SandboxManager()
        self.memory = MemoryStore()
        self.vector_store = VectorStore()
        self.critic = CriticAgent()
        self.history: List[Dict] = []
        self.loop_count = 0
        self.max_loops = max_loops
        self._shutdown_requested = False

    def _write_pid(self):
        """Write PID file for graceful shutdown."""
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        log.debug("Wrote PID %d to %s", os.getpid(), PID_FILE)

    def _remove_pid(self):
        """Remove PID file on shutdown."""
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def _signal_handler(self, signum, frame):
        """Handle SIGTERM/SIGINT for graceful shutdown."""
        log.info("Received signal %d, shutting down gracefully...", signum)
        self._shutdown_requested = True

    def start(self):
        """Start the autonomous process."""
        # Register signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        self._write_pid()

        self.sandbox.start()
        daily_limit = settings.daily_loop_limit
        log.info("Agent sandbox started. Daily limit: %d loops. Running %d loop(s).", 
                 daily_limit, self.max_loops)

        try:
            while self.loop_count < self.max_loops and self.loop_count < daily_limit:
                if self._shutdown_requested:
                    log.info("Shutdown requested. Stopping after %d loops.", self.loop_count)
                    break

                log.info("--- Starting Loop %d / %d ---", self.loop_count + 1, self.max_loops)
                
                # Step 1: Generate Goal
                goal = self._generate_goal()
                state = LoopState(goal)
                log.info("Goal: %s", goal)
                
                # Step 2: Execution Phase
                self._execute_goal(state)
                
                # Step 3: Evaluation (Critic)
                final_output = ""
                if state.status == "completed":
                    log.info("Evaluating output...")
                    final_output = self._get_final_output_from_sandbox(state)
                    eval_result = self.critic.evaluate(state.objective, final_output)
                    
                    state.score = eval_result["overall_score"]
                    state.score_breakdown = eval_result["breakdown"]
                    log.info("Critic Score: %.2f — %s", state.score, eval_result['reasoning'][:120])

                # Step 4: Memory Storage
                loop_data = {
                    "id": state.loop_id,
                    "goal": state.objective,
                    "output_path": state.output_path,
                    "score": state.score,
                    "score_breakdown": state.score_breakdown,
                    "tokens_used": state.tokens_used,
                    "cost_usd": state.cost_usd,
                    "status": state.status,
                    "error": state.error_msg
                }
                self.memory.save_loop(loop_data)
                
                # Save to Vector DB for semantic search later
                if state.status == "completed" and final_output:
                    self.vector_store.embed_and_store(
                        text=f"Goal: {state.objective}\nResult: {final_output}", 
                        metadata={"loop_id": state.loop_id, "score": state.score}
                    )

                if state.status == "failed":
                    log.warning("Loop failed: %s", state.error_msg)
                
                self.loop_count += 1

        finally:
            self.sandbox.stop()
            self._remove_pid()
            log.info("Agent stopped after %d loop(s).", self.loop_count)

    def _generate_goal(self) -> str:
        """Query memory and LLM to decide the next objective."""
        past_loops = self.memory.list_loops(limit=5)
        
        if not past_loops:
            return "Create a python script named 'hello.py' that prints 'Hello, world!' and execute it."
        
        # Build context from past loops for the LLM
        history_summary = []
        for loop in past_loops:
            status = loop['status']
            score = f"{loop['score']:.2f}" if loop['score'] is not None else "N/A"
            history_summary.append(f"- Goal: {loop['goal']} | Status: {status} | Score: {score}")

        history_text = "\n".join(history_summary)
        
        # Search for similar past work in vector DB
        similar = self.vector_store.search_similar("What should I build next?", limit=3)
        similar_text = ""
        if similar:
            similar_text = "\nPast artifact summaries found in memory:\n" + "\n".join(
                [f"- {s.get('payload', {})}" for s in similar]
            )
        
        system_prompt = (
            "You are a goal generator for an autonomous AI agent. Based on the agent's past work, "
            "generate ONE specific, actionable goal for the next loop iteration.\n"
            "Rules:\n"
            "- If past goals failed, try a different approach or simpler variation.\n"
            "- If past goals succeeded with low scores, improve upon them.\n"
            "- If past goals succeeded with high scores, try something new and more ambitious.\n"
            "- The goal must be achievable with shell commands, file writing, and Python.\n"
            "- Be specific and concrete. Output ONLY the goal text, nothing else."
        )
        
        user_prompt = f"Past loop history:\n{history_text}{similar_text}\n\nGenerate the next goal:"
        
        try:
            import litellm
            from litellm import completion
            response = completion(
                model=self.llm.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            goal = response.choices[0].message.content.strip()
            log.debug("LLM generated goal: %s", goal)
            return goal
        except Exception as e:
            log.warning("LLM goal generation failed, using fallback: %s", e)
            recent_goal = past_loops[0]['goal']
            return f"Improve upon the previous goal: {recent_goal}. Make it more advanced."

    def _execute_goal(self, state: LoopState):
        """Execute the agent loop using standard Tool-calling cycle."""
        system_prompt = (
            f"Your current goal is: {state.objective}. "
            f"You have access to a secure sandbox. Achieve the goal using the provided tools. "
            f"When you are finished, respond with a summary of what you accomplished."
        )
        messages = [{"role": "user", "content": "Begin execution."}]
        
        # Max 10 steps to prevent runaway
        for step in range(10):
            if self._shutdown_requested:
                state.status = "failed"
                state.error_msg = "Shutdown requested during execution."
                return

            log.debug("Execution step %d", step + 1)
            try:
                # 1. Ask LLM what to do next
                response = self.llm.chat(messages, system=system_prompt)
                
                # Update metrics
                input_tokens = response.usage.prompt_tokens
                output_tokens = response.usage.completion_tokens
                state.add_tokens(
                    input_tokens,
                    output_tokens,
                    self.llm.calculate_cost(input_tokens, output_tokens)
                )

                if not self.llm.validate_token_limit(state.tokens_used):
                    state.status = "timeout"
                    state.error_msg = "Token limit exceeded."
                    log.warning("Token limit exceeded at %d tokens", state.tokens_used)
                    return

                # Append assistant message to history
                assistant_msg = response.choices[0].message
                messages.append(assistant_msg.model_dump())
                
                if assistant_msg.tool_calls:
                    # 2. Execute tools locally
                    for tool_call in assistant_msg.tool_calls:
                        tool_name = tool_call.function.name
                        import json
                        try:
                            tool_inputs = json.loads(tool_call.function.arguments)
                        except json.JSONDecodeError:
                            tool_inputs = {}
                        tool_id = tool_call.id
                        
                        log.info("[Tool] %s(%s)", tool_name, 
                                json.dumps(tool_inputs)[:100])
                        result_str = self._handle_tool_call(tool_name, tool_inputs)
                        log.debug("[Result] %s", result_str[:200])
                        
                        # 3. Append tool results to history
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "name": tool_name,
                            "content": result_str
                        })
                else:
                    # Assistant is done
                    log.info("Agent finished: %s", (assistant_msg.content or "No text")[:200])
                    state.status = "completed"
                    return

            except Exception as e:
                state.status = "failed"
                state.error_msg = str(e)
                log.error("Error during execution step %d: %s", step + 1, e)
                return

    def _handle_tool_call(self, name: str, inputs: dict) -> str:
        if name == "execute_command":
            res = self.sandbox.execute_command(inputs["command"])
            return f"Exit code: {res['exit_code']}\nOutput:\n{res['output']}"
        elif name == "write_file":
            self.sandbox.write_file(inputs["path"], inputs["content"])
            return f"Successfully wrote to {inputs['path']}."
        elif name == "read_file":
            return self.sandbox.read_file(inputs["path"])
        return "Unknown tool."

    def _get_final_output_from_sandbox(self, state: LoopState) -> str:
        """Helper to extract the result of the agent's work for the Critic."""
        res = self.sandbox.execute_command("cat *.* 2>/dev/null || echo 'No files created.'")
        return res.get("output", "No output retrieved.")
