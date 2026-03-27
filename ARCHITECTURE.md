# Telos — アーキテクチャドキュメント

> 最終更新: 2026-03-27（Critic廃止・本能駆動アーキテクチャへの移行）

---

## 1. プロジェクト概要

**Telos** は「目標を自分で設定して、自分で動き続けるAIエージェントランタイム」。
人間が具体的なタスクを指示しなくても、AIが自律的に目標を生成・実行し、環境から得た本能シグナルを記憶として蓄積しながら次のループに活かすOSSランタイム。

**根本哲学:** 「人間の想像を超えるものをAIに起こさせる」。AIをツールとして使うのではなく、AIが自走するループ回数を圧倒的に増やすことがシンギュラリティへの道。Telosはそのインフラとして機能する。

- **方針**: モデル非依存（litellm経由で全プロバイダー対応）
- **安全性**: Docker隔離 + コスト上限 + 日次ループ上限 + トークン制限
- **学習**: SQLite（構造データ/監査ログ）+ Qdrant（セマンティック記憶）
- **評価**: LLMによる評価なし — 環境シグナル（本能）が唯一の判断基準

### 設計思想: Single Source of Truth

各設定・仕様は**1箇所にのみ**定義される：

| 情報 | 唯一の定義場所 |
|---|---|
| 本能シグナルの計算ロジック | `instincts.py` |
| インフラ設定（Qdrant URL、Docker、コスト上限） | `config.yaml` |
| プロジェクト設定（モデル名、インテント、メモリ設定） | `telos.yaml` |
| 埋め込みモデルの次元数 | `telos.yaml` (`memory.embedding_dimensions`) |
| LLMコスト単価 | `telos.yaml` (`model_cost_overrides`) |
| エージェントの振る舞い | `templates/*.txt` |

---

## 2. プロジェクト構造

```
telos/
├── src/telos/                  # コアソースコード
│   ├── agents.py               # BaseAgent（全エージェントの基底クラス）
│   ├── cli.py                  # CLIエントリポイント（Click）
│   ├── config.py               # 設定管理（YAML + 環境変数 + デフォルト値）
│   ├── db_models.py            # SQLAlchemy ORM（SessionRecord, LoopRecord, InstinctState, AuditLog）
│   ├── deduplicator.py         # GoalDeduplicator（目標重複排除）
│   ├── instincts.py            # InstinctEngine（4本能シグナルの計算）★
│   ├── interfaces.py           # 抽象基底クラス（Tool, TemplateLoader）
│   ├── llm.py                  # LLMService（litellm統一インターフェース）
│   ├── logger.py               # ロギング設定
│   ├── memory.py               # MemoryStore（SQLite）+ VectorStore（Qdrant）
│   ├── sandbox.py              # SandboxManager（Docker/Localの実行環境）
│   ├── schemas.py              # Pydanticスキーマ（GoalSchema）
│   ├── journal.py              # JournalWriter（JOURNAL.md への自動追記）
│   ├── telos_core.py           # Orchestrator, GoalGenerator, ProducerAgent
│   ├── tools.py                # ToolRegistry + ツール実装
│   ├── usage.py                # CostTracker（LLM使用量・コスト追跡）
│   ├── utils.py                # ユーティリティ（JSON修復等）
│   └── migrations/             # DBマイグレーションスクリプト
│       ├── __init__.py
│       ├── add_sessions.py     # sessions テーブル追加マイグレーション
│       └── add_instincts.py    # 本能シグナル列 + InstinctState テーブル追加 ★
│
├── templates/                  # LLMシステムプロンプト
│   ├── producer_system.txt     # Producerエージェントの行動指示
│   └── goal_generation_system.txt # 本能駆動の目標生成プロンプト ★
│
├── tests/                      # テストスイート（pytest）
├── data/                       # データディレクトリ（TELOS_HOME のデフォルト、gitignore）
│   ├── telos.db                # SQLiteデータベース（ループ履歴・本能状態・監査ログ）
│   ├── JOURNAL.md              # セッション・ループの自動記録
│   ├── agent.log               # ローテーティングログ
│   └── workspace/
│       └── persistent/         # セッション内の永続ワークスペース（全ループで共有）
├── config.yaml                 # インフラ設定（Qdrant、Docker、コスト上限）— git管理
├── telos.yaml                  # プロジェクト設定（モデル、intent）— git管理
├── .env                        # APIキー等の環境変数
├── Dockerfile                  # サンドボックスコンテナ定義
└── docker-compose.yml          # Docker構成（Qdrant + Sandbox）
```

---

## 3. システム全体フロー

