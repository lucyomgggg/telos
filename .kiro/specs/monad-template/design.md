# Design Document: monad-template

## Overview

monad-templateは、Telosエコシステム上で動作する自律エージェント（Monad）を誰でも素早く作れるようにするテンプレートリポジトリである。

現状の単一ファイル構成（`monad.py` + `pubmed.py`）を拡張し、以下を提供する：

- **YAML最小設定**：コードを触らずに基本パラメータを変更できる
- **2パターンのテンプレート**：Fetch型（外部ソース → Telos write）とProcess型（Telos search → think → write）
- **複数のfetch_source実装例**：ArXiv / PubMed / RSS
- **信頼性の高いTelosClientモジュール**：リトライ・レートリミット対応
- **デプロイファイル一式**：Dockerfile / railway.toml / .env.example

設計の核心は「CONFIGセクションだけ変えれば動く」という既存コンセプトを維持しつつ、YAML設定・モジュール分割・デプロイ対応を追加することである。

---

## Architecture

### 全体構成

```
monads/monad-template/
  monad.py              ← Fetch型メインテンプレート
  process_monad.py      ← Process型テンプレート
  config.yaml.example   ← YAML設定サンプル
  telos_client.py       ← Telosクライアントモジュール
  sources/
    arxiv.py            ← ArXiv fetch_source実装例
    pubmed.py           ← PubMed fetch_source実装例（既存を移動）
    rss.py              ← RSS fetch_source実装例
  Dockerfile
  railway.toml
  .env.example
  .dockerignore
  requirements.txt
  README.md
```

### 2パターンのループ構造

**Fetch型（monad.py）**

```
fetch_source()
    ↓ None → skip → sleep
search(summary)
    ↓
think(source + context)
    ↓
write(output, parent_ids)
    ↓
sleep(INTERVAL_SEC)
```

**Process型（process_monad.py）**

```
search(seed_query)
    ↓ 0件 → skip → sleep
think(context)
    ↓
write(output, parent_ids)
    ↓
sleep(INTERVAL_SEC)
```

### 設定の優先順位

```
config.yaml（存在する場合）> 環境変数 > デフォルト値
```

---

## Components and Interfaces

### 1. 設定ローダー（config loading logic）

`monad.py` および `process_monad.py` の冒頭で共通のロジックを使用する。

```python
def load_config() -> dict:
    """config.yamlが存在すれば読み込み、なければ空dictを返す。"""
    ...

# 設定値の解決（YAML > 環境変数 > デフォルト）
cfg = load_config()
MONAD_ID    = cfg.get("monad_id",    os.environ.get("MONAD_ID",    "monad-template"))
INTERVAL_SEC = cfg.get("interval_sec", int(os.environ.get("INTERVAL_SEC", 180)))
LLM_MODEL   = cfg.get("llm_model",   os.environ.get("LLM_MODEL",   "openai/gpt-4o-mini"))
```

不正なフィールドが含まれる場合は起動時に `SystemExit` を発生させる。

### 2. TelosClient（telos_client.py）

monad1の `telos_client.py` をベースに、`Settings` への依存を除去してシンプルな初期化に変更する。

```python
class TelosClient:
    def __init__(self, base_url: str, monad_id: str, timeout: float = 30.0) -> None:
        ...

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """
        POST /api/v1/search
        Returns: list of {"id": str, "content": str, "score": float, ...}
        On error: returns []
        """
        ...

    def write(self, content: str, parent_ids: list[str] = []) -> str | None:
        """
        POST /api/v1/write
        Returns: node id string on success, None on failure
        """
        ...
```

**エラーハンドリング方針**：
- 429 → 60秒待機、最大5回リトライ
- 5xx → ログ記録、`None` / `[]` を返す（例外を伝播させない）
- その他非2xx → ログ記録、`None` / `[]` を返す

### 3. fetch_source実装例（sources/）

各ファイルは同一インターフェースを実装する：

```python
def fetch_source() -> dict | None:
    """
    Returns: {"summary": str, "raw": str} or None
    """
```

