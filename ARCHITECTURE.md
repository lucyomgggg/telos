# Telos — アーキテクチャドキュメント

> 最終更新: 2026-03-16（TUI Dashboard・config.yaml更新反映）

---

## 1. プロジェクト概要

**Telos** は「目標を自分で設定して、自分で動き続けるAIエージェントランタイム」。
人間が具体的なタスクを指示しなくても、AIが自律的に目標を生成・実行・評価し、その結果を記憶として蓄積しながら次のループに活かすOSSランタイム。

- **方針**: モデル非依存（litellm経由で全プロバイダー対応）
- **安全性**: Docker隔離 + コスト上限 + 日次ループ上限 + トークン制限
- **学習**: SQLite（構造データ/監査ログ）+ Qdrant（セマンティック記憶）

### 設計思想: Single Source of Truth

各設定・仕様は**1箇所にのみ**定義される。コードの複数箇所が同じ値を持つ「局所最適の連鎖」を避ける：

| 情報 | 唯一の定義場所 |
|---|---|
| 評価軸・重み | `rubric.json` |
| モデル名・インフラ設定 | `config.yaml` |
| 初期起動インテント | `config.yaml` (`initial_intent`) |
| 埋め込みモデルの次元数 | `config.yaml` (`embedding_dimensions`) |
| LLMコスト単価 | `config.yaml` (`model_cost_overrides`) |
| エージェントの振る舞い | `templates/*.txt` |

---

## 2. プロジェクト構造

```
telos/
├── src/telos/                  # コアソースコード
│   ├── agents.py               # BaseAgent（全エージェントの基底クラス）
│   ├── cli.py                  # CLIエントリポイント（Click）
│   ├── config.py               # 設定管理（YAML + 環境変数 + デフォルト値）
│   ├── critic.py               # CriticAgent（評価エージェント）
│   ├── dashboard/              # TUIダッシュボードモジュール
│   │   ├── __init__.py
│   │   └── tui.py              # TelosDashboard（Textual App）
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
│   ├── producer_system.txt     # Producerエージェントの行動指示
│   ├── critic_system.txt       # Criticエージェントの評価指示（軸は注入しない）
│   └── goal_generation_system.txt # 目標生成の駆動プロンプト
│
├── tests/                      # テストスイート（pytest）
├── data/
│   ├── telos.db                # SQLiteデータベース（ループ履歴・監査ログ）
│   └── qdrant/                 # Qdrantベクトルストアデータ
├── workspace/
│   └── persistent/             # セッション内の永続ワークスペース（全ループで共有）
├── outputs/                    # 生成レポート
├── config.yaml                 # メイン設定ファイル（単一設定源）
├── rubric.json                 # 評価ルーブリック（評価軸・重みの単一定義源）
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
        │      ├─ 日次ループ上限チェック（CostTracker.get_daily_loop_count）
        │      └─ 月次コスト上限チェック（CostTracker.get_monthly_cost）
        │
        ├─ 2. サンドボックス起動 (SandboxManager.start)
        │      ├─ Docker優先（ping確認）
        │      └─ 失敗時はLocalSandboxStrategyへ自動フォールバック
        │
        ├─ 3. 目標生成 (GoalGenerator.generate)
        │      ├─ SQLiteから直近20件の履歴取得（goal + score）
        │      ├─ Qdrantから意味的に類似した過去アーティファクト取得
        │      ├─ LLMにGoalSchema形式（title/success_criteria/output_path）で生成
        │      └─ GoalDeduplicatorで重複チェック（cos類似度 > 0.85でリジェクト）
        │
        ├─ 4. SQLiteにloop「running」で記録
        │
        ├─ 5. 実行 (ProducerAgent.execute_goal)  ← 詳細は §4.3
        │      ├─ score < 0.3 のループからレッスン抽出してシステムプロンプトに注入
        │      └─ 最大15ステップのツール使用ループ
        │
        ├─ 6. 評価 (CriticAgent.evaluate)  ← 詳細は §4.4
        │      ├─ [スキップ条件] result が "Loop aborted:" で始まる場合
        │      │    └─ score=0.0・failed=True を直接セットしてCriticを呼ばない
        │      │       （現状: トークン上限超過のみ該当）
        │      ├─ サンドボックスからアーティファクト読み取り
        │      ├─ rubric.jsonの軸をプロンプトに動的注入
        │      └─ 重み付きスコア算出 (0.0〜1.0)
        │
        ├─ 7. コスト記録 (CostTracker.record_usage)
        │
        ├─ 8. ベクトル保存 (VectorStore.embed_and_store)
        │      ├─ score > failure_threshold の場合のみ実行
        │      └─ アーティファクト内容をQdrantに埋め込み保存
        │         （アボート・低スコアループはQdrantに保存されない）
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

`BaseAgent.__init__` はエージェント種別（`agent_type`）に応じて設定から自動的にモデルを選択：

```python
selected_model = model or getattr(settings.llm, f"{agent_type}_model", settings.llm.model)
```

| エージェント | `agent_type` | 設定キー | デフォルトモデル |
|---|---|---|---|
| GoalGenerator | `goal_gen` | `llm.goal_gen_model` | `gemini/gemini-flash-latest` |
| ProducerAgent | `producer` | `llm.producer_model` | `gemini/gemini-flash-latest` |
| CriticAgent | `critic` | `llm.critic_model` | `gemini/gemini-flash-latest` |

**設計上の重要な分離（Zero-Knowledge Critic）:**
CriticはProducerの思考プロセス（chain-of-thought）を見ず、最終成果物のみを評価する。バイアスを排除するための意図的な設計。

---

### 4.2 GoalGenerator — 目標生成の仕組み

#### AI Instincts (内部衝動)

人間の具体的な指示に従うのではなく、`templates/goal_generation_system.txt` に定義された4つの本能に基づいて自律的に目標を決定する。

1. **CURIOSITY**: 未知のファイルやシステムの開拓（「何が起きるか？」の探求）。
2. **SELF-PRESERVATION**: コードの堅牢性と実行確認の重視。
3. **UTILITY/GROWTH**: ワークスペース内での機能的なツールの構築。
4. **ENTROPY REDUCTION**: 既存コードの統合、リファクタリング、洗練。

#### Qdrant Query Stabilization (クエリ安定化)

過去の記憶を検索する際、`initial_intent` ではなく**直近の目標タイトル**を検索クエリとして使用する。これにより、初期インテントが強すぎる「引力」として働き、同じようなドメインの記憶ばかりが引き寄せられるバイアス（Intent-bias amplification）を回避している。

```python
# コンテキスト入力
initial_intent: str         # Ambient Intent（北極星のような指針）
history: List[Dict]         # 直近20ループの goal + score
similar: List[Dict]         # 直近目標をクエリに取得した類似記憶