```
[User]
  │
  ├─ telos init [--force] [--non-interactive]
  │     │
  │     ▼
  │  セットアップウィザード (cli.py)
  │     ├─ 1. OpenRouter APIキー入力 → litellm で検証（max_tokens=1）→ .env に保存
  │     ├─ 2. initial_intent 入力 → telos.yaml に書き込み
  │     ├─ 3. docker info → 起動中なら docker compose up -d（Qdrant）
  │     └─ 4. SentenceTransformer('all-MiniLM-L6-v2') を事前 DL
  │
  └─ telos start --loops N --model M [--name session-name]
        │
        ▼
   CLI (cli.py)
        │
        ├─ _preflight_check(): telos.yaml のモデルからプロバイダー名を動的導出し
        │   対応する API キー（例: OPENROUTER_API_KEY）が設定されているか検証
        │   未設定なら即時終了（telos init を促す）
        │
        ▼
   Orchestrator.__init__
        ├─ SessionRecord を SQLite に作成（status="running"）
        ├─ InstinctEngine を初期化
        └─ instinct_state を全シグナル 0.5 で初期化

        ▼  （loops 回繰り返す）
   Orchestrator.run_iteration()
        │
        ├─ 1. 安全チェック (_check_safety)
        │      ├─ 日次ループ上限チェック（CostTracker.get_daily_loop_count）
        │      └─ 月次コスト上限チェック（CostTracker.get_monthly_cost）
        │
        ├─ 2. サンドボックス起動 (SandboxManager.start)
        │      ├─ Docker優先（ping確認）
        │      └─ 失敗時はLocalSandboxStrategyへ自動フォールバック
        │
        ├─ 3. 目標生成 (GoalGenerator.generate)  ← §4.2 参照
        │      ├─ 現在の instinct_state を注入（C/P/G/O の値 + 強度ラベル）
        │      ├─ SQLiteから履歴取得（直近 + 最近の失敗）
        │      ├─ Qdrantクエリ: 類似アーティファクト取得
        │      ├─ SandboxManager.list_files() → workspace_state 取得
        │      └─ GoalDeduplicatorで重複チェック（cos類似度 > 0.85でリジェクト）
        │
        ├─ 4. SQLiteにloop「running」で記録（session_id を付与）
        │
        ├─ 5. 実行 (ProducerAgent.execute_goal)  ← §4.3 参照
        │      ├─ 最近の失敗ループからレッスン抽出してプロンプトに注入
        │      └─ 最大15ステップのツール使用ループ
        │
        ├─ 6. アーティファクト読み取り + output_stats 抽出
        │      └─ extract_output_stats(): LOC / function_count / import_count / builds_on_previous
        │
        ├─ 7. 本能シグナル計算 (InstinctEngine.compute_state)  ← §4.4 参照
        │      ├─ Curiosity: Qdrant平均コサイン類似度から算出
        │      ├─ Preservation: 直近クラッシュ率・タイムアウト率から算出
        │      ├─ Growth: ローリング複雑度比較（sigmoid）で算出
        │      └─ Order: Qdrant孤立比率から算出
        │
        ├─ 8. コスト記録 (CostTracker.record_usage)
        │
        ├─ 9. ベクトル保存 (VectorStore.embed_and_store)
        │      └─ 完了ループのみ保存（閾値なし、アボートは除外）
        │
        ├─ 10. SQLiteにloop最終状態で保存
        │       └─ exit_code / execution_time_ms / loc / function_count / import_count /
        │          builds_on_previous + InstinctState レコード保存
        │
        └─ 11. JournalWriter でループ結果を JOURNAL.md に即時追記
               （pre/post 本能状態 + output_stats を記録）

  ループ完了後（Orchestrator.shutdown）
        ├─ JournalWriter で Session Summary を JOURNAL.md に追記（final_instincts）
        ├─ SessionRecord を status="completed" に更新
        │      （completed_loops, total_cost_usd を集計）
        └─ persistent workspace はそのまま保持（次セッションへ引き継ぎ）
```

---

## 4. コアコンポーネント詳細

### 4.1 エージェント階層 (`agents.py`, `telos_core.py`)

```
BaseAgent
├── GoalGenerator      # 本能状態を受け取り目標を生成
└── ProducerAgent      # タスク実行専門エージェント

Orchestrator           # セッション管理 + ループオーケストレーション
└── AgentLoop          # Orchestrator のレガシーラッパー（CLI互換）

InstinctEngine         # 本能シグナル計算エンジン（LLMを使わない）
```

**Critic は廃止。** LLMによる評価は行わない。ループあたりのLLMコールは3→2（GoalGen + Producer のみ）に削減。

`Orchestrator.__init__` は `SessionRecord` を SQLite に作成し、`session_id` を全ループに付与する。また `JournalWriter` を初期化してセッションヘッダーを `JOURNAL.md` に追記する。`Orchestrator.shutdown()` はセッション統計（completed_loops, total_cost_usd, final_instincts）を確定させ、Session Summary を JOURNAL.md に追記する。

`BaseAgent.__init__` はエージェント種別（`agent_type`）に応じて設定から自動的にモデルを選択：

```python
selected_model = model or getattr(settings.llm, f"{agent_type}_model", settings.llm.model)
```

