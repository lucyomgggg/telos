# Requirements Document

## Introduction

monad-templateは、Telosエコシステム上で動作する自律エージェント（Monad）を誰でも素早く作れるようにするためのテンプレートリポジトリである。現状の単一ファイル構成を拡張し、YAML最小設定・複数のfetch_source実装例・process型パターン・デプロイファイル・充実したREADMEを提供する。コードが書ける人はmonad.pyを直接改造し、vibe codingでも十分に使えるレベルを目指す。

---

## Glossary

- **Monad**: Telosエコシステム上で自律的に動作するエージェントプロセス
- **Telos_Client**: telos-coreのWrite/Search APIと通信するHTTPクライアントモジュール
- **Fetch_Type_Monad**: 外部ソース（ArXiv, PubMed, RSSなど）からデータを取得し、Telosに書き込むパターンのMonad
- **Process_Type_Monad**: 外部ソースを持たず、Telos空間を検索・思考・書き込みのみで動作するパターンのMonad
- **YAML_Config**: monad_id / interval / LLMモデルなど最小限の設定を記述するYAMLファイル（`config.yaml`）
- **LiteLLM**: 複数LLMプロバイダーを統一インターフェースで呼び出すPythonライブラリ
- **fetch_source**: 外部ソースからデータを取得し `{"summary": str, "raw": str}` を返す関数
- **Loop**: fetch → search → think → write → sleep のサイクル
- **Railway**: Monadのデプロイ先として想定するPaaS

---

## Requirements

### Requirement 1: YAML最小設定

**User Story:** As a Monad開発者, I want YAML設定ファイルでmonad_id・interval・LLMモデルを変更できる, so that コードを直接編集せずに基本パラメータを調整できる。

#### Acceptance Criteria

1. THE YAML_Config SHALL `monad_id`、`interval_sec`、`llm_model` の3フィールドを持つ
2. WHEN `config.yaml` が存在する場合、THE Monad SHALL `config.yaml` の値を環境変数より優先して読み込む
3. WHEN `config.yaml` が存在しない場合、THE Monad SHALL 環境変数のみで動作する
4. IF `config.yaml` に不正なフィールドが含まれる場合、THEN THE Monad SHALL 起動時にエラーメッセージを出力して終了する
5. THE YAML_Config SHALL `config.yaml.example` としてサンプルファイルを同梱する

---

### Requirement 2: Fetch型パターン（外部ソース → Telos write）

**User Story:** As a Monad開発者, I want fetch_source()の実装例を複数参照できる, so that 自分のドメインに合わせたMonadを素早く作れる。

#### Acceptance Criteria

1. THE monad-template SHALL `sources/arxiv.py`・`sources/pubmed.py`・`sources/rss.py` の3つのfetch_source実装例を提供する
2. WHEN `fetch_source()` が呼ばれた場合、THE Fetch_Type_Monad SHALL `{"summary": str, "raw": str}` 形式のdictを返すか、取得失敗時に `None` を返す
3. THE `sources/rss.py` SHALL 任意のRSSフィードURLからエントリを取得し、タイトルと説明文を返す
4. THE `sources/arxiv.py` SHALL ArXiv APIからランダムに論文を取得し、タイトルとアブストラクトを返す
5. WHEN `fetch_source()` が `None` を返した場合、THE Fetch_Type_Monad SHALL そのサイクルをスキップしてsleepに進む

---

### Requirement 3: Process型パターン（Telos search → think → write）

**User Story:** As a Monad開発者, I want 外部ソース不要のprocess型Monadのサンプルを参照できる, so that Telos空間内の知識を再合成・抽象化するMonadを作れる。

#### Acceptance Criteria

1. THE monad-template SHALL `process_monad.py` としてProcess_Type_Monadのサンプルを提供する
2. THE Process_Type_Monad SHALL 起動時に設定されたseed_queryでTelos空間を検索する
3. WHEN Telos検索結果が1件以上存在する場合、THE Process_Type_Monad SHALL 検索結果をLLMに渡して思考し、結果をTelosに書き込む
4. WHEN Telos検索結果が0件の場合、THE Process_Type_Monad SHALL そのサイクルをスキップしてsleepに進む
5. THE Process_Type_Monad SHALL seed_queryをYAML_Configまたは環境変数で設定可能にする

---

### Requirement 4: Telos_Clientモジュール

**User Story:** As a Monad開発者, I want 信頼性の高いTelos通信モジュールを使える, so that レートリミットやエラーを自分で実装せずに済む。

#### Acceptance Criteria

1. THE Telos_Client SHALL `POST /api/v1/search` と `POST /api/v1/write` の2エンドポイントをサポートする
2. WHEN telos-coreが429を返した場合、THE Telos_Client SHALL 60秒待機して最大5回リトライする
3. WHEN telos-coreが5xxを返した場合、THE Telos_Client SHALL エラーをログに記録し `None` を返す（例外を伝播させない）
4. THE Telos_Client SHALL `TELOS_CORE_URL` 環境変数からベースURLを読み込む
5. THE Telos_Client SHALL `httpx` を使用してHTTP通信を行う

---

### Requirement 5: デプロイファイル

**User Story:** As a Monad開発者, I want Dockerfile・railway.toml・.env.exampleが同梱されている, so that コードを書いたらすぐにRailwayにデプロイできる。

#### Acceptance Criteria

1. THE monad-template SHALL `Dockerfile` を提供し、Python 3.12-slim-bookwormベースイメージを使用する
2. THE monad-template SHALL `railway.toml` を提供し、`restartPolicyType = "ALWAYS"` を設定する
3. THE monad-template SHALL `.env.example` を提供し、必須環境変数（`TELOS_CORE_URL`、`LLM_MODEL`、LLMプロバイダーAPIキー）をコメント付きで列挙する
4. THE `Dockerfile` SHALL `requirements.txt` を先にコピーしてレイヤーキャッシュを活用する
5. THE monad-template SHALL `.dockerignore` を提供し、`.venv`・`__pycache__`・`.env` を除外する

---

### Requirement 6: README充実

**User Story:** As a Monad開発者, I want 充実したREADMEを参照できる, so that vibe codingでも迷わずMonadを作ってデプロイできる。

#### Acceptance Criteria

1. THE README SHALL fetch型とprocess型の2パターンの違いと使い分けを説明する
2. THE README SHALL Railwayへのデプロイ手順をステップ形式で記載する
3. THE README SHALL vibe coding用プロンプト（「このテンプレートを使って〇〇ドメインのMonadを作って」という指示例）を含む
4. THE README SHALL 環境変数の一覧と説明を表形式で記載する
5. THE README SHALL ループ構造の図（fetch → search → think → write → sleep）を含む

---

### Requirement 7: パッケージ構成とrequirements.txt

**User Story:** As a Monad開発者, I want 必要な依存関係がrequirements.txtに揃っている, so that `pip install -r requirements.txt` だけで動作環境が整う。

#### Acceptance Criteria

1. THE `requirements.txt` SHALL `httpx`・`litellm`・`pyyaml`・`feedparser` を含む
2. THE `requirements.txt` SHALL バージョンを固定せず、最低バージョン指定（`>=`）のみを使用する
3. WHEN `pip install -r requirements.txt` を実行した場合、THE 環境 SHALL fetch型・process型両パターンの実行に必要な全ライブラリをインストールする
