# Implementation Plan: monad-template

## Overview

既存の `monad.py` + `pubmed.py` 構成を拡張し、YAML設定・TelosClientモジュール・複数のfetch_source実装例・Process型テンプレート・デプロイファイル一式を追加する。
実装は依存関係の少ないモジュールから順に進め、最後にメインテンプレートへ統合する。

## Tasks

- [x] 1. TelosClientモジュールの実装
  - [x] 1.1 `telos_client.py` を新規作成する
    - `TelosClient` クラスを実装（`__init__`, `search`, `write`）
    - `httpx` を使用し、`TELOS_CORE_URL` 環境変数からベースURLを読み込む
    - 429 → 60秒待機・最大5回リトライのロジックを実装
    - 5xx / その他非2xx → ログ記録して `None` / `[]` を返す（例外を伝播させない）
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 1.2 Property 5のテストを書く（429リトライ）
    - **Property 5: TelosClientは429に対して最大5回リトライする**
    - `pytest-httpx` または `respx` で連続429をモック
    - リトライ回数が5回を超えないこと、各リトライ前に60秒待機することを検証
    - **Validates: Requirements 4.2**

  - [x] 1.3 Property 6のテストを書く（5xxで例外なし）
    - **Property 6: TelosClientは5xxエラーで例外を伝播させない**
    - 500〜599のランダムなステータスコードをHypothesisで生成
    - `write()` が `None` を返し、`search()` が `[]` を返すことを検証
    - **Validates: Requirements 4.3**

  - [x] 1.4 TelosClientのユニットテストを書く
    - 正しいエンドポイント（`/api/v1/search`、`/api/v1/write`）を呼ぶこと
    - `TELOS_CORE_URL` からベースURLが正しく読み込まれること
    - _Requirements: 4.1, 4.4_

- [x] 2. YAML設定ローダーの実装と既存ファイルの更新
  - [x] 2.1 `monad.py` に `load_config()` と設定解決ロジックを追加する
    - `config.yaml` が存在すれば読み込み、なければ空dictを返す `load_config()` を実装
    - 設定値の優先順位: `config.yaml` > 環境変数 > デフォルト値
    - 許可フィールド（`monad_id`, `interval_sec`, `llm_model`, `seed_query`）以外が含まれる場合は `SystemExit(1)`
    - YAML構文エラー時も `SystemExit(1)` + エラーメッセージ出力
    - 既存の `requests` ベースのTelos通信を `TelosClient` に置き換える
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 2.2 Property 1のテストを書く（YAML優先順位）
    - **Property 1: YAML設定値が環境変数より優先される**
    - Hypothesisで各設定キーにランダムな値を生成し、YAML値が常に採用されることを検証
    - **Validates: Requirements 1.2, 3.5**

  - [x] 2.3 Property 2のテストを書く（不正フィールドでエラー）
    - **Property 2: 不正なYAMLフィールドは起動エラーを引き起こす**
    - 許可フィールドセット外のランダム文字列をHypothesisで生成
    - `load_config()` が `SystemExit` を発生させることを検証
    - **Validates: Requirements 1.4**

  - [x] 2.4 設定ローダーのユニットテストを書く
    - `config.yaml` が存在しない場合、環境変数が使われること
    - `config.yaml` が存在する場合、その値が優先されること
    - _Requirements: 1.2, 1.3_

