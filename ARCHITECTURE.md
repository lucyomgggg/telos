# Telos — アーキテクチャドキュメント

> 最終更新: 2026-03-16

---

## 1. プロジェクト概要

**Telos** は「目標を自分で設定して、自分で動き続けるAIエージェントランタイム」。
人間が具体的なタスクを指示しなくても、AIが自律的に目標を生成・実行・評価し、その結果を記憶として蓄積しながら次のループに活かすOSSランタイム。

- **方針**: モデル非依存（litellm経由でOpenAI/Anthropic/Gemini/Ollama等すべて対応）
- **安全性**: Docker隔離 + コスト上限 + 日次ループ上限
- **学習**: SQLite（構造データ）+ Qdrant（セマンティック記憶）による経験蓄積

---

## 2. プロジェクト構造

```
telos/
├── src/telos/                  # コアソースコード
│   ├── agents.py               # BaseAgent（全エージェントの基底クラス）
│   ├── cli.py                  # CLIエントリポイント（Click）
│   ├── config.py               # 設定管理（YAML + 環境変数 + デフォルト値）
│   ├── critic.py               # CriticAgent（評価エージェント）
│   ├── db_models.py            # SQLAlchemy ORM（LoopRecord, AuditLog）
│   ├── deduplicator.py         # GoalDeduplicator（目標重複排除）
│   ├── interfaces.py           # 抽象基底クラス（Tool, Critic, TemplateLoader）
│   ├── llm.py                  # LLMService（litellm統一インターフェース）
│   ├── logger.py               # ロギング設定
│   ├── memory.py               # MemoryStore（SQLite）+ VectorStore（Qdrant）
│   ├── sandbox.py              # SandboxManager（Docker/Localの実行環境）
│   ├── schemas.py              # Pydanticスキーマ（GoalSchema, EvaluationResponse）
│   ├── telos_core.py           # Orchestrator, GoalGenerator, ProducerAgent
│   ├── tools.py                # ToolRegistry + ツール実装
│   ├── usage.py                # CostTracker（LLM使用量・コスト追跡）
│   └── utils.py                # ユーティリティ（JSON修復等）
│
├── templates/                  # LLMシステムプロンプト
│   ├── producer_system.txt     # Producerエージェントのキャラクター定義
│   ├── critic_system.txt       # Criticエージェントのキャラクター定義
│   └── goal_generation_system.txt # 目標生成の駆動プロンプト
│
├── tests/                      # テストスイート（pytest）
├── data/
│   ├── telos.db                # SQLiteデータベース（ループ履歴・監査ログ）
│   └── qdrant/                 # Qdrantベクトルストアデータ
├── workspace/
│   └── run_*/                  # ループごとの隔離実行ディレクトリ
├── outputs/                    # 生成レポート
├── config.yaml                 # メイン設定ファイル
├── rubric.json                 # 評価ルーブリック（Criticの採点軸と重み）
├── .env                        # APIキー等の環境変数
├── Dockerfile                  # サンドボックスコンテナ定義
└── docker-compose.yml          # Docker構成（Qdrant + Sandbox）
```

---

## 3. システム全体フロー

```
[User]
  │
  └─ telos start --loops N --model M
        │
        ▼
   CLI (cli.py)
        │
        ▼
   AgentLoop.run_iteration(intent)
        │
        ├─ 1. 安全チェック (_check_safety)
        │      └─ 日次ループ上限 / 月次コスト上限
        │
        ├─ 2. サンドボックス起動 (SandboxManager.start)
        │      └─ Docker優先、失敗時はLocalフォールバック
        │
        ├─ 3. 目標生成 (GoalGenerator.generate)
        │      ├─ SQLiteから直近20件の履歴取得
        │      ├─ Qdrantから意味的に類似した過去アーティファクト取得
        │      ├─ LLMにGoalSchema形式で目標を生成させる
        │      └─ GoalDeduplicatorで重複チェック（cos類似度 > 0.85でリジェクト）
        │
        ├─ 4. SQLiteにloop「running」で記録
        │
        ├─ 5. 実行 (ProducerAgent.execute_goal)
        │      ├─ 過去の失敗ループからレッスン抽出（score < 0.3）
        │      └─ 最大15ステップのツール使用ループ:
        │            execute_command → write_file → read_file → task_complete
        │
        ├─ 6. 評価 (CriticAgent.evaluate)
        │      ├─ サンドボックスからアーティファクトを読み取り
        │      ├─ rubric.jsonに基づいてスコアリング
        │      └─ 重み付きスコア算出 (0.0〜1.0)
        │
        ├─ 7. コスト記録 (CostTracker.record_usage)
        │
        ├─ 8. ベクトル保存 (VectorStore.embed_and_store)
        │      └─ アーティファクトをQdrantに埋め込み保存
        │
        └─ 9. SQLiteにloop最終状態で保存
```