| エージェント | `agent_type` | 設定キー | デフォルトモデル |
|---|---|---|---|
| GoalGenerator | `goal_gen` | `llm.goal_gen_model` | `gemini/gemini-flash-latest` |
| ProducerAgent | `producer` | `llm.producer_model` | `gemini/gemini-flash-latest` |

---

### 4.2 GoalGenerator — 本能駆動の目標生成

#### 4本能の緊張構造

GoalGeneratorは `instinct_state`（4シグナルのfloat値）を受け取り、その緊張を「解決」するのではなく「ナビゲート」する目標を生成する。

| シグナル | 意味 | 高い時の解釈 |
|---|---|---|
| **Curiosity (C)** | 探索欲求 | 既知領域が飽和 → 新規ドメインへ |
| **Preservation (P)** | 保存欲求 | 直近ループが不安定 → 慎重に |
| **Growth (G)** | 成長欲求 | 複雑度が停滞 → より難しい課題へ |
| **Order (O)** | 整理欲求 | 記憶が断片化 → 統合・整理へ |

**自然な緊張:** Curiosity vs Preservation（探索 vs 安定）、Growth vs Order（複雑化 vs 収束）。Goal Gen はこの緊張を生きたまま目標に反映する。これが知性の源。

#### プロンプト注入形式

```python
# instinct_state の値に応じて強度ラベルを生成
# > 0.7: "starving", 0.4〜0.7: "moderate", < 0.4: "satisfied"
instinct_section = """
Current Instinct State:
- Curiosity:     0.82 (starving for novelty)
- Preservation:  0.15 (satisfied — low risk tolerance)
- Growth:        0.64 (moderate drive to push complexity)
- Order:         0.41 (moderate — some fragmentation)
"""
```

#### GoalGenerator への入力

```python
# コンテキスト入力
instinct_state: Dict[str, float]  # 4本能シグナル（前ループ後に更新済み）
history: List[Dict]               # 直近ループの goal + status
similar: List[Dict]               # 類似アーティファクト（Qdrantクエリ）
workspace_state: List[Dict]       # persistent/内の既存ファイル一覧
                                  # 例: [{"path": "disk_analyzer.sh", "loop_id": "8518d798", "size_bytes": 1234}]

# 出力: GoalSchema（Pydantic）
title: str                        # 目標タイトル
success_criteria: List[str]       # 具体的・計測可能な達成条件
output_path: str                  # 成果物の保存パス（例: solution.py）
```

#### CONTINUITY PRINCIPLE（継続性の原則）

`workspace_state` が空でない場合（Loop 1 以降）、Generatorは既存の成果物を起点とした目標を生成する。孤立したスクリプトの乱立を防ぎ、蓄積によるシステム成長を促す。

#### DOMAIN ESCAPE（ドメイン脱出）

直近3ループが全て `failed` だった場合は `initial_intent` をQdrantクエリとして使用する（ドメイン引力から強制脱出）。

重複チェック: `GoalDeduplicator` がcos類似度 0.85（`deduplication_threshold`で設定可能）を閾値として、既存目標との重複を防ぐ。

---

### 4.3 ProducerAgent — 実行ループの仕組み

#### ツール一覧

| ツール名 | クラス | 機能 |
|---|---|---|
| `execute_command` | `BashTool` | Dockerサンドボックス内でbashコマンド実行 |
| `write_file` | `WriteFileTool` | 隔離ワークスペースにファイル書き込み |
| `read_file` | `ReadFileTool` | 隔離ワークスペースからファイル読み取り |
| `task_complete` | `TaskCompleteTool` | タスク完了シグナル（ループ終了） |

`LLMService.tools` プロパティがツール定義を遅延ロード（循環インポート回避）し、`chat()` 呼び出し時に自動的にツール定義を付与する。

#### 実行ループ詳細 (`ProducerAgent.execute_goal`)

**Failure Lessons (失敗からの学習):**
直近ループで `status == "failed"` だった場合、そのループの情報を `CRITICAL LESSONS` としてシステムプロンプトに注入する。スコアではなくステータスで判断する。

**Persistent Workspace (永続ワークスペース):**
`workspace/persistent/` はセッションが終わるまで維持される。エージェントは前のループで作成したコードやライブラリを次のループでも継続して利用・改良できる。

```python
had_tool_call = False

for step in range(max_steps):  # 最大15ステップ
    response = llm.chat(messages, system_prompt)

    # トークン上限チェック（累積 > max_tokens_per_loop でアボート）

    if msg.tool_calls:
        had_tool_call = True
        final_result, errors = _handle_tool_calls(...)
        if errors >= consecutive_error_limit:  break  # エラー上限
        if final_result.startswith("TASK_COMPLETE:"):  break  # 完了
    else:
        final_result = msg.content
        if had_tool_call:
            break   # ツール使用後のテキスト = 完了シグナル
        # had_tool_call=False のとき: 計画フェーズとみなしループ継続
```

