# Telos — The Autonomous AI Runtime

**"Empowering AI to set, execute, and evaluate its own destiny."**

---

## Overview

Telos is an autonomous agent runtime designed to bridge the gap between "tool-using agents" and "self-evolving systems." 

Traditional agents follow a linear script provided by a human. **Telos** flips this:
- **Human**: Sets the initial "Ambient Intent" and high-level safety constraints.
- **Telos**: Continuously generates its own sub-goals, executes them in a hardened sandbox, and evaluates the results against a formal rubric.

### Core Philosophy
- **Zero-Knowledge Criticism**: The evaluator (Critic) is isolated from the executor's (Producer) internal "Chain of Thought" to prevent judge bias.
- **Semantic Continuity**: Every action is embedded into a vector store, allowing the system to recognize patterns and avoid repeating past failures.
- **Isolated Execution**: Every line of code is executed in a restricted Docker sandbox for safety.

---

## Architecture

Telos operates on a continuous feedback loop:
1. **Goal Generation**: Analyzes history and context to propose a novel next step.
2. **Multi-Step Execution**: The Producer interacts with the sandbox via tool-calling.
3. **Strict Evaluation**: The Critic judge scores the result based on a weighted rubric.
4. **Audit & Memory**: Results are stored in SQLite and Qdrant for long-term learning.

---

## Getting Started

### 1. Installation
```bash
# Clone the repository
git clone <repository-url>
cd telos

# Install dependencies (requires Python 3.11+)
pip install -e .
```

### 2. Initialization
```bash
telos init
```
This creates the necessary directories (`data/`, `workspace/`, `outputs/`) and a template `.env` file. Add your API keys (OpenAI, Gemini, etc.) to `.env`.

---

## CLI Reference

### Typical workflow

```bash
# 1. Run loops
telos start --loops 5 --name "my-experiment"

# 2. Check what happened
telos status              # session list
telos status --loops      # individual loop list

# 3. Inspect a specific loop
telos show                        # latest loop
telos show <loop_id>              # specific loop (8-char ID ok)
telos show <loop_id> --explain    # narrative explanation

# 4. Export results
telos export                          # latest session → JSON
telos export <session_id>             # specific session
telos export --format csv -o out.csv  # CSV to custom path

# 5. Generate a full report
telos report                  # saves to REPORT.md
telos report -o summary.md    # custom path
```

### All commands

| Command | Description |
|:---|:---|
| `telos init` | Set up config files and directories. |
| `telos start` | Run autonomous loops. Options: `--loops N`, `--name`, `--model`, `--verbose`. |
| `telos stop` | Stop a running loop gracefully. |
| `telos status` | Show session history. Add `--loops` for individual loop view. |
| `telos show [LOOP_ID]` | Inspect a loop result in detail. Add `--explain` for LLM narrative. |
| `telos export [SESSION_ID]` | Export session data to JSON or CSV (`--format csv`, `-o FILE`). |
| `telos report` | Generate a full Markdown report of all activity (`-o FILE`). |
| `telos logs` | View agent logs. Add `-f` to stream in real time. |
| `telos dashboard` | Open the interactive TUI dashboard. |
| `telos clean` | Clear workspace files and logs. |

---

## Configuration

- **`config.yaml`**: Main settings for models, limits, and sandbox parameters.
- **`rubric.json`**: Definition of the scoring criteria used by the Critic.
- **`templates/`**: System prompts that define the Producer and Critic personalities.
