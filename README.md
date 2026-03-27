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

## Prerequisites

| Tool | Version | Purpose |
|:---|:---|:---|
| [Python](https://www.python.org/downloads/) | 3.11+ | Runtime |
| [Docker](https://docs.docker.com/get-docker/) | 24+ | Sandbox execution & Qdrant |
| [Docker Compose](https://docs.docker.com/compose/install/) | v2+ | Infrastructure orchestration |

### Installing prerequisites

**macOS (Homebrew)**
```bash
# Step 1: Install Homebrew if not.
# Xcode Command Line Tools will automatically be installed.
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Step 2: Install Python and Docker
brew install python@3.11
brew install --cask docker   # Contains Docker Compose

# Step 3: Start Docker Desktop（Open mannualy at first time.）
open -a Docker
```

**Linux (Ubuntu/Debian)**
```bash
sudo apt update && sudo apt install -y python3.11 python3.11-venv
# Docker: follow https://docs.docker.com/engine/install/ubuntu/
```

**Windows**
- Install [Python 3.11+](https://www.python.org/downloads/)
- Install [Docker Desktop](https://docs.docker.com/desktop/install/windows-install/) (includes Compose)

---

## Getting Started

### 1. Clone & install
```bash
git clone <repository-url>
cd telos

# (Recommended) create a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .
```

### 2. Run the setup wizard
```bash
telos init
```

The wizard will guide you through:
1. **API key** — enter your OpenRouter key and it's validated and saved to `.env` automatically
2. **Initial intent** — set what the AI should work on (e.g. `"Build useful tools and explore the system."`)
3. **Docker** — Qdrant is started automatically if Docker is available; if not, Telos falls back to local sandbox mode
4. **Embedding model** — `all-MiniLM-L6-v2` is downloaded on first run (~90 MB)

```
$ telos init

Welcome to Telos — Autonomous AI Runtime

? OpenRouter API key: sk-or-...
  Verifying... ✅

? What should the AI work on?
  > Build useful tools and explore the system.

Creating project structure... done ✅

Checking Docker... found ✅
Running: docker compose up -d (Qdrant)... done ✅

Downloading embedding model (first time only, ~90MB)...
Embedding model... done ✅

════════════════════════════════
  Telos is ready.
  Run: telos start --loops 10
════════════════════════════════
```

> **OpenRouter is recommended** — one key gives access to all models (DeepSeek, Claude, Gemini, etc.).
> Get yours at https://openrouter.ai/keys

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
telos report                  # saves to report_<project>_<timestamp>.md
telos report -o summary.md    # custom path
```

### Starting over (reset)

Wipe all data in the current project and restart from loop 1:

```bash
telos reset        # wipe DB + workspace + log (with confirm)
telos reset --yes  # same, no confirmation prompt
```

### Project isolation

Each project has its own database, workspace, and logs under `projects/<name>/`.

```bash
telos project current              # show the active project and its stats
telos project list                 # list all projects (★ marks the active one)
telos project new experiment-v2   # create a new project and switch to it
telos project switch main          # switch to an existing project
telos project delete old-run       # permanently delete a project and its data
```

The active project is stored in `.env.local` as `TELOS_HOME`. Every command operates on the active project — `telos status` and `telos project current` both show which project is active.

### All commands

| Command | Description |
|:---|:---|
| `telos init [--force] [--non-interactive]` | Initialize a project in the current directory (`telos.yaml`). `--force` overwrites; `--non-interactive` skips prompts (CI mode). |
| `telos init --global [--force]` | Initialize the global machine config (`~/.config/telos/config.yaml`). Run once per machine. |
| `telos start` | Run autonomous loops. Options: `--loops N`, `--name`, `--model`. Runs a pre-flight API key check before starting. |
| `telos stop` | Stop a running loop gracefully. |
| `telos status` | Show session history. Add `--loops` for individual loop view. |
| `telos show [LOOP_ID]` | Inspect a loop result. Add `--explain` for LLM narrative. |
| `telos export [SESSION_ID]` | Export session data to JSON or CSV (`--format csv`, `-o FILE`). |
| `telos report` | Generate a full Markdown report (`-o FILE`). |
| `telos logs` | View agent logs. Add `-f` to stream in real time. |
| `telos dashboard` | Open the interactive TUI dashboard. |
| `telos reset` | Wipe the active project's DB + workspace + log to restart from loop 1. Add `--yes` to skip confirmation. |
| `telos project current` | Show the active project name and stats. |
| `telos project list` | List all projects (★ = active) with loop counts and scores. |
| `telos project new NAME` | Create a new isolated project and switch to it. |
| `telos project switch NAME` | Switch the active project. |
| `telos project delete NAME` | Permanently delete a project and all its data. |

---

## Configuration

Telos uses a **two-tier configuration system**:

| File | Location | Purpose | Git-tracked? |
|:---|:---|:---|:---|
| `~/.config/telos/config.yaml` | Global (machine-wide) | Infrastructure: Qdrant URL, Docker, logging, cost limits | No |
| `telos.yaml` | Project root | Project-specific: intent, models, memory, rubric | Yes |

Settings are merged in this priority order (highest wins):

```
Environment variables
    ↓
telos.yaml  (searched upward from CWD)
    ↓
~/.config/telos/config.yaml
    ↓
Pydantic defaults
```

Initialize global config once per machine:
```bash
telos init --global
```

Initialize a new project directory:
```bash
mkdir my-project && cd my-project
telos init
```

Other configuration files:
- **`rubric.json`**: Scoring criteria used by the Critic.
- **`templates/`**: System prompts that define the Producer and Critic personalities.