**重要: 計画テキストの扱い**
DeepSeekなど一部のLLMは最初のステップでツールを呼ばずテキストで計画を述べることがある（指示に従った正常な動作）。`had_tool_call` フラグを使うことで、まだツールを使っていない最初のテキストレスポンスをループ終了と誤判断しない。

#### 終了条件まとめ

| 条件 | status |
|---|---|
| `task_complete` ツール呼び出し | `completed` |
| ツール使用後にテキスト応答 | `completed` |
| 最大ステップ数（15）到達 | `timeout` |
| 連続エラー3回 | `failed` |
| トークン上限超過 | `failed`（aborted） |

#### ツール出力の切り詰め (`_truncate_tool_output`)

`max_output_truncation`（デフォルト8000文字）を超えるツール出力は切り詰め。JSONの場合は `[JSON TRUNCATED]` マーカーを付加してLLMが不完全なJSONを誤解釈しないようにする。

---

### 4.4 InstinctEngine — 本能シグナル計算 (`instincts.py`)

**LLMを一切使わない。** 環境（Qdrant / SQLite）から直接シグナルを計算する。これにより評価のLLM呼び出しが不要になり、コスト削減と「ルーブリックゲーミング」の排除を同時に実現する。

```python
class InstinctEngine:
    def __init__(self, vector_store, memory_store, config=None):
        self.vector = vector_store
        self.sqlite = memory_store
        self.window = 10        # 直近N件を参照

    def compute_curiosity(self, output_embedding) -> float: ...
    def compute_preservation(self) -> float: ...
    def compute_growth(self, current_stats=None) -> float: ...
    def compute_order(self) -> float: ...
    def compute_state(self, output_embedding=None, output_stats=None) -> Dict[str, float]: ...
```

#### 各シグナルの計算式

**Curiosity** — 探索欲求（高 = 飽和 → 新しいものを求める）
```
curiosity = 1.0 - mean_cosine_similarity(top10_qdrant_neighbors)
```
Qdrantで現在の出力に最も近い10件を取得し、平均コサイン類似度を反転。既知領域に近い出力ほど `curiosity` が高くなり、次ループで新規ドメインへの誘引が強まる。

**Preservation** — 保存欲求（高 = 不安定 → 慎重に）
```
preservation = crash_rate * 0.7 + timeout_rate * 0.3
```
直近10ループの失敗率（exit_code != 0）とタイムアウト率から算出。実行が不安定なほど `preservation` が高くなり、Goal Gen に慎重な目標を生成させる。

**Growth** — 成長欲求（高 = 停滞 → 複雑化を求める）
```
complexity = LOC + 5 * function_count + 3 * import_count
growth = sigmoid(mean(newest_5_complexity) - mean(prior_5_complexity))  # 反転
```
最新5件 vs その前5件の複雑度指標の差をsigmoidで正規化。複雑度が上がっていれば `growth` は低下（欲求充足）、停滞していれば高くなる。

**Order** — 整理欲求（高 = 断片化 → 統合を求める）
```
isolation_ratio = count(points where max_neighbor_sim < 0.7) / total_points
order = isolation_ratio
```
Qdrant内の「孤立点」（最近傍との類似度が0.7未満の点）の比率。記憶が断片化しているほど `order` が高くなり、統合・整理系の目標が促進される。

#### extract_output_stats

```python
def extract_output_stats(content: str) -> Dict[str, Any]:
    # LOC: 空行・コメントを除く実効行数
    # function_count: def/function キーワード数（正規表現）
    # import_count: import/require 文数
    # builds_on_previous: 既存ファイルの読み込みパターンがあるか
    return {"loc": int, "function_count": int, "import_count": int, "builds_on_previous": bool}
```

---

### 4.5 LLMサービス (`llm.py`)

#### 基本機能

- **litellm** による統一インターフェース（全プロバイダーを単一APIで呼び出し）
- `litellm.drop_params = True`: モデルがサポートしない追加パラメータを自動除去
- リトライ: 最大5回、指数バックオフ（`2^(attempt-1) * 5` 秒）
- レート制限（429）: 待機時間に +10 秒の追加ペナルティ
- 致命的クォータエラー（`spending cap` / `budget exceeded`）: 即時停止（リトライしない）

#### 構造化出力 (`chat_structured`)

Pydanticモデルを強制するためにツール呼び出し形式を使用：

```python
# スキーマをツール定義に変換
structured_tool = {
    "type": "function",
    "function": {
        "name": f"submit_{ModelClass.__name__.lower()}",
        "parameters": ModelClass.model_json_schema()
    }
}
response = self.chat(tools=[structured_tool], tool_choice={"type": "function", ...})
```

LLMごとの揺れを吸収する正規化パイプライン:
1. **"arguments" ラッパー除去**: ツール引数が二重にネストされる問題を解決。
2. **フラット化**: フィールドが展開されて返ってくる場合、自動的に辞書に再構成。
3. **型修復**: `repair_json` による不完全なJSONの自動修復。

