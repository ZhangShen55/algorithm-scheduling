# Algorithm Scheduling Architecture Review HTML Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Chinese architecture-review HTML that explains the problems in the original algorithm scheduling diagram and presents the corrected offline, online-image, and realtime-ASR architecture.

**Architecture:** Use one dependency-free HTML file with embedded CSS and JavaScript. A fixed-format dotted architecture canvas contains semantic HTML nodes and an SVG connector layer; view filters, zoom controls, component details, and print support operate entirely in the browser. A Node built-in test verifies required content, interaction hooks, and the absence of external runtime dependencies.

**Tech Stack:** HTML5, CSS3, inline JavaScript, SVG connectors, Node.js built-in `node:test`, in-app browser verification.

---

## File Structure

- Create `algorithm-scheduling-architecture-review.html`: complete standalone review page, architecture canvas, styles, interactions, component metadata, and print layout.
- Create `tests/architecture-review.test.mjs`: dependency-free contract tests for required architecture content and interaction hooks.
- Keep `docs/superpowers/specs/2026-07-27-algorithm-scheduling-review-html-design.md` as the approved source of truth.

The workspace root is not a Git repository, so this plan intentionally omits commit steps.

### Task 1: Add the HTML Contract Test

**Files:**
- Create: `tests/architecture-review.test.mjs`
- Test: `tests/architecture-review.test.mjs`

- [ ] **Step 1: Create a test that reads the standalone HTML**

```javascript
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const currentDir = dirname(fileURLToPath(import.meta.url));
const htmlPath = resolve(currentDir, "../algorithm-scheduling-architecture-review.html");
const html = readFileSync(htmlPath, "utf8");

test("is a standalone Chinese architecture review", () => {
  assert.match(html, /算法调度平台架构评审/);
  assert.doesNotMatch(html, /<script[^>]+src=/i);
  assert.doesNotMatch(html, /<link[^>]+href=["']https?:/i);
  assert.doesNotMatch(html, /url\(["']?https?:/i);
});
```

- [ ] **Step 2: Add architecture-content assertions**

```javascript
test("contains every required execution path", () => {
  for (const layer of ["control", "offline", "online", "realtime", "registry", "resource"]) {
    assert.match(html, new RegExp(`data-layer=["']${layer}["']`));
  }

  for (const node of [
    "task-ingress",
    "outbox",
    "kafka",
    "orchestrator",
    "task-db",
    "registry",
    "media-worker",
    "offline-executor",
    "ppt-callback",
    "result-writer",
    "sync-gateway",
    "realtime-gateway",
  ]) {
    assert.match(html, new RegExp(`data-node=["']${node}["']`));
  }

  assert.match(html, /ppt_image_id/);
  assert.match(html, /\/v1\/extract_keywords/);
  assert.match(html, /\/v1\/course_overviews/);
  assert.match(html, /\/AE\/SyncTasks2/);
  assert.match(html, /\/detect_all/);
  assert.match(html, /\/recognize\/batch/);
});
```

- [ ] **Step 3: Add interaction and response-envelope assertions**

```javascript
test("contains review controls and the agreed response envelope", () => {
  for (const filter of ["all", "control", "offline", "online", "realtime"]) {
    assert.match(html, new RegExp(`data-filter=["']${filter}["']`));
  }

  for (const action of ["zoom-in", "zoom-out", "reset", "print"]) {
    assert.match(html, new RegExp(`data-action=["']${action}["']`));
  }

  assert.match(html, /request_id/);
  assert.match(html, /X-Operator-Instance/);
  assert.match(html, /HTTP 429/);
  assert.match(html, /HTTP 503/);
  assert.match(html, /HTTP 504/);
  assert.match(html, /id=["']component-detail["']/);
});
```

- [ ] **Step 4: Run the test and verify it fails because the HTML does not exist**

Run:

```bash
node --test tests/architecture-review.test.mjs
```

Expected: FAIL with `ENOENT` for `algorithm-scheduling-architecture-review.html`.

### Task 2: Build the Page Shell and Review Summary

**Files:**
- Create: `algorithm-scheduling-architecture-review.html`
- Test: `tests/architecture-review.test.mjs`

- [ ] **Step 1: Create semantic page sections**

The file must contain this top-level structure:

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>算法调度平台架构评审</title>
  <style>/* all page, diagram, responsive, and print CSS */</style>
</head>
<body>
  <header class="page-header"></header>
  <main>
    <section class="review-band" aria-labelledby="review-title"></section>
    <section class="architecture-section" aria-labelledby="architecture-title"></section>
    <section class="contract-section" aria-labelledby="contract-title"></section>
    <section class="state-section" aria-labelledby="state-title"></section>
  </main>
  <footer></footer>
  <script>/* all interactions and component metadata */</script>
</body>
</html>
```

- [ ] **Step 2: Render the seven approved findings**

Use an ordered list with severity labels and these exact subjects:

```html
<ol class="finding-list">
  <li><strong>媒体处理缺口</strong><span>缺少视频下载、WAV 提取和按策略抽帧。</span></li>
  <li><strong>在线链路混用</strong><span>实时 ASR 会话与同步图片请求需要分离。</span></li>
  <li><strong>重复调度服务</strong><span>每种算法一个调度器会重复实现路由与重试。</span></li>
  <li><strong>落库职责耦合</strong><span>适配器不应分别维护业务数据库协议。</span></li>
  <li><strong>文本节点缺失</strong><span>需要课程脑图与单张 PPT 关键词节点。</span></li>
  <li><strong>DB 与 Kafka 双写</strong><span>任务接入需要事务 Outbox。</span></li>
  <li><strong>GPU 拓扑误导</strong><span>不应假定每张 GPU 部署全部算子。</span></li>
</ol>
```

- [ ] **Step 3: Add the legend and restrained color tokens**

Define CSS custom properties without gradients:

```css
:root {
  --ink: #17202a;
  --muted: #5c6773;
  --line: #263238;
  --paper: #f8fafb;
  --control: #51b9e6;
  --adapter: #e93f92;
  --compute: #f59e0b;
  --data: #55c9ad;
  --business: #7c3aed;
  --risk: #e84c3d;
  --panel: #ffffff;
  --border: #d8e0e6;
}
```

The diagram background must use a repeating radial dot pattern and every component must use `border-radius: 6px` or less.

- [ ] **Step 4: Run the contract test**

Run:

```bash
node --test tests/architecture-review.test.mjs
```

Expected: tests run but architecture-content assertions still fail until Task 3.

### Task 3: Build the Improved Architecture Canvas

**Files:**
- Modify: `algorithm-scheduling-architecture-review.html`
- Test: `tests/architecture-review.test.mjs`

- [ ] **Step 1: Add stable canvas and connector layers**

Use this required frame:

```html
<div class="diagram-viewport" id="diagram-viewport">
  <div class="diagram" id="diagram" style="--diagram-scale: 1">
    <svg class="connectors" aria-hidden="true" viewBox="0 0 1680 1180"></svg>
    <div class="diagram-nodes"></div>
  </div>
</div>
```

Set `.diagram` to `width: 1680px; height: 1180px; transform: scale(var(--diagram-scale)); transform-origin: top left;` and update viewport compensation in JavaScript so zooming does not collapse scrollable dimensions.

- [ ] **Step 2: Add control-plane nodes and edges**

Create clickable nodes with `data-layer="control"` and these identifiers:

```html
<button class="node node-control" data-node="upstream-a">A 上游服务</button>
<button class="node node-control" data-node="task-ingress">任务接入服务</button>
<button class="node node-data" data-node="task-db">平台任务数据库</button>
<button class="node node-control" data-node="outbox">Outbox 发布器</button>
<button class="node node-control" data-node="kafka">Kafka 任务队列</button>
<button class="node node-control" data-node="orchestrator">课程 DAG 编排器</button>
<button class="node node-control" data-node="ops-view">运维查询与任务操作</button>
```

The connectors must show `A -> task-ingress -> task-db`, `task-db -> outbox -> kafka -> orchestrator`, and `A <-> ops-view <-> task-db`.

- [ ] **Step 3: Add registry and instance-aware routing**

Create `data-layer="registry"` nodes for the registry and Redis TTL state. Connect operator instances to the registry with `注册 / 心跳`, and connect both offline and online executors to the registry with `查询 + 并发占位`.

The registry detail must list:

```text
instance_id · operator_code · service_url
status · max_concurrency · inflight · last_heartbeat
```

- [ ] **Step 4: Add the offline execution lane**

Create `data-layer="offline"` nodes for:

```text
媒体处理执行器
通用离线执行器
离线 ASR 适配器
PPT 提交适配器
PPT 回调接收器
OCR 适配器
课程脑图适配器 /v1/course_overviews
单图关键词适配器 /v1/extract_keywords
结果写入器
```

Draw the PPT branch as:

```text
P 视频 -> PPT 提交 -> PPT 回调
      -> ppt_image_id × N
      -> OCR × N
      -> extract_keywords × N
      -> 结果写入器
```

- [ ] **Step 5: Add the online-image and realtime-ASR lanes**

Create `data-layer="online"` nodes for the synchronous inference gateway and TIAS, face, and image-quality adapters. Show that upstream sends prepared Base64 images and that no RTSP connection or Kafka is involved.

Create `data-layer="realtime"` nodes for the realtime ASR gateway, WebSocket session routing, and live subtitle response. Keep this lane visually separate from online images.

- [ ] **Step 6: Add realistic compute resources and business storage**

Create multiple resource groups with different sparse deployments instead of duplicating every operator on every GPU:

```text
GPU 0: 离线 ASR × 2
GPU 1: TIAS × 2, 人脸 × 1
GPU 2: OCR × 2, 图像质量 × 1
CPU 节点: PPT 切片, 媒体处理, 结果写入
```

Add a purple business database node and connect only the result writer to it. Online image results and realtime subtitles return to upstream rather than writing directly to that node.

- [ ] **Step 7: Run the contract test**

Run:

```bash
node --test tests/architecture-review.test.mjs
```

Expected: content assertions pass; interaction assertions may fail until Task 4.

### Task 4: Add Filters, Zoom, Print, and Component Details

**Files:**
- Modify: `algorithm-scheduling-architecture-review.html`
- Test: `tests/architecture-review.test.mjs`

- [ ] **Step 1: Add the segmented layer control**

Use buttons with these hooks:

```html
<div class="segmented" role="group" aria-label="架构视图">
  <button data-filter="all" aria-pressed="true">全部</button>
  <button data-filter="control" aria-pressed="false">控制面</button>
  <button data-filter="offline" aria-pressed="false">离线任务</button>
  <button data-filter="online" aria-pressed="false">在线图片</button>
  <button data-filter="realtime" aria-pressed="false">实时语音</button>
</div>
```

Filtering must add `.is-muted` to unrelated nodes and connectors, preserve all layout dimensions, and update `aria-pressed`.

- [ ] **Step 2: Add zoom and print commands**

Provide compact symbol buttons with visible tooltips:

```html
<button data-action="zoom-out" aria-label="缩小架构图" title="缩小">−</button>
<button data-action="reset" aria-label="复位架构图" title="复位">1:1</button>
<button data-action="zoom-in" aria-label="放大架构图" title="放大">+</button>
<button data-action="print" aria-label="打印架构评审" title="打印">打印</button>
```

Clamp zoom to `0.7 <= scale <= 1.35` in `0.1` steps. Reset must restore `1`, scroll the diagram viewport to its top-left, and update the visible percentage.

- [ ] **Step 3: Add component metadata and the detail panel**

Store component metadata in one JavaScript object:

```javascript
const componentDetails = {
  "task-ingress": {
    title: "任务接入服务",
    responsibility: "校验课程信息，事务写入 course_job 与 outbox_event。",
    input: "课程标识、T/S/P 视频 URL、业务上下文",
    output: "course_job_id 与 PUBLISH_PENDING 状态",
    boundary: "不直接调用算法算子。",
  },
  "sync-gateway": {
    title: "同步推理网关",
    responsibility: "为在线图片请求选择并占用可用算子实例。",
    input: "上游已经截取的 Base64 图片",
    output: "统一外壳与算子原始 data",
    boundary: "不接 RTSP、不截图、不进入离线 Kafka。",
  },
};
```

Every clickable node must have an entry. Clicking a node updates `#component-detail`, marks the node selected, and moves keyboard focus to the detail heading only when activation came from the keyboard.

- [ ] **Step 4: Add the agreed online response contract**

Render a contract band containing:

```json
{
  "request_id": "01J...",
  "code": 200,
  "message": "success",
  "data": {}
}
```

Show platform errors `HTTP 429`, `HTTP 503`, `HTTP 504`, and `HTTP 502`, plus response headers `X-Request-ID` and `X-Operator-Instance`.

- [ ] **Step 5: Run the contract test**

Run:

```bash
node --test tests/architecture-review.test.mjs
```

Expected: all tests PASS.

### Task 5: Add Responsive and Print Layouts

**Files:**
- Modify: `algorithm-scheduling-architecture-review.html`
- Test: `tests/architecture-review.test.mjs`

- [ ] **Step 1: Add responsive constraints**

At widths below `760px`:

```css
@media (max-width: 760px) {
  .page-header__inner,
  main,
  footer { padding-inline: 16px; }
  .review-summary { grid-template-columns: 1fr; }
  .architecture-toolbar { align-items: stretch; }
  .segmented { overflow-x: auto; }
  .diagram-viewport { min-height: 640px; }
  .contract-grid,
  .state-grid { grid-template-columns: 1fr; }
}
```

The architecture canvas remains fixed-format and scrollable; do not shrink text to fit.

- [ ] **Step 2: Add print rules**

```css
@media print {
  .architecture-toolbar,
  .detail-panel__hint { display: none !important; }
  body { background: #fff; }
  .diagram-viewport { overflow: visible; border: 0; }
  .diagram { transform: scale(.63); transform-origin: top left; }
  .architecture-section { break-before: page; }
  .review-band,
  .contract-section,
  .state-section { break-inside: avoid; }
}
```

- [ ] **Step 3: Run static verification**

Run:

```bash
node --test tests/architecture-review.test.mjs
```

Expected: all tests PASS and no external dependency assertions fail.

### Task 6: Browser Verification

**Files:**
- Verify: `algorithm-scheduling-architecture-review.html`

- [ ] **Step 1: Open the local HTML in the in-app browser**

Navigate to the absolute `file://` URL for the generated HTML and wait until `document.readyState === "complete"`.

- [ ] **Step 2: Verify the desktop layout**

At a desktop viewport around 1440 × 1000, check:

```text
page title visible
seven findings visible
diagram canvas nonblank
nodes do not overlap
labels remain inside nodes
connector arrows point to intended nodes
detail panel updates after a node click
```

- [ ] **Step 3: Verify interactions**

Click each filter and confirm unrelated layers become muted. Exercise zoom in, zoom out, and reset; confirm the diagram scroll dimensions remain usable. Stub `window.print`, click print, and confirm it is called once.

- [ ] **Step 4: Verify the narrow layout**

At a viewport around 390 × 844, check that review text wraps, segmented controls scroll, the fixed diagram is horizontally scrollable, and no controls or text overlap.

- [ ] **Step 5: Check runtime health**

Confirm the browser console contains no uncaught exceptions and the page has no failed network requests.

- [ ] **Step 6: Perform final workspace verification**

Run:

```bash
node --test tests/architecture-review.test.mjs
```

Expected: all tests PASS.
