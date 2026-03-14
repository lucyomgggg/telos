# Telos: The Autonomous AI Runtime

Telos is an open-source autonomous runtime designed to bridge the gap between AI reasoning and real-world execution. It provides the infrastructure for an AI to decide its own goals, execute them in a secure environment, and learn from its own outputs.

## 🚀 Vision
**"Infrastructure for 100% Autonomous AI"**
Instead of just responding to prompts, Telos acts as a continuous loop that builds, tests, and refines software and data artifacts independently.

## 🏗️ Architecture
For a deep dive into the system design, see the [System Architecture](docs/ARCHITECTURE.md) document.

Telos is built on a modular "Closed-Loop" architecture consisting of five core pillars:

### 1. Agent Loop (`AgentLoop`)
The orchestrator. It manages the lifecycle of a task, ensuring the transition between reasoning, tool execution, and self-critique.

### 2. Secure Sandbox (`SandboxManager`)
A containerized execution environment (Docker-based) where the AI can safely run shell commands, execute Python scripts, and manage files without host system risk.

### 3. LLM & Reasoning (`LLMInterface`)
Powered by `litellm`, Telos supports a wide range of state-of-the-art models (Gemini 2.5, GPT-4o, Claude 3.5). It leverages tool-calling to interact with the sandbox.

### 4. Dual-Layer Memory (`MemoryStore` & `VectorStore`)
- **Long-term Experience (Vector DB/Qdrant)**: Stores semantic embeddings of every successful artifact, allowing the AI to "remember" how it solved problems in the past.
- **Audit Log (SQLite)**: A structured record of every loop iteration, including token costs, execution status, and safety scores.

### 5. Critic Agent (`CriticAgent`)
An autonomous quality-assurance layer that evaluates the agent's output against a defined rubric (completeness, coherence, novelty). It provides a numerical score that influences the AI's future decisions.

## 🔄 The Autonomous Cycle

1. **Observe**: Query the memory system for past successes and failures.
2. **Ideate**: Generate a specific, actionable goal for the next iteration.
3. **Act**: Use the available tools in the sandbox to build the solution.
4. **Critique**: The Critic Agent scores the result.
5. **Remember**: Save the artifact, metadata, and score to the memory store.

## 🛠️ Getting Started

1. **Initialize**: `telos init`
2. **Configure**: Edit `config.yaml` in the project root to add your `OPENAI_API_KEY` or `GEMINI_API_KEY`.
3. **Run**: `telos start --loops 5`
4. **Monitor**: `telos status` and `telos logs -f`

---
*Telos is a research-oriented project aimed at pushing the boundaries of AI autonomy.*