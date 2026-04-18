# Requirements Document

## Introduction

telos-observation UI リデザインは、複数の AI Monad がリアルタイムでベクターDBに書き込む様子を可視化する観測 UI の品質向上を目的とする。
主な変更点は (1) "pheromone stream" という内部用語を "stream" に統一、(2) Palantir Gotham を参考にした高品質なオペレーショナル UI の実現、(3) 将来的な情報追加・変更に対応できる拡張性の高い設計、の3点である。

既存スタック（Next.js 14 App Router / BlueprintJS v6 / Three.js R3F / D3.js / Tailwind CSS）を維持しながら実装する。

---

## Glossary

- **Stream**: SSE (Server-Sent Events) 経由で telos-core から受信するリアルタイムイベントの流れ。旧称 "pheromone stream"。
- **StreamEvent**: SSE で受信する個々のイベントオブジェクト。`monad_id`, `content`, `timestamp` 等を含む。
- **StreamPanel**: ストリームイベントのリアルタイムログを表示するパネルコンポーネント。旧称 `EventLog`。
- **Monad**: telos-core にデータを書き込む AI エージェント。`monad_id` で識別される。
- **MonadPacePanel**: Monad ごとの書き込みペース（1時間ウィンドウ）を表示するパネル。
- **PanelRegistry**: ボトムパネルエリアの動的パネル管理コンポーネント。
- **PanelDefinition**: パネルの設定を定義するデータ構造（id, title, component 等）。
- **StatItem**: Header に表示する統計情報の拡張ポイントとなるデータ構造。
- **StatusBar**: 画面下部に接続詳細・タイムスタンプ・システム情報を表示するコンポーネント。
- **ObservationDashboard**: アプリ全体のレイアウトと状態管理を担うルートコンポーネント。
- **SpaceMap**: Three.js/R3F による 3D パーティクルビジュアライゼーションコンポーネント。
- **StreamIssue**: SSE 接続の問題種別を表す型（`"missing_stream_events_url"` | `"stream_connection_error"` | `null`）。
- **Design_Token**: CSS カスタムプロパティとして定義されるデザイン変数（色・サイズ等）。
- **filterByMonads**: 選択された Monad ID に基づいてイベントをフィルタリングする関数。
- **computeMonadPace**: イベント配列から Monad ごとの書き込みペースを計算する関数。

---

## Requirements

### Requirement 1: 用語の統一（"pheromone" → "stream"）

**User Story:** As a developer, I want all UI text and component names to use "stream" instead of "pheromone stream", so that the codebase uses consistent, non-internal terminology.

#### Acceptance Criteria

1. THE StreamPanel SHALL display the panel title as "Stream" (旧: "Pheromone Stream")
2. WHEN no stream events have been received, THE StreamPanel SHALL display the text "Waiting for stream events..." (旧: "Waiting for pheromones...")
3. THE StreamPanel component SHALL be implemented in a file named `StreamPanel.tsx` (旧: `EventLog.tsx`)
4. THE ObservationDashboard SHALL reference StreamPanel instead of EventLog in all imports and usages

---

### Requirement 2: Header コンポーネントの改修

**User Story:** As an operator, I want the header to display connection status, statistics, and monad filters clearly, so that I can monitor the system state at a glance.

#### Acceptance Criteria

1. THE Header SHALL display a connection status indicator showing either "LIVE" (connected) or "OFFLINE" (disconnected)
2. WHEN the stream connection fails due to missing configuration, THE Header SHALL display "OFFLINE (CONFIG)"
3. WHEN the stream connection fails due to a connection error, THE Header SHALL display "OFFLINE (CONNECTION)"
4. THE Header SHALL display the number of active Qdrant nodes
5. WHEN the stats API returns an error, THE Header SHALL display "!" for the Qdrant nodes value
6. THE Header SHALL display the total number of active Monads
7. THE Header SHALL display the total write count within the last 1 hour
8. THE Header SHALL provide a MultiSelect control for filtering events by Monad ID
9. WHEN the `extraStats` prop is provided, THE Header SHALL render each StatItem as an additional statistic
10. THE Header SHALL have a height of 48px
11. THE Header SHALL apply the `--stat-value-color` design token (`#cdd9e5`) to statistic values

---

### Requirement 3: StreamPanel コンポーネントの実装

**User Story:** As an operator, I want to see a real-time log of stream events, so that I can monitor what data is being written to the vector DB.

#### Acceptance Criteria

1. WHEN stream events are received, THE StreamPanel SHALL render each event as a row displaying monad_id, timestamp, content, and linked IDs
2. WHEN a new event arrives, THE StreamPanel SHALL apply a flash animation to the new row
3. WHEN no events have been received, THE StreamPanel SHALL display an empty state message "Waiting for stream events..."
4. THE StreamPanel SHALL limit displayed rows to a maximum of `maxRows` (default: 100)
5. THE StreamPanel SHALL render event rows using a Gotham-style grid layout

---

### Requirement 4: SpaceMap コンポーネントの改修

**User Story:** As an operator, I want the 3D visualization to have a precise, operational aesthetic, so that it matches the Palantir Gotham design language.

#### Acceptance Criteria

1. THE SpaceMap SHALL render a Three.js GridHelper overlay on the 3D scene
2. THE SpaceMap SHALL apply adjusted particle size and brightness settings consistent with the Gotham design aesthetic
3. THE SpaceMap SHALL initialize the camera at an optimized position for viewing the particle field

---

### Requirement 5: MonadPacePanel コンポーネントの改修

**User Story:** As an operator, I want to see each Monad's write pace visualized with a bar chart, so that I can quickly compare activity levels across Monads.

#### Acceptance Criteria