# 出力: GoalSchema（Pydantic）
title: str                  # 目標タイトル
success_criteria: List[str] # 具体的・計測可能な達成条件
output_path: str            # 成果物の保存パス（例: solution.py）
```

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
直近のループでスコアが `failure_threshold` (デフォルト0.3) を下回った場合、そのループの評価理由を `CRITICAL LESSONS` としてシステムプロンプトに注入する。これにより、同じ過ちを繰り返さない「適応型プロンプト」を実現している。

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

| 条件 | 状態 |
|---|---|
| `task_complete` ツール呼び出し | 正常完了 |
| ツール使用後にテキスト応答 | 正常完了とみなす |
| 最大ステップ数（15）到達 | タイムアウト |
| 連続エラー3回 | アボート（Criticスキップ） |
| トークン上限超過 | アボート（Criticスキップ） |

#### ツール出力の切り詰め (`_truncate_tool_output`)

`max_output_truncation`（デフォルト8000文字）を超えるツール出力は切り詰め。JSONの場合は `[JSON TRUNCATED]` マーカーを付加してLLMが不完全なJSONを誤解釈しないようにする。

---

### 4.4 CriticAgent — 評価の仕組み

#### ルーブリック駆動設計 (`rubric.json` が単一定義源)

評価軸・重み・説明はすべて `rubric.json` に定義。コードに軸名はハードコードされていない。

```json
{
  "axes": [
    {"name": "completeness", "weight": 0.4, "description": "..."},
    {"name": "coherence",    "weight": 0.2, "description": "..."},
    {"name": "novelty",      "weight": 0.2, "description": "..."},
    {"name": "performance",  "weight": 0.2, "description": "..."}
  ]
}
```

ルーブリックの読み込み優先順位:
1. `critic_agent` 引数で渡された `rubric_path`
2. `config.yaml` の `critic.rubric_path`
3. フォールバック: `TELOS_HOME/rubric.json`（存在しない場合は4軸デフォルトを自動生成）

#### プロンプトへの動的注入

`critic_system.txt` テンプレートには評価軸の記述を**含まない**。実行時に `rubric.json` から軸を読み取りシステムプロンプトに注入：

```python
axes_lines = "\n".join(f"  - {a['name']}: {a['description']}" for a in rubric["axes"])
system_prompt = f"{base_template}\n\nScore ALL of the following axes (0.0 to 1.0) inside the `scores` field:\n{axes_lines}"
```

軸を追加・変更するには `rubric.json` を編集するだけでよい。コードの変更は不要。

#### スコアリング

```python
# EvaluationResponse (schemas.py)
class EvaluationResponse(BaseModel):
    scores: Dict[str, float]  # 軸名 → スコア（0.0〜1.0）
    criteria_met: List[bool]
    reasoning: str