#### トークン上限と安全停止

`max_tokens_per_loop` を超えた累積トークン消費が発生した場合、ループを即座にアボートする。

#### モデルコストオーバーライド

litellmのモデルコストデータベースに未登録のモデル（例: OpenRouter経由のDeepSeek）はコストが $0.00 になる。`model_cost_overrides` でカスタムコストを登録できる：

```yaml
# config.yaml
model_cost_overrides:
  deepseek/deepseek-chat-v3:         # API応答に含まれる実際のmodel ID
    input_cost_per_million: 0.27
    output_cost_per_million: 1.10
```

モジュールロード時に `_apply_cost_overrides()` が実行され `litellm.model_cost` 辞書に登録される。

---

### 4.6 メモリシステム (`memory.py`)

```
記憶の二層構造:
┌─────────────────────────────┐
│  MemoryStore (SQLite)       │  ← 構造化記憶（全ループの完全な記録）
│  - SessionRecord（セッション履歴） │
│  - LoopRecord（ループ履歴）  │
│  - InstinctState（本能履歴） │  ★ NEW
│  - AuditLog（LLM呼び出し）  │
└─────────────────────────────┘
┌─────────────────────────────┐
│  VectorStore (Qdrant)       │  ← セマンティック記憶（意味的類似検索）
│  - アーティファクトの埋め込み│
│  - コサイン類似度検索       │
└─────────────────────────────┘
```

Qdrantが起動していない場合は `available=False` のサイレントフォールバックモードになり、すべての操作が `None` / `[]` を返す（エラーにならない）。

#### SessionRecord スキーマ

| フィールド | 型 | 内容 |
|---|---|---|
| id | UUID | セッションID |
| name | str | セッション名（`--name` オプション、省略時自動生成） |
| created_at | datetime | 開始日時 |
| completed_at | datetime | 終了日時 |
| producer_model | str | 使用したProducerモデル名 |
| goal_gen_model | str | 使用したGoalGenモデル名 |
| intended_loops | int | 指定ループ数 |
| completed_loops | int | 完了ループ数 |
| status | enum | `running` / `completed` / `failed` |
| total_cost_usd | float | セッション合計コスト（USD） |

#### セッション管理メソッド

| メソッド | 機能 |
|---|---|
| `create_session(SessionRecord)` | セッション作成 |
| `update_session(session_id, **kwargs)` | セッション更新（shutdown時に集計） |
| `get_session(session_id)` | セッション取得（8文字ショートID対応） |
| `list_sessions(limit=20)` | セッション一覧 |
| `list_loops_by_session(session_id)` | セッション内の全ループ取得 |
| `export_session_json(session_id)` | セッション + ループを JSON でエクスポート |
| `export_session_csv(session_id)` | セッション + ループを CSV でエクスポート |

#### LoopRecord スキーマ

| フィールド | 型 | 内容 |
|---|---|---|
| id | UUID | ループID |
| created_at | datetime | 作成日時 |
| goal | str | 目標タイトル |
| goal_detail | JSON | GoalSchema完全データ |
| output_path | str | 成果物パス |
| score | float | **常に None**（評価なし） |
| tokens_used | int | 使用トークン数 |
| cost_usd | float | コスト（USD） |
| status | enum | `running` / `completed` / `failed` / `timeout` |
| messages | JSON | 完全な会話ログ（全ツール呼び出し含む） |
| session_id | UUID | 所属セッションID |
| exit_code | int | サンドボックスの終了コード ★ |
| execution_time_ms | int | 実行時間（ミリ秒） ★ |
| memory_peak_bytes | int | ピークメモリ使用量 ★ |
| loc | int | 成果物の実効行数 ★ |
| function_count | int | 関数定義数 ★ |
| import_count | int | インポート文数 ★ |
| builds_on_previous | bool | 既存ファイルを利用したか ★ |

#### InstinctState テーブル（★ NEW）

| フィールド | 型 | 内容 |
|---|---|---|
| loop_id | UUID (FK) | LoopRecord への外部キー（主キー） |
| curiosity | float | 探索欲求シグナル（0.0〜1.0） |
| preservation | float | 保存欲求シグナル（0.0〜1.0） |
| growth | float | 成長欲求シグナル（0.0〜1.0） |
| order_drive | float | 整理欲求シグナル（0.0〜1.0） |
| timestamp | datetime | 記録日時 |

#### get_quality_history (更新済み)

スコアベースの品質加重をやめ、ステータスベースの履歴を返す：

| ロール | 条件 | 上限 |
|---|---|---|
| 直近ループ | 時系列順 | 10件 |
| 最近の失敗 | status == "failed" | 5件 |

失敗ループには利用可能な場合に本能シグナルを付与する。重複は除去してマージ。

#### 埋め込みモデルの次元数解決

`VectorStore` はベクトルサイズを以下の優先順位で決定する：