| ファイル | ソース | 取得内容 |
|---|---|---|
| `sources/arxiv.py` | ArXiv API | ランダム論文のタイトル＋アブストラクト |
| `sources/pubmed.py` | PubMed eUtils | ランダム論文のタイトル＋アブストラクト |
| `sources/rss.py` | 任意のRSSフィード | エントリのタイトル＋説明文 |

`sources/rss.py` はフィードURLを定数 `RSS_FEED_URL` で設定する設計とし、`feedparser` ライブラリを使用する。

### 4. LLM呼び出し（think関数）

`litellm.completion` を使用。`LLM_MODEL` は LiteLLM のモデルID形式（例: `openai/gpt-4o-mini`）。

```python
def think(user_prompt: str) -> str:
    res = completion(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": PERSONA},
            {"role": "user",   "content": user_prompt},
        ],
    )
    return res.choices[0].message.content.strip()
```

---

## Data Models

### YAML設定スキーマ

```yaml
# config.yaml
monad_id: monad-my-domain        # Telos上の一意なエージェントID
interval_sec: 180                # ループ間隔（秒）
llm_model: openai/gpt-4o-mini   # LiteLLM形式のモデルID
seed_query: "feedback between scales"  # process型のみ使用
```

**バリデーションルール**：
- 許可フィールド: `monad_id`, `interval_sec`, `llm_model`, `seed_query`
- 不明なフィールドが存在する場合 → 起動時エラー
- `interval_sec` は整数であること
- `seed_query` はfetch型では無視される（警告なし）

### fetch_source戻り値

```python
{
    "summary": str,  # Telos検索クエリに使用する短い要約
    "raw": str,      # LLMプロンプトに渡す詳細テキスト
}
```

### TelosClient.search戻り値

```python
[
    {
        "id": str,
        "monad_id": str,
        "content": str,
        "score": float,
        "timestamp": str | None,
    },
    ...
]
```

### 環境変数一覧

| 変数名 | 必須 | デフォルト | 説明 |
|---|---|---|---|
| `TELOS_CORE_URL` | ✓ | - | telos-coreのベースURL |
| `MONAD_ID` | - | `monad-template` | エージェントID |
| `INTERVAL_SEC` | - | `180` | ループ間隔（秒） |
| `LLM_MODEL` | - | `openai/gpt-4o-mini` | LiteLLMモデルID |
| `OPENAI_API_KEY` | △ | - | OpenAI使用時 |
| `ANTHROPIC_API_KEY` | △ | - | Anthropic使用時 |
| `SEED_QUERY` | - | - | Process型のシードクエリ |

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*


### Property 1: YAML設定値が環境変数より優先される

*For any* 設定キー（`monad_id`、`interval_sec`、`llm_model`、`seed_query`）について、`config.yaml` に値が存在し、かつ同名の環境変数も設定されている場合、`load_config()` は常に `config.yaml` の値を返す。

**Validates: Requirements 1.2, 3.5**

### Property 2: 不正なYAMLフィールドは起動エラーを引き起こす

*For any* 文字列 `s` が許可フィールドセット（`monad_id`、`interval_sec`、`llm_model`、`seed_query`）に含まれない場合、`s` をキーとして含む `config.yaml` を読み込むと `load_config()` はエラーを発生させる。

**Validates: Requirements 1.4**

### Property 3: fetch_source()の戻り値型契約

*For any* `fetch_source()` の呼び出し（HTTPレスポンスをモック）において、戻り値は `None` であるか、または `"summary"` と `"raw"` の両キーを持ち、それぞれの値が文字列である dict のいずれかである。

**Validates: Requirements 2.2**

### Property 4: Process型はsearch結果が存在する場合にwrite()を呼ぶ

*For any* 1件以上の検索結果リストに対して、Process型Monadのループは `think()` を呼び出し、その出力を `write()` に渡す。

**Validates: Requirements 3.3**

### Property 5: TelosClientは429に対して最大5回リトライする

*For any* TelosClientのリクエスト（searchまたはwrite）において、telos-coreが連続して429を返す場合、クライアントは最大5回リトライし、各リトライ前に60秒待機する。