# 重み付き総合スコア（critic.py）
overall_score = sum(
    axis["weight"] * response.scores.get(axis["name"], 0.0)
    for axis in rubric["axes"]
)
```

評価失敗時（例外）: `score=0.0`, `failed=True` を返し、ループ記録に `failed` フラグが立つ。

#### Critic スキップ条件

`Orchestrator.run_iteration` はProducer実行結果の文字列を検査し、`"Loop aborted:"` で始まる場合はCriticを呼ばずスコアを直接セットする：

```python
if result.startswith("Loop aborted:"):
    eval_res = {"overall_score": 0.0, "breakdown": {}, "failed": True, "reasoning": result}
else:
    eval_res = self.critic.evaluate(...)
```

| 終了状態 | result文字列 | Critic呼び出し |
|---|---|---|
| トークン上限超過 | `"Loop aborted: Exceeded token limit."` | **スキップ** |
| 連続エラー上限 | `"Loop aborted: Consecutive tool errors exceeded limit."` | **スキップ** |
| 最大ステップ到達 | `"Loop reached max steps."` | 呼ばれる |
| 正常完了 | `"TASK_COMPLETE: ..."` | 呼ばれる |

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

#### 構造化出力と正規化 (`chat_structured`)

Pydanticモデルを強制するためにツール呼び出し形式を使用し、LLMごとの揺れを吸収する正規化パイプラインを通す。

1. **"arguments" ラッパー除去**: ツール引数が二重にネストされる問題を解決。
2. **フラット化**: `scores` フィールドが展開されて返ってくる（DeepSeek等に多い）場合、自動的に辞書に再構成。
3. **型修復**: `repair_json` による不完全なJSONの自動修復。

#### トークン上限と安全停止

`max_tokens_per_loop` を超えた累積トークン消費が発生した場合、ループを即座にアボートする。無限ループや高額な推論コストの発生をコードレベルで防止する。

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
│  - LoopRecord（ループ履歴） │
│  - AuditLog（LLM呼び出し） │
└─────────────────────────────┘
┌─────────────────────────────┐
│  VectorStore (Qdrant)       │  ← セマンティック記憶（意味的類似検索）
│  - アーティファクトの埋め込み│
│  - コサイン類似度検索       │
└─────────────────────────────┘
```

Qdrantが起動していない場合は `available=False` のサイレントフォールバックモードになり、すべての操作が `None` / `[]` を返す（エラーにならない）。

#### LoopRecord スキーマ

| フィールド | 型 | 内容 |
|---|---|---|
| id | UUID | ループID |
| created_at | datetime | 作成日時 |
| goal | str | 目標タイトル |
| goal_detail | JSON | GoalSchema完全データ |
| output_path | str | 成果物パス |
| score | float | 総合スコア（0.0〜1.0） |
| score_breakdown | JSON | 評価軸別スコア辞書 |
| reasoning | str | Criticの評価理由 |
| tokens_used | int | 使用トークン数 |
| cost_usd | float | コスト（USD） |
| status | enum | `running` / `completed` / `failed` / `timeout` |
| messages | JSON | 完全な会話ログ（全ツール呼び出し含む） |

#### 埋め込みモデルの次元数解決