```
1. telos.yaml の memory.embedding_dimensions（明示設定）
        ↓ なければ
2. _KNOWN_DIMENSIONS 辞書によるモデル名からの自動検出
   （all-MiniLM-L6-v2 → 384, text-embedding-3-large → 3072 等）
        ↓ 辞書にないモデルなら
3. デフォルト 1536 + 警告ログ
```

既知のモデルと次元数（`_KNOWN_DIMENSIONS`）:

| モデル | 次元数 |
|---|---|
| `all-MiniLM-L6-v2` | 384 |
| `all-mpnet-base-v2` | 768 |
| `nomic-embed-text` | 768 |
| `text-embedding-ada-002` | 1536 |
| `text-embedding-3-small` | 1536 |
| `text-embedding-3-large` | 3072 |

既存コレクションのベクトルサイズが現在の設定と一致しない場合、コレクションを自動再作成する。

---

### 4.7 目標重複排除 (`deduplicator.py`)

`GoalDeduplicator` はローカルの `SentenceTransformer` モデルを使用。APIキーが不要で高速。

**APIモデルへのフォールバック防止:**
`config.yaml` で APIスタイルの埋め込みモデル（`/` を含むモデル名、例: `openai/text-embedding-3-small`）が設定されていても、重複排除は `all-MiniLM-L6-v2` ローカルモデルにフォールバックする。APIモデルはネットワーク遅延・コストが発生するため。

```python
_LOCAL_FALLBACK = 'all-MiniLM-L6-v2'

if '/' in resolved and not resolved.startswith('sentence-transformers/'):
    log.info("API embedding model not usable locally; falling back to %s", _LOCAL_FALLBACK)
    resolved = _LOCAL_FALLBACK
```

---

### 4.8 サンドボックス (`sandbox.py`)

```
SandboxManager
├── DockerSandboxStrategy（優先）
│     - ネットワーク: Bridge（ホストから隔離）
│     - メモリ上限: 1024m（設定可能）
│     - タイムアウト: 300秒（設定可能）
│     - ファイルI/O: tarストリーム経由（get_archive / put_archive）
│     - コマンド実行: /workspace をworkdirとして exec_run
│     - list_files(): find -mindepth 2 -maxdepth 2 でループID別サブディレクトリを列挙
│
└── LocalSandboxStrategy（Dockerなし時のフォールバック）
      - subprocess + shlex（コマンドインジェクション防止）
      - shell=False で実行（シェルインジェクション不可）
      - パストラバーサル保護あり
      - list_files(): Path.rglob() でループID別サブディレクトリを列挙
```

**`list_files()` の戻り値形式:**
```python
[
    {"path": "disk_analyzer.sh", "loop_id": "8518d798", "size_bytes": 1234},
    {"path": "heatmap.py",       "loop_id": "77b28adf", "size_bytes": 5678},
]
```
深さ2のファイル（`{loop_id[:8]}/{filename}`）のみを対象とし、それ以外のパス構造は無視する。`SandboxManager.list_files()` はパストラバーサル防止のため `_resolve_safe_path()` によるセキュリティチェックを行う。

**永続ワークスペース:**
`Orchestrator` は `workspace/persistent/` を共有ワークスペースとして使用。同一セッション内の全ループが同じワークスペースにアクセスでき、ループ間での成果物の積み上げが可能。**セッションをまたいでも保持される**（`telos reset` を実行するまで削除されない）。ProducerAgentは新しいループを始める前に既存ファイルを確認し、前回の成果物を起点として積み上げることが期待される。

---

### 4.9 JournalWriter (`journal.py`)

各プロジェクトの `JOURNAL.md` にセッション・ループの結果を自動追記する。

#### 書き込みタイミング

| タイミング | 内容 |
|---|---|
| `Orchestrator.__init__` | `## Session ...` ヘッダーを追記 |
| `run_iteration()` 完了直後 | ループブロック（Goal/本能状態/Stats）を即時追記 |
| `Orchestrator.shutdown()` | Session Summary（final_instincts）を追記 |

#### JOURNAL.md フォーマット

```markdown
# Telos Journal — <project_name>

---

## Session abc12345 | 2026-03-27 14:15 | openrouter/anthropic/claude-sonnet-4-6

### Loop 1 ✅
**Goal:** ファイルシステムの構造を調査する
**Pre-instincts:** curiosity=0.82 | preservation=0.15 | growth=0.64 | order=0.41
**Post-instincts:** curiosity=0.58 | preservation=0.12 | growth=0.51 | order=0.45
**Artifact:** abc12345/explorer.py
**Output:** 47 LOC, 3 functions, 2 imports

### Loop 2 ❌
**Goal:** 外部APIへの接続を試みる
**Pre-instincts:** curiosity=0.58 | preservation=0.12 | growth=0.51 | order=0.45
**Post-instincts:** curiosity=0.60 | preservation=0.45 | growth=0.52 | order=0.48
**Artifact:** —
**Output:** 0 LOC, 0 functions, 0 imports

---
**Session Summary:** 2 loops | cost: $0.031 | final instincts: C=0.60 P=0.45 G=0.52 O=0.48
---
```