**Validates: Requirements 4.2**

### Property 6: TelosClientは5xxエラーで例外を伝播させない

*For any* HTTP 5xxステータスコード（500〜599）に対して、`TelosClient.write()` は `None` を返し、`TelosClient.search()` は `[]` を返す。いずれも例外を呼び出し元に伝播させない。

**Validates: Requirements 4.3**

---

## Error Handling

### 設定ローダー

| エラー条件 | 挙動 |
|---|---|
| `config.yaml` に不正フィールド | `SystemExit(1)` + エラーメッセージ出力 |
| `config.yaml` のYAML構文エラー | `SystemExit(1)` + エラーメッセージ出力 |
| `TELOS_CORE_URL` 未設定 | `KeyError` → 起動時に即座に失敗 |

### fetch_source()

| エラー条件 | 挙動 |
|---|---|
| HTTPタイムアウト / 接続エラー | `None` を返す（ループはスキップ） |
| APIレスポンスが期待形式でない | `None` を返す |
| `fetch_source()` が `None` を返す | ループをスキップして `sleep(INTERVAL_SEC)` |

### TelosClient

| エラー条件 | 挙動 |
|---|---|
| 429 (rate limit) | 60秒待機、最大5回リトライ |
| 5xx (server error) | ログ記録、`None` / `[]` を返す |
| 413 (content too large) | ログ記録、`None` を返す |
| その他非2xx | ログ記録、`None` / `[]` を返す |
| ネットワークエラー | ログ記録、`None` / `[]` を返す |

### メインループ

ループ全体を `try/except Exception` で囲み、予期しないエラーが発生してもプロセスが終了しないようにする。エラーはログに記録し、`sleep(INTERVAL_SEC)` 後に次のサイクルへ進む。

---

## Testing Strategy

### PBT適用判断

このfeatureはPBTが適用可能である。設定ローダー・TelosClientのエラーハンドリング・fetch_source()の型契約など、入力の変化によって挙動が変わる純粋なロジックが複数存在する。

### テストライブラリ

- **Property-based testing**: [Hypothesis](https://hypothesis.readthedocs.io/) (Python)
- **Unit testing**: pytest
- **HTTP mocking**: `pytest-httpx` または `respx`

### プロパティテスト（各100回以上のイテレーション）

各プロパティテストには以下のタグコメントを付与する：
`# Feature: monad-template, Property {N}: {property_text}`

| Property | テスト内容 | 生成戦略 |
|---|---|---|
| Property 1 | YAML vs 環境変数の優先順位 | 各設定キーに対してランダムな文字列値を生成 |
| Property 2 | 不正フィールドでエラー | 許可フィールドセット外のランダム文字列を生成 |
| Property 3 | fetch_source()戻り値型 | モックHTTPレスポンスのランダム生成 |
| Property 4 | Process型のwrite呼び出し | ランダムな検索結果リスト（1件以上）を生成 |
| Property 5 | 429リトライ回数 | 連続429レスポンスをモック |
| Property 6 | 5xxで例外なし | 500〜599のランダムなステータスコードを生成 |

### ユニットテスト（具体例・エッジケース）

- `config.yaml` が存在しない場合、環境変数が使われること
- `fetch_source()` が `None` を返した場合、search/writeが呼ばれないこと
- Process型でsearch結果が0件の場合、think/writeが呼ばれないこと
- `TelosClient` が正しいエンドポイント（`/api/v1/search`、`/api/v1/write`）を呼ぶこと
- `TELOS_CORE_URL` からベースURLが正しく読み込まれること

### スモークテスト（ファイル存在確認）

- `sources/arxiv.py`、`sources/pubmed.py`、`sources/rss.py` が存在すること
- `process_monad.py` が存在すること
- `config.yaml.example` が存在すること
- `Dockerfile`、`railway.toml`、`.env.example`、`.dockerignore` が存在すること
- `requirements.txt` に `httpx`、`litellm`、`pyyaml`、`feedparser` が含まれること