---

## 4. コアコンポーネント詳細

### 4.1 エージェント階層 (`agents.py`, `telos_core.py`, `critic.py`)

```
BaseAgent
├── GoalGenerator      # 目標生成専門エージェント
├── ProducerAgent      # タスク実行専門エージェント
└── CriticAgent        # 評価専門エージェント
```

| エージェント | デフォルトモデル | 役割 |
|---|---|---|
| GoalGenerator | `ollama/qwen2.5-coder:7b` | 次のループの目標を自律的に生成 |
| ProducerAgent | `ollama/qwen2.5-coder:32b` | ツールを使ってゴールを実行 |
| CriticAgent | `openrouter/deepseek/deepseek-chat` | アーティファクトをルーブリックで評価 |

**設計上の重要な分離（Zero-Knowledge Critic）:**
CriticはProducerの思考プロセス（chain-of-thought）を見ず、最終成果物のみを評価する。バイアスを排除するための意図的な設計。

---

### 4.2 GoalGenerator — 目標生成の仕組み

```python
# 入力コンテキスト
initial_intent: str      # 人間が最初に与えたアンビエントインテント
history: List[Dict]      # 直近20ループの goal + score
similar: List[Dict]      # Qdrantから取得した類似過去アーティファクト

# 出力: GoalSchema
title: str               # 目標タイトル（30文字以内）
success_criteria: List[str]  # 具体的・計測可能な達成条件
output_path: str         # 成果物の保存パス
```

重複チェック: `GoalDeduplicator`がcos類似度0.85を閾値として、既存目標との重複を防ぐ。

---

### 4.3 ProducerAgent — 実行ループの仕組み

最大15ステップのツール使用ループ:

| ツール | 機能 |
|---|---|
| `execute_command` | Dockerサンドボックス内でbashコマンド実行 |
| `write_file` | 隔離ワークスペースにファイル書き込み |
| `read_file` | 隔離ワークスペースからファイル読み取り |
| `task_complete` | タスク完了シグナル（ループ終了） |

**終了条件:**
- `task_complete` ツール呼び出し
- 最大ステップ数（15）到達
- 連続エラー3回
- トークン上限超過

---

### 4.4 CriticAgent — 評価の仕組み

**ルーブリック** (`rubric.json`):

| 評価軸 | 重み | 説明 |
|---|---|---|
| completeness | 0.4 | 全要件を満たしているか、プレースホルダーが残っていないか |
| coherence | 0.2 | 論理的一貫性とコード品質 |
| novelty | 0.2 | 過去の履歴と比較した新規性 |
| performance | 0.2 | リソース効率、スレッドセーフ性、CPU/メモリオーバーヘッド |

**スコアリング式:**
```
overall_score = Σ(score[axis] * weight[axis])
```

**ペナルティルール:**
アーティファクトに "placeholder", "TODO", "implement here" が含まれる場合 → completeness = 0.0

---

### 4.5 メモリシステム (`memory.py`)

```
記憶の二層構造:
┌─────────────────────────────┐
│  MemoryStore (SQLite)       │  ← 構造化記憶（全ループの完全な記録）
│  - LoopRecord（ループ履歴） │
│  - AuditLog（LLM呼び出し） │
└─────────────────────────────┘
┌─────────────────────────────┐
│  VectorStore (Qdrant)       │  ← セマンティック記憶（意味的類似検索）
│  - アーティファクトの埋め込み│
│  - コサイン類似度検索       │
└─────────────────────────────┘
```

**LoopRecord スキーマ:**

| フィールド | 型 | 内容 |
|---|---|---|
| id | UUID | ループID |
| created_at | datetime | 作成日時 |
| goal | str | 目標タイトル |
| goal_detail | JSON | GoalSchema完全データ |
| output_path | str | 成果物パス |
| score | float | 総合スコア（0.0〜1.0） |
| score_breakdown | JSON | 評価軸別スコア |
| reasoning | str | Criticの評価理由 |
| tokens_used | int | 使用トークン数 |
| cost_usd | float | コスト（USD） |
| status | enum | running / completed / failed / timeout |
| messages | JSON | 完全な会話ログ |