ステータスアイコン: `✅` (status == "completed") / `❌` (failed/timeout)

すべての書き込みは append モードで行い、既存コンテンツを読み込まない（高速・安全）。`telos reset` 実行時は `JOURNAL.md` も削除対象に含まれる。

---

### 4.10 設定システム (`config.py`)

#### 2ファイル構成

| ファイル | 責務 | 編集頻度 |
|---|---|---|
| `config.yaml` | インフラ（Qdrant URL、Docker、ログ、コスト上限） | 低 |
| `telos.yaml` | プロジェクト（モデル、intent、メモリパラメータ） | 高 |

両ファイルともgit管理対象。

#### 読み込み優先順位（高→低）

```
環境変数  >  telos.yaml  >  config.yaml  >  Pydanticデフォルト値
```

`config.yaml` をベースに読み込み、`telos.yaml` を **deep merge** で上書きする。同じキーは `telos.yaml` が勝つ。設定はモジュールレベルのキャッシュ（`_settings_cache`）で保持。`reload_settings()` でキャッシュを破棄して再読み込み可能。

#### `config.yaml` の主要項目（インフラ設定）

```yaml
memory:
  qdrant_url: http://localhost:6333

sandbox:
  image: telos-sandbox:latest
  container_name: telos-agent-sandbox
  use_docker: true
  memory_limit: 1024m
  timeout: 300

logging:
  level: INFO                       # DEBUG で詳細ログ

daily_loop_limit: 1000
monthly_cost_limit: 20.0
```

#### `telos.yaml` の主要項目（プロジェクト設定）

```yaml
initial_intent: "Establish existence and evolve."

llm:
  producer_model: openrouter/anthropic/claude-sonnet-4-6
  goal_gen_model: openrouter/deepseek/deepseek-chat-v3-0324
  max_tokens_per_loop: 150000

memory:
  collection_name: telos_artifacts
  embedding_model: all-MiniLM-L6-v2
  embedding_dimensions: 384
  persistent_workspace_name: persistent

history_limit: 20
similar_artifacts_limit: 3
max_lessons: 2
rate_limit_delay: 2.0
deduplication_threshold: 0.85

model_cost_overrides:
  deepseek/deepseek-chat-v3:
    input_cost_per_million: 0.27
    output_cost_per_million: 1.10
```

> **注意**: `max_steps`（15）, `consecutive_error_limit`（3）, `max_output_truncation`（8000）は省略可能。Pydanticデフォルト値が使われる。

#### 環境変数オーバーライド

| 環境変数 | 上書き対象 |
|---|---|
| `TELOS_PRODUCER_MODEL` | `llm.producer_model` |
| `TELOS_EMBEDDING_MODEL` | `memory.embedding_model` |
| `QDRANT_URL` | `memory.qdrant_url` |
| `TELOS_USE_DOCKER` | `sandbox.use_docker` |
| `TELOS_HOME` | データディレクトリパス（デフォルト: `./data/`） |

---

## 5. CLIインターフェース

| コマンド | 機能 |
|---|---|
| `telos init [--force] [--non-interactive]` | セットアップウィザード（APIキー検証・`telos.yaml` 生成・Docker 起動・埋め込みモデル DL）。`--force` で強制上書き、`--non-interactive` で全プロンプトスキップ（CI用） |
| `telos start [--loops N] [--model M] [--name NAME]` | 自律ループ起動。起動前に preflight チェック（APIキー確認）を実行。セッション作成・JOURNAL.md自動更新 |
| `telos stop` | 実行中エージェントの停止 |
| `telos reset [--yes]` | アクティブプロジェクトのDB・ワークスペース・ログ・JOURNALを削除 |
| `telos project list` | 全プロジェクト一覧を表示（★ = アクティブ） |
| `telos project new NAME` | 新しいプロジェクトを作成してスイッチ |
| `telos project switch NAME` | アクティブプロジェクトを切り替え |
| `telos project delete NAME [--yes]` | プロジェクトを完全削除 |

### プロジェクト管理

`telos project switch` は `TELOS_HOME=projects/<name>` を `.env.local` に書き込み、次回起動から有効になる。`TELOS_HOME` 未設定時のデフォルトは `./data/`。

```
telos/                      ← リポジトリルート
├── config.yaml             ← インフラ設定（git管理）
├── telos.yaml              ← プロジェクト設定（git管理）
├── data/                   ← TELOS_HOME デフォルト（gitignore）
│   ├── telos.db
│   ├── JOURNAL.md
│   ├── workspace/
│   └── agent.log
└── projects/               ← 複数プロジェクト使用時（gitignore）
    ├── main/
    │   ├── telos.db
    │   └── workspace/
    └── experiment-v2/
        ├── telos.db
        └── workspace/
```

