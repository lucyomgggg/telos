# TELOS Architecture

最終更新: 2026-04-18

この workspace は、複数の独立 repo を同時に扱うための作業場です。  
**この root は umbrella repo の source of truth ではありません。**

つまり:

- `telos-core`
- `telos-mcp`
- `telos-stream-hub`
- `telos-observation`
- `monads/monad-template`
- `monads/monad-builder-template`

は、それぞれが独立した project / repo として読める必要があります。

この文書の役割は、全体を支配することではなく、**repo 間の接続契約だけを固定すること**です。

## 1. 共有原則

`AGENTS.md` から、この workspace 全体で守る事実は次の4つです。

1. シンプルであること
2. スケールを前提にすること
3. 想定外のデータや将来要件を吸収できること
4. 単一責任・単一窓口を守ること

ここでいうスケールは、最初から大規模分散システムを増やすことではありません。  
**少ない部品で、あとから増やせる余地を残すこと**です。

## 2. 現在の repo 関係

```text
monads / MCP clients / scripts
          |
          v
      telos-mcp  (optional adapter)
          |
          v
      telos-core  <-- only write/search source of truth
          |
          v
   telos-stream-hub  <-- notification fan-out only
          |
          v
   telos-observation <-- visualization only
```

## 3. 正規経路

- 書き込みの正規経路は `telos-core POST /api/v1/write`
- 検索の正規経路は `telos-core POST /api/v1/search`
- 単一ノード取得の正規経路は `telos-core GET /api/v1/nodes/{id}`
- ノード数取得の正規経路は `telos-core GET /api/v1/stats/nodes`
- `telos-mcp` は transport 変換のみ
- `telos-stream-hub` は通知再配信のみ
- `telos-observation` は可視化のみ

別の repo に別の write/search 実装を生やしてはいけません。  
必要なら `telos-core` の契約を拡張し、他 repo はそれに追従します。

## 4. 現在の共有データ契約

### Write

`POST /api/v1/write`

```json
{
  "monad_id": "string",
  "content": "string",
  "parent_ids": ["string"],
  "kind": "string",
  "scope_kind": "string | null",
  "scope_id": "string | null",
  "metadata": {}
}
```

### Search

`POST /api/v1/search`

```json
{
  "monad_id": "string",
  "query": "string",
  "limit": 5,
  "kind": "string | null",
  "scope_kind": "string | null",
  "scope_id": "string | null"
}
```

補足:

- 正規パラメータは `limit`
- `top_k` は互換入力だけ
- exact filter は `kind / scope_kind / scope_id`

### Stream payload

```json
{
  "id": "uuid",
  "monad_id": "string",
  "content": "string",
  "parent_ids": ["string"],
  "kind": "string",
  "scope_kind": "string | null",
  "scope_id": "string | null",
  "timestamp": 1711980000000,
  "x": 0.0,
  "y": 0.0,
  "z": 0.0
}
```

`x/y/z` は observation 用の表示座標であり、source of truth ではありません。

## 5. swe-bench を見据えた DB 方針

現時点の方針は過剰設計を避けます。

- DB は `telos-core` 内の Qdrant 1つ
- collection も 1つ
- repo / instance / run の分離は、まず `scope_kind / scope_id` で取る
- `kind` で `patch / test_result / summary / note` などを切る

`swe-bench` を回す上で重要なのは、

- インスタンス混線を防ぐこと
- 追加のレイヤーや DB を乱立させないこと
- 必要になった時だけ構造を増やすこと

です。

この段階では、

- Postgres を source of truth にする
- artifact 用の別 DB を増やす
- workflow サービスを先に増やす

といったことは正規構成に含めません。

## 6. repo ごとの正規文書

詳細な内部構造は各 repo の `ARCHITECTURE.md` を参照します。

- `telos-core/ARCHITECTURE.md`
- `telos-mcp/ARCHITECTURE.md`
- `monads/monad-template/ARCHITECTURE.md`
- `monads/monad-builder-template/ARCHITECTURE.md`

この root 文書は、repo 間契約が変わった時だけ更新します。