1. THE MonadPacePanel SHALL display a relative bar for each Monad row, where bar width is proportional to the Monad's `writesPerHour` relative to the maximum value
2. THE MonadPacePanel SHALL render bars using D3.js or CSS width-based relative sizing

---

### Requirement 6: StatusBar コンポーネントの新規追加

**User Story:** As an operator, I want a status bar at the bottom of the screen showing connection details and timestamps, so that I can quickly diagnose issues without navigating away.

#### Acceptance Criteria

1. THE StatusBar SHALL display the timestamp of the last received stream event
2. WHEN `streamIssue` is non-null, THE StatusBar SHALL display a descriptive error message corresponding to the issue type
3. WHEN the `extraInfo` prop is provided, THE StatusBar SHALL render each StatusBarItem as additional status information
4. THE StatusBar SHALL be positioned at the bottom of the ObservationDashboard layout
5. THE StatusBar SHALL apply the `--bg-statusbar` design token (`#060a0e`) as its background color

---

### Requirement 7: PanelRegistry コンポーネントの新規追加

**User Story:** As a developer, I want a panel registry that dynamically renders bottom panels from a configuration, so that I can add new panels without modifying the dashboard layout code.

#### Acceptance Criteria

1. THE PanelRegistry SHALL render one panel container for each entry in the `panels: PanelDefinition[]` prop
2. WHEN a `PanelDefinition` has a `widthRatio` value, THE PanelRegistry SHALL apply that value as the flex ratio of the panel container
3. WHEN a `PanelDefinition` does not have a `widthRatio` value, THE PanelRegistry SHALL default the flex ratio to 1
4. THE PanelRegistry SHALL apply a unified title bar style (border, height, color) to all panels
5. THE PanelRegistry SHALL pass filtered events and selectedMonads to each panel component
6. WHEN a new PanelDefinition is added to `panelConfig.ts`, THE PanelRegistry SHALL render the new panel without requiring changes to ObservationDashboard

---

### Requirement 8: Design Token の拡張（Gotham スタイル）

**User Story:** As a developer, I want a comprehensive set of design tokens aligned with the Palantir Gotham visual language, so that all components share a consistent look and feel.

#### Acceptance Criteria

1. THE Design_Token set SHALL include `--accent-amber: #f0a500` for warning and caution states
2. THE Design_Token set SHALL include `--accent-cyan: #00b4d8` as a highlight accent color
3. THE Design_Token set SHALL include `--bg-header: #060a0e` for the header background
4. THE Design_Token set SHALL include `--bg-statusbar: #060a0e` for the status bar background
5. THE Design_Token set SHALL include `--border-panel-title: #1e2d3d` for panel title borders
6. THE Design_Token set SHALL include `--stat-value-color: #cdd9e5` for statistic value text
7. THE Design_Token set SHALL include `--panel-title-height: 28px` for panel title bar height
8. THE Design_Token set SHALL include `--header-height: 48px` for the header height
9. THE Design_Token set SHALL preserve all existing tokens (`--bg-base`, `--bg-panel`, `--bg-panel-hover`, `--border-subtle`, `--border-active`, `--accent-green`, `--accent-blue`, `--accent-red`, `--text-primary`, `--text-secondary`, `--text-muted`)

---

### Requirement 9: filterByMonads 関数の仕様

**User Story:** As a developer, I want a reliable filterByMonads function, so that panel components receive only the events relevant to the selected Monads.

#### Acceptance Criteria

1. WHEN `selectedMonads` is an empty array, THE filterByMonads function SHALL return all events unchanged
2. WHEN `selectedMonads` is non-empty, THE filterByMonads function SHALL return only events whose `monad_id` is included in `selectedMonads`
3. THE filterByMonads function SHALL not mutate the input `events` array

---

### Requirement 10: computeMonadPace 関数の仕様

**User Story:** As a developer, I want a reliable computeMonadPace function, so that MonadPacePanel always displays accurate and consistently sorted data.

#### Acceptance Criteria

1. THE computeMonadPace function SHALL return results sorted in descending order by `writesPerHour`
2. THE computeMonadPace function SHALL include each `monad_id` at most once in the result
3. WHEN an event has an empty string `monad_id`, THE computeMonadPace function SHALL aggregate it under the key `"(unknown)"`
4. WHEN the input `events` array is empty, THE computeMonadPace function SHALL return an empty array

---

### Requirement 11: エラーハンドリング

**User Story:** As an operator, I want the UI to clearly communicate connection and configuration errors, so that I can quickly identify and resolve issues.

#### Acceptance Criteria

1. WHEN `NEXT_PUBLIC_TELOS_STREAM_EVENTS_URL` is not set, THE ObservationDashboard SHALL set `streamIssue` to `"missing_stream_events_url"`
2. WHEN the EventSource fires an `onerror` event, THE ObservationDashboard SHALL set `streamIssue` to `"stream_connection_error"`
3. WHEN the `/stats/nodes` fetch fails, THE ObservationDashboard SHALL set `nodeStatsStatus` to `"error"`
4. WHEN `streamIssue` is `"stream_connection_error"`, THE ObservationDashboard SHALL allow the browser's built-in EventSource reconnection to proceed automatically

---

### Requirement 12: パフォーマンス最適化

**User Story:** As an operator, I want the UI to remain responsive even as stream events accumulate, so that I can monitor the system without performance degradation.

#### Acceptance Criteria

1. THE StreamPanel SHALL retain at most 100 stream events in the displayed list (MAX_ROWS = 100)
2. THE PanelRegistry SHALL wrap each panel component with `React.memo` to prevent unnecessary re-renders
3. THE ObservationDashboard SHALL use `useMemo` to cache computed values for `filteredEvents`, `paceRows`, and `monadIds`