---

### ターミナル出力フォーマット

`telos start` 実行中のシンプルな進捗出力：

```
[Telos] Session started: abc12345 | openrouter/anthropic/claude-sonnet-4-6
[Loop 1] Generating goal...
[Loop 1] Goal: ファイルシステムの構造を調査する
[Loop 1] ✅ instincts: C=0.58 P=0.12 G=0.51 O=0.45
[Loop 2] Generating goal...
[Loop 2] Goal: 外部APIへの接続を試みる
[Loop 2] ❌ instincts: C=0.60 P=0.45 G=0.52 O=0.48
[Session] Complete: 2 loops | $0.031 | JOURNAL updated
```

詳細な実行ログは `TELOS_HOME/agent.log` を参照。ループ結果の記録は `TELOS_HOME/JOURNAL.md` を参照。

---

## 6. 依存関係

| ライブラリ | 用途 |
|---|---|
| litellm | LLM統一インターフェース（全プロバイダー対応） |
| qdrant-client | ベクトルデータベースクライアント |
| sqlalchemy | SQLite ORM |
| docker | Docker SDK（サンドボックス管理） |
| click | CLIフレームワーク |
| pydantic | データバリデーション・スキーマ定義 |
| sentence-transformers | ローカル埋め込みモデル（all-MiniLM-L6-v2） |
| python-dotenv | .env読み込み |
| pyyaml | YAML設定読み込み |

---

## 7. セキュリティ設計

- **監査ログ（AuditLog）**: 全LLMコールのモデル、トークン、コスト、時間をSQLiteに永続化。事後分析を可能にする。
- **コスト上限**: 日次ループ数 + 月次コストの二重ガード。
- **トークン制限**: 1ループあたりの経過トークン数による自動アボート。
- **Docker隔離**: ホストFSアクセス不可、リソース制限。
- **パストラバーサル保護**: `_resolve_safe_path()` によるワークスペース外アクセスの完全遮断。

---

## 8. DBマイグレーション

既存のデータベースに対して以下のスクリプトで新スキーマを適用できる（idempotent — 複数回実行しても安全）：

```bash
# 本能シグナル列 + InstinctState テーブルの追加
python -m telos.migrations.add_instincts
```

既存の `loops` テーブルに `exit_code`, `execution_time_ms`, `memory_peak_bytes`, `loc`, `function_count`, `import_count`, `builds_on_previous` 列を追加し、新規に `instinct_states` テーブルを作成する。既存レコードは全フィールドが `NULL` になる。

---

## 9. テスト構成

| ファイル | テスト対象 |
|---|---|
| `test_config.py` | 設定システム: `_deep_merge`, `load_settings`（グローバルのみ・プロジェクトのみ・マージ・env var優先） |
| `test_telos_core.py` | Orchestrator（セッション作成・shutdown含む）, GoalGenerator, ProducerAgent, 安全チェック |
| `test_sandbox.py` | SandboxManager, ファイルI/O, パストラバーサル保護 |
| `test_memory.py` | MemoryStore (SQLite, セッション管理含む), VectorStore (Qdrant), 埋め込み次元数解決 |
| `test_loop.py` | 統合テスト |
| `test_utils.py` | ユーティリティ関数 |
| `verify_memory.py` | MemoryStore/VectorStore 動作検証スクリプト（非pytest） |
| `verify_sandbox.py` | Sandbox 動作検証スクリプト（非pytest） |
| `verify_vector.py` | VectorStore 動作検証スクリプト（非pytest） |

`pytest tests/ -v` でメインテストスイートを実行。

---

## 10. よくある問題と対処

### ループがすぐに失敗する

| 原因 | 確認方法 | 対処 |
|---|---|---|
| Producerがファイルを書き込まなかった | `JOURNAL.md` の Artifact が `—` でないか確認 | `producer_system.txt` の指示を確認 |
| 連続エラー上限に達した | ログに `Consecutive tool errors exceeded` があるか | `logging.level: DEBUG` にして詳細確認 |
| サンドボックスが起動していない | ログに `sandbox start failed` があるか | `docker-compose up -d` でDocker起動確認 |

### コストが $0.00 になる

OpenRouter経由で使うモデルはlitellmのコスト辞書に登録されていない場合がある。`model_cost_overrides` にモデルIDとコストを追加する。
APIレスポンスの実際のモデルIDは `logging.level: DEBUG` にすると確認できる。

### Qdrant接続エラー

VectorStoreは接続失敗時にサイレントフォールバック（`available=False`）するため、ループ自体は継続する。ただし本能シグナルの計算精度が低下する（固定値 0.5 にフォールバック）。
`docker-compose up -d qdrant` でQdrantを起動することを推奨。

### 本能シグナルが全て 0.5 のまま

Qdrant未接続か、ループ数が少なすぎて十分な履歴がない（window=10）。数ループ実行後に変動し始める。
