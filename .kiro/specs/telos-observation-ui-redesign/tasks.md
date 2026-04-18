# Tasks

## Task List

- [x] 1. Design Token の拡張（globals.css）
  - [x] 1.1 `--accent-amber`, `--accent-cyan`, `--bg-header`, `--bg-statusbar`, `--border-panel-title`, `--stat-value-color`, `--panel-title-height`, `--header-height` を `globals.css` の `:root` に追加する
  - [x] 1.2 既存トークンが維持されていることを確認する

- [x] 2. 用語統一：EventLog → StreamPanel
  - [x] 2.1 `components/EventLog.tsx` を `components/StreamPanel.tsx` にリネームする
  - [x] 2.2 パネルタイトルを "Pheromone Stream" → "Stream" に変更する
  - [x] 2.3 空状態テキストを "Waiting for pheromones..." → "Waiting for stream events..." に変更する
  - [x] 2.4 `ObservationDashboard.tsx` のインポートを `EventLog` → `StreamPanel` に更新する

- [x] 3. StreamPanel の Gotham スタイル改修
  - [x] 3.1 行レイアウトを Gotham スタイルのグリッド表示（monad_id / timestamp / content / linked IDs）に改修する
  - [x] 3.2 新着イベントのフラッシュアニメーションを実装する
  - [x] 3.3 `maxRows` prop（デフォルト 100）による行数上限を実装する

- [x] 4. lib/types.ts の更新
  - [x] 4.1 `StatItem` 型を追加する（`label`, `value`, `intent`）
  - [x] 4.2 `PanelDefinition` 型を追加する（`id`, `title`, `titleColor`, `widthRatio`, `component`）
  - [x] 4.3 `PanelComponentProps` 型を追加する（`events`, `selectedMonads`）
  - [x] 4.4 `StatusBarItem` 型を追加する（`key`, `label`, `value`）

- [x] 5. lib/panelConfig.ts の新規作成
  - [x] 5.1 `PanelDefinition[]` を export する `panelConfig.ts` を作成する
  - [x] 5.2 StreamPanel と MonadPacePanel のエントリを定義する

- [x] 6. PanelRegistry コンポーネントの新規作成
  - [x] 6.1 `components/PanelRegistry.tsx` を作成する
  - [x] 6.2 `PanelDefinition[]` を受け取り、各パネルを動的レンダリングする実装を行う
  - [x] 6.3 `widthRatio` が未指定の場合に flex: 1 をデフォルト適用する
  - [x] 6.4 統一タイトルバースタイル（`--border-panel-title`, `--panel-title-height`）を適用する
  - [x] 6.5 各パネルコンポーネントを `React.memo` でラップする

- [x] 7. StatusBar コンポーネントの新規作成
  - [x] 7.1 `components/StatusBar.tsx` を作成する
  - [x] 7.2 最終イベント受信時刻の表示を実装する
  - [x] 7.3 `streamIssue` に応じたエラーメッセージ表示を実装する
  - [x] 7.4 `extraInfo` prop による追加ステータス情報の表示を実装する
  - [x] 7.5 `--bg-statusbar` デザイントークンを背景色に適用する

- [x] 8. Header コンポーネントの改修
  - [x] 8.1 高さを 40px → 48px（`--header-height`）に変更する
  - [x] 8.2 統計値の色を `--stat-value-color` に変更する
  - [x] 8.3 `extraStats?: StatItem[]` prop を追加し、追加統計を動的レンダリングする
  - [x] 8.4 `streamIssue` に応じた "OFFLINE (CONFIG)" / "OFFLINE (CONNECTION)" 表示を実装する
  - [x] 8.5 Stats API エラー時に Qdrant nodes 値を "!" 表示する

- [x] 9. MonadPacePanel の改修
  - [x] 9.1 各行に `writesPerHour` の最大値に対する相対バーを追加する（CSS width ベース）

- [x] 10. SpaceMap の改修
  - [x] 10.1 Three.js `GridHelper` オーバーレイを追加する
  - [x] 10.2 パーティクルサイズ・輝度を Gotham スタイルに合わせて調整する
  - [x] 10.3 カメラ初期位置を最適化する

- [x] 11. ObservationDashboard の改修
  - [x] 11.1 `PanelRegistry` を導入し、ボトムパネルエリアを置き換える
  - [x] 11.2 `StatusBar` を画面下部に追加する
  - [x] 11.3 `lastEventAt` 状態を管理し `StatusBar` に渡す
  - [x] 11.4 `useMemo` による `filteredEvents`, `paceRows`, `monadIds` のキャッシュを維持・確認する

- [x] 12. filterByMonads のユニットテスト
  - [x] 12.1 空フィルタで全イベントが返ることをテストする（Property 1）
  - [x] 12.2 フィルタ適用後の結果整合性をテストする（Property 2）
  - [x] 12.3 入力配列の不変性をテストする（Property 3）

- [x] 13. computeMonadPace のユニットテスト
  - [x] 13.1 降順ソートをテストする（Property 4）
  - [x] 13.2 monad_id の一意性をテストする（Property 5）
  - [x] 13.3 空文字列 monad_id が "(unknown)" に集計されることをテストする（edge case）
  - [x] 13.4 空配列入力で空配列が返ることをテストする（edge case）

- [x] 14. StreamPanel のユニットテスト
  - [x] 14.1 maxRows 上限のテストを実装する（Property 6）
  - [x] 14.2 イベントフィールド（monad_id, timestamp, content, linked IDs）の表示テストを実装する（Property 7）
  - [x] 14.3 空状態テキスト "Waiting for stream events..." の表示テストを実装する

- [x] 15. PanelRegistry のユニットテスト
  - [x] 15.1 パネル数の一致テストを実装する（Property 8）
  - [x] 15.2 widthRatio の適用テストを実装する（Property 9）
  - [x] 15.3 widthRatio 未指定時のデフォルト flex: 1 テストを実装する
  - [x] 15.4 イベントとフィルタの伝播テストを実装する（Property 10）

- [~] 16. Header / StatusBar のユニットテスト
  - [ ] 16.1 extraStats の全表示テストを実装する（Property 11）
  - [~] 16.2 streamIssue のエラーメッセージ表示テストを実装する（Property 12）
  - [~] 16.3 extraInfo の全表示テストを実装する（Property 13）