`VectorStore` はベクトルサイズを以下の優先順位で決定する：

```
1. config.yaml の memory.embedding_dimensions（明示設定）
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
│
└── LocalSandboxStrategy（Dockerなし時のフォールバック）
      - subprocess + shlex（コマンドインジェクション防止）
      - shell=False で実行（シェルインジェクション不可）
      - パストラバーサル保護あり
```

**永続ワークスペース:**
`Orchestrator` は `workspace/persistent/` を共有ワークスペースとして使用。同一セッション内の全ループが同じワークスペースにアクセスでき、ループ間での成果物の積み上げが可能。セッション終了時（`Orchestrator.shutdown()`）に一括クリーンアップ。

---

### 4.9 TUIダッシュボード (`dashboard/tui.py`)

`telos dashboard` コマンドで起動するインタラクティブなターミナルダッシュボード。**Textual** フレームワークで実装。

#### タブ構成

| タブ | 内容 |
|---|---|
| Overview | スコア推移グラフ（plotext） + ルーブリック軸別平均バー |
| Goals | 全ループ一覧テーブル（スコア・ステータス・目標・日時） |
| Learning | 失敗→改善ペアカード（Failure Lessons の可視化） |
| Costs | モデル別コスト統計テーブル + コスト推計パネル |

#### キーバインド

| キー | アクション |
|---|---|
| `r` | データ再読み込み |
| `e` | アクティブタブをファイルにエクスポート（Markdown/CSV） |
| `q` | 終了 |

#### データソース

`TelosDashboard` は `MemoryStore` から以下のメソッドを通じてデータを取得する：

| メソッド | 用途 |
|---|---|
| `get_dashboard_summary()` | 総ループ数・平均スコア・高スコア率・累積コスト |
| `get_score_progression(limit=100)` | スコア推移（ループ番号順） |
| `get_goal_diversity(limit=200)` | 全ループのゴール一覧 |
| `get_failure_improvement_pairs()` | 失敗→改善ペア |
| `get_model_cost_stats()` | モデル別コスト統計 |
| `get_score_breakdown_averages()` | ルーブリック軸別平均スコア |

5秒ごとに自動更新（`set_interval(5, _load_data)`）。アクティブなタブのウィジェットのみ更新し、未表示タブへの無駄な描画を回避。

---

### 4.10 設定システム (`config.py`, `config.yaml`)

優先順位: **環境変数 > config.yaml > Pydanticデフォルト値**

設定はモジュールレベルのキャッシュ（`_settings_cache`）で保持。`reload_settings()` でキャッシュを破棄して再読み込み可能。

**現在の `config.yaml` 全項目:**

```yaml
llm:
  producer_model: openrouter/anthropic/claude-sonnet-4.6
  goal_gen_model: openrouter/deepseek/deepseek-chat-v3-0324
  critic_model: openrouter/deepseek/deepseek-chat-v3-0324
  max_tokens_per_loop: 150000       # 累積トークン上限

memory:
  qdrant_url: http://localhost:6333
  collection_name: telos_artifacts
  embedding_model: all-MiniLM-L6-v2
  embedding_dimensions: 384         # 明示設定でコード依存を排除
  workspace_path: workspace         # ワークスペースベースパス
  persistent_workspace_name: persistent  # 永続ワークスペースのサブディレクトリ名

sandbox:
  image: telos-sandbox:latest
  container_name: telos-agent-sandbox
  use_docker: true
  memory_limit: 1024m
  timeout: 300

critic:
  rubric_path: rubric.json          # 省略時は TELOS_HOME/rubric.json

logging:
  level: INFO                       # DEBUG で詳細ログ

initial_intent: "Establish existence and evolve."  # 北極星インテント
history_limit: 20                   # GoalGenerator に渡す直近ループ件数
similar_artifacts_limit: 3          # Qdrantから取得する類似記憶の最大件数
failure_threshold: 0.3              # Failure Lessons を抽出するスコア下限
max_lessons: 2                      # システムプロンプトに注入するレッスンの最大数
daily_loop_limit: 1000
monthly_cost_limit: 20.0
rate_limit_delay: 2.0               # LLMコール間の待機秒数
deduplication_threshold: 0.85

model_cost_overrides:               # litellm未登録モデルのコスト定義
  deepseek/deepseek-chat-v3:
    input_cost_per_million: 0.27
    output_cost_per_million: 1.10
  anthropic/claude-4.6-sonnet-20260217:
    input_cost_per_million: 3.00
    output_cost_per_million: 15.00
```

