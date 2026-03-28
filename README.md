# Telos — The Autonomous AI Runtime

**"Empowering AI to set, execute, and evaluate its own destiny."**

---

## Overview

Telos is an autonomous agent runtime designed to bridge the gap between "tool-using agents" and "self-evolving systems." 

Traditional agents follow a linear script provided by a human. **Telos** flips this:
- **Human**: Sets the initial "Ambient Intent" and high-level safety constraints.
- **Telos**: Continuously generates its own sub-goals, executes them in a hardened sandbox, and evaluates the results against a formal rubric.

### Core Philosophy
- **Instinct-Driven Goal Generation**: The AI's goals are generated based on environmental signals (curiosity, preservation, growth, order) computed from past loops, not human-provided prompts.
- **Semantic Continuity**: Every action is embedded into a vector store, allowing the system to recognize patterns and avoid repeating past failures.
- **Isolated Execution**: Every line of code is executed in a restricted Docker sandbox for safety.

---

## Architecture

Telos operates on a continuous feedback loop:
1. **Goal Generation**: Driven by instinct signals (curiosity, preservation, growth, order) computed from environmental feedback.
2. **Multi-Step Execution**: The Producer interacts with the sandbox via tool-calling, building upon previous work.
3. **Instinct Update**: Environmental signals (output complexity, crash rate, semantic novelty, etc.) are computed to update internal drives.
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

```bash
git clone <repository-url>
cd telos
pip install -e .
telos init
```

`telos init` が対話形式でセットアップをガイドします（全ステップ Enter でスキップ可）:

| Step | 内容 |
|:---|:---|
| **[1/5] Model** | 使用モデルを選択（プリセット一覧 or カスタム入力） |
| **[2/5] API Key** | 選択モデルのプロバイダーキーを設定・検証 |
| **[3/5] Intent** | AI に何をさせるかを設定 |
| **[4/5] Docker** | サンドボックスイメージをビルドし Qdrant を起動（Docker なしの場合はローカルモードで続行） |
| **[5/5] Embedding** | `all-MiniLM-L6-v2` をダウンロード（初回のみ ~90MB） |

```bash
telos start --loops 10
```

> **設定の変更:** `telos.yaml`（モデル・intent・メモリ設定）と `config.yaml`（Docker・Qdrant・コスト上限）を直接編集することで、`telos init` を再実行せずに設定を変更できます。API キーは `.env` に記載されています。

> **ウィザードの再実行:** `telos init`（既存の設定が現在値としてデフォルト表示されます）

---

## CLI Reference

### Typical workflow

```bash
# Run loops
telos start --loops 5 --name "my-experiment"
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
telos project list                 # list all projects (★ marks the active one)
telos project new experiment-v2   # create a new project and switch to it
telos project switch main          # switch to an existing project
telos project delete old-run       # permanently delete a project and its data
```

The active project is stored in `.env.local` as `TELOS_HOME`. Every command operates on the active project — `telos project list` shows which project is active (★ marks the active one).

### All commands

| Command | Description |
|:---|:---|
| `telos init [--force] [--non-interactive]` | Generate `telos.yaml` with project settings. `--force` overwrites; `--non-interactive` skips prompts (CI mode). |
| `telos start` | Run autonomous loops. Options: `--loops N`, `--name`, `--model`. Runs a pre-flight API key check before starting. |
| `telos stop` | Stop a running loop gracefully. |
| `telos reset` | Wipe the active project's DB + workspace + log + journal to restart from loop 1. Add `--yes` to skip confirmation. |
| `telos project list` | List all projects (★ = active) with loop counts. |
| `telos project new NAME` | Create a new isolated project and switch to it. |
| `telos project switch NAME` | Switch the active project. |
| `telos project delete NAME` | Permanently delete a project and all its data. |

---

## Configuration

Telos uses two config files at the repository root:

| File | Purpose | Edit frequency |
|:---|:---|:---|
| `config.yaml` | Infrastructure: Qdrant URL, Docker, logging, cost limits | Rarely |
| `telos.yaml` | Project: models, initial intent, memory parameters | Often |

Settings are merged in this priority order (highest wins):

```
Environment variables  >  telos.yaml  >  config.yaml  >  Pydantic defaults
```

`telos init` generates `telos.yaml` with sensible defaults. Edit it to change models or intent.

Other files:
- **`templates/`**: System prompts that define the Producer and GoalGenerator personalities.
