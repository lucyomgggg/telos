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

| Command | Description |
|:---|:---|
| `telos start` | Launch the autonomous engine (`--loops N`). |
| `telos status` | View recent loop history, scores, and costs. |
| `telos show` | Deep-dive into a specific loop result (`--explain` for narrative). |
| `telos report` | Generate a comprehensive Markdown report of recent work. |
| `telos logs` | View raw system and agent logs (`-f` to follow). |
| `telos clean` | Clear the `workspace/` and temporary logs. |

---

## Configuration

- **`config.yaml`**: Main settings for models, limits, and sandbox parameters.
- **`rubric.json`**: Definition of the scoring criteria used by the Critic.
- **`templates/`**: System prompts that define the Producer and Critic personalities.