> **注意**: `max_steps`, `consecutive_error_limit`, `max_output_truncation` は設定ファイルから削除され、コードのデフォルト値（各15, 3, 8000）に統合された。

**環境変数オーバーライド:**

| 環境変数 | 上書き対象 |
|---|---|
| `TELOS_PRODUCER_MODEL` | `llm.producer_model` |
| `TELOS_CRITIC_MODEL` | `llm.critic_model` |
| `TELOS_EMBEDDING_MODEL` | `memory.embedding_model` |
| `QDRANT_URL` | `memory.qdrant_url` |
| `TELOS_USE_DOCKER` | `sandbox.use_docker` |
| `TELOS_HOME` | データディレクトリパス |

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
| `telos dashboard` | インタラクティブTUIダッシュボード起動 |

### セッションコストサマリー

`telos start` 終了時に自動表示：

```
========================================================
💰 Session Cost Summary
   This session:   $0.0123
   Month-to-date:  $0.4567

   Model Stack Breakdown:
   openrouter/anthropic/claude-sonnet-4.6  [producer]  $0.0100  ( 12,345 tok)  avg $0.0100/loop
   openrouter/deepseek/deepseek-chat-v3... [goal_gen]  $0.0015  (  3,210 tok)  avg $0.0015/loop
   openrouter/deepseek/deepseek-chat-v3... [critic]    $0.0008  (  1,890 tok)  avg $0.0008/loop
========================================================
```

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
| textual | TUIダッシュボード（`telos dashboard`） |
| plotext | ターミナル上のスコア推移グラフ描画 |

---

## 7. セキュリティ設計

- **監査ログ（AuditLog）**: 全LLMコールのモデル、トークン、コスト、時間をSQLiteに永続化。事後分析を可能にする。
- **コスト上限**: 日次ループ数 + 月次コストの二重ガード。
- **トークン制限**: 1ループあたりの経過トークン数による自動アボート。
- **Docker隔離**: ホストFSアクセス不可、リソース制限。
- **パストラバーサル保護**: `_resolve_safe_path()` によるワークスペース外アクセスの完全遮断。

---

## 8. テスト構成

| ファイル | テスト対象 |
|---|---|
| `test_telos_core.py` | Orchestrator, GoalGenerator, ProducerAgent, 安全チェック |
| `test_critic.py` | CriticAgent, ルーブリック読み込み, 動的スコアリング, カスタムルーブリック |
| `test_sandbox.py` | SandboxManager, ファイルI/O, パストラバーサル保護 |
| `test_memory.py` | MemoryStore (SQLite), VectorStore (Qdrant), 埋め込み次元数解決 |
| `test_loop.py` | 統合テスト |
| `test_utils.py` | ユーティリティ関数 |

全30テストが `pytest tests/ -v` で通過することを確認する。

---

## 9. よくある問題と対処

### スコアが 0.00 になる

| 原因 | 確認方法 | 対処 |
|---|---|---|
| LLMが計画テキストを返してループが終了した（旧バグ、修正済み） | `telos show <id>` で `result` を確認 | `had_tool_call` フラグで修正済み |
| Producerがファイルを書き込まなかった | `result` が `TASK_COMPLETE:` で始まっているか確認 | `producer_system.txt` の指示を確認 |
| EvaluationResponse のパースに3回失敗 | ログに `WARNING: Structured chat attempt N failed` があるか | `logging.level: DEBUG` にして詳細確認 |
| Critic が "(file not found)" を評価した | ログに `Could not read artifact` があるか | Sandboxが正常に起動しているか確認 |

### コストが $0.00 になる

OpenRouter経由で使うモデルはlitellmのコスト辞書に登録されていない場合がある。`model_cost_overrides` にモデルIDとコストを追加する。
APIレスポンスの実際のモデルIDは `logging.level: DEBUG` にすると確認できる。

### Qdrant接続エラー

VectorStoreは接続失敗時にサイレントフォールバック（`available=False`）するため、ループ自体は継続する。
`docker-compose up -d qdrant` でQdrantを起動することを推奨。