- [x] 3. Checkpoint - ここまでのテストがすべてパスすることを確認する
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. fetch_source実装例（sources/）の作成
  - [x] 4.1 `sources/pubmed.py` を作成する（既存 `pubmed.py` を移動・改善）
    - 既存 `pubmed.py` の内容を `sources/pubmed.py` に移動
    - `requests` を `httpx` に置き換え、エラー時に `None` を返すよう改善
    - _Requirements: 2.1, 2.2_

  - [x] 4.2 `sources/arxiv.py` を作成する
    - ArXiv APIからランダムに論文を取得し `{"summary": str, "raw": str}` を返す
    - `httpx` を使用し、取得失敗時は `None` を返す
    - _Requirements: 2.1, 2.2, 2.4_

  - [x] 4.3 `sources/rss.py` を作成する
    - `feedparser` を使用して任意のRSSフィードURLからエントリを取得
    - フィードURLを定数 `RSS_FEED_URL` で設定する設計
    - タイトルと説明文を `{"summary": str, "raw": str}` 形式で返す
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 4.4 Property 3のテストを書く（fetch_source戻り値型契約）
    - **Property 3: fetch_source()の戻り値型契約**
    - モックHTTPレスポンスをHypothesisでランダム生成
    - 戻り値が `None` か `{"summary": str, "raw": str}` のいずれかであることを検証
    - 3つのsourceすべてに対して実行
    - **Validates: Requirements 2.2**

  - [x] 4.5 fetch_sourceのユニットテストを書く
    - `fetch_source()` が `None` を返した場合、search/writeが呼ばれないこと
    - _Requirements: 2.5_

- [x] 5. Process型テンプレートの実装
  - [x] 5.1 `process_monad.py` を新規作成する
    - `load_config()` を使って設定を読み込む（`monad.py` と共通ロジック）
    - `seed_query` を YAML_Config または `SEED_QUERY` 環境変数から読み込む
    - ループ: `search(seed_query)` → 0件ならスキップ → `think(context)` → `write(output, parent_ids)`
    - `TelosClient` を使用してTelos通信を行う
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [x] 5.2 Property 4のテストを書く（Process型のwrite呼び出し）
    - **Property 4: Process型はsearch結果が存在する場合にwrite()を呼ぶ**
    - ランダムな検索結果リスト（1件以上）をHypothesisで生成
    - `think()` と `write()` が呼ばれることを検証
    - **Validates: Requirements 3.3**

  - [x] 5.3 Process型のユニットテストを書く
    - search結果が0件の場合、think/writeが呼ばれないこと
    - _Requirements: 3.4_

- [x] 6. デプロイファイルの作成
  - [x] 6.1 `Dockerfile` を作成する
    - `python:3.12-slim-bookworm` ベースイメージを使用
    - `requirements.txt` を先にコピーしてレイヤーキャッシュを活用
    - _Requirements: 5.1, 5.4_

  - [x] 6.2 `railway.toml` を作成する
    - `restartPolicyType = "ALWAYS"` を設定
    - _Requirements: 5.2_

  - [x] 6.3 `.env.example` を作成する
    - 必須環境変数（`TELOS_CORE_URL`、`LLM_MODEL`、LLMプロバイダーAPIキー）をコメント付きで列挙
    - _Requirements: 5.3_

  - [x] 6.4 `.dockerignore` を作成する
    - `.venv`・`__pycache__`・`.env` を除外
    - _Requirements: 5.5_

- [x] 7. `requirements.txt` と `config.yaml.example` の更新
  - [x] 7.1 `requirements.txt` を更新する
    - `httpx>=0.27`・`litellm>=1.0`・`pyyaml>=6.0`・`feedparser>=6.0` を含める
    - バージョンは固定せず `>=` 指定のみ使用
    - _Requirements: 7.1, 7.2_

  - [x] 7.2 `config.yaml.example` を作成する
    - `monad_id`・`interval_sec`・`llm_model`・`seed_query` の4フィールドをコメント付きで記載
    - _Requirements: 1.1, 1.5_

- [x] 8. README の更新
  - [x] 8.1 `README.md` を書き直す
    - fetch型とprocess型の2パターンの違いと使い分けを説明
    - ループ構造の図（fetch → search → think → write → sleep）を含める
    - 環境変数の一覧と説明を表形式で記載
    - Railwayへのデプロイ手順をステップ形式で記載
    - vibe coding用プロンプト例を含める
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

- [x] 9. Final Checkpoint - すべてのテストがパスし、ファイル構成が揃っていることを確認する
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- タスクに `*` が付いているものはオプションで、MVPとして先にスキップ可能
- 各タスクは特定のRequirementsを参照しており、トレーサビリティを確保している
- Property テストはHypothesisを使用し、各100回以上のイテレーションで実行する
- `sources/` ディレクトリは `__init__.py` 不要（各ファイルを直接インポートまたはコピーして使う想定）