---

### 4.6 サンドボックス (`sandbox.py`)

```
SandboxManager
├── DockerSandboxStrategy（優先）
│     - ネットワーク: Bridge（ホストから隔離）
│     - メモリ上限: 512m（設定可能）
│     - タイムアウト: 300秒
│     - ファイルI/O: tarストリーム経由
│
└── LocalSandboxStrategy（Dockerなし時のフォールバック）
      - subprocess + shlex（コマンドインジェクション防止）
      - パストラバーサル保護あり
      - workspace/run_{uuid}/ に隔離
```

---

### 4.7 LLMサービス (`llm.py`)

- **litellm** による統一インターフェース（全プロバイダーを単一APIで呼び出し）
- リトライ: 最大5回、指数バックオフ（`2^(attempt-1) * 5` 秒）
- 構造化出力: ツール呼び出し経由でPydanticモデルを強制
- コスト追跡: `litellm.completion_cost()` でコール単位で記録

**対応プロバイダー:**
- OpenAI, Anthropic, Google Gemini, OpenRouter, Ollama, Mistral, Azure 他

---

### 4.8 設定システム (`config.py`, `config.yaml`)

優先順位: **環境変数 > config.yaml > デフォルト値**

**主要設定:**

```yaml
llm:
  producer_model: ollama/qwen2.5-coder:32b
  goal_gen_model: ollama/qwen2.5-coder:7b
  critic_model: openrouter/deepseek/deepseek-chat
  max_tokens_per_loop: 8000

sandbox:
  use_docker: true
  memory_limit: 1024m
  timeout: 300

daily_loop_limit: 1000
monthly_cost_limit: 20.0
max_steps: 15
consecutive_error_limit: 3
deduplication_threshold: 0.85
```

---

## 5. CLIインターフェース

| コマンド | 機能 |
|---|---|
| `telos init [--force]` | ディレクトリ・設定・テンプレート初期化 |
| `telos start [--loops N] [--model M] [--verbose]` | 自律ループ起動 |
| `telos stop` | 実行中エージェントの停止 |
| `telos status [--limit N]` | ループ履歴表示（スコア・コスト） |
| `telos logs [-n LINES] [-f]` | ログ表示（ライブフォロー対応） |
| `telos show [LOOP_ID] [--explain]` | 特定ループの詳細表示 |
| `telos report [--limit N] [-o FILE]` | Markdownレポート生成 |
| `telos clean [--yes]` | ワークスペース・ログのクリア |

---

## 6. 依存関係

| ライブラリ | バージョン | 用途 |
|---|---|---|
| litellm | >=1.0.0 | LLM統一インターフェース |
| qdrant-client | >=1.7.0 | ベクトルデータベース |
| sqlalchemy | >=2.0.0 | SQLite ORM |
| docker | >=7.0.0 | Docker SDK |
| click | >=8.1.0 | CLIフレームワーク |
| pydantic | >=2.5.0 | データバリデーション |
| sentence-transformers | >=2.2.0 | ローカル埋め込みモデル |
| python-dotenv | >=1.0.0 | .env読み込み |
| pyyaml | >=6.0 | YAML設定読み込み |

---

## 7. セキュリティ設計

- **Docker隔離**: ホストファイルシステムへのアクセス不可、メモリ・ネットワーク制限
- **パストラバーサル保護**: `_resolve_safe_path()` で全パスをワークスペース内に制限
- **コスト上限**: 日次ループ数 + 月次コストの二重ガード
- **監査ログ**: 全LLM呼び出しをAuditLogに記録（loop_id追跡可能）
- **自己コード変更不可**: ToolRegistryは静的。エージェントはTelos自身のコードを変更できない

---

## 8. テスト構成

| ファイル | テスト対象 |
|---|---|
| `test_telos_core.py` | Orchestrator, GoalGenerator, ProducerAgent, 安全チェック |
| `test_critic.py` | CriticAgent, ルーブリック読み込み, スコアリング |
| `test_sandbox.py` | SandboxManager, ファイルI/O, パストラバーサル保護 |
| `test_memory.py` | MemoryStore (SQLite), VectorStore (Qdrant) |
| `test_loop.py` | 統合テスト |
| `test_utils.py` | ユーティリティ関数 |
