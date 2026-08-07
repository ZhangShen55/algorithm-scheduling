# Architecture Diagram Flow Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the crowded single architecture canvas with a concise overview and four independently displayed detail diagrams whose flows are readable without crossing long-distance curves.

**Architecture:** Keep the review as one dependency-free HTML file. Build the overview and each detail view as separate dotted-grid diagram surfaces using semantic HTML nodes, horizontal arrow connectors, parallel flow rows, and true tab visibility; keep the shared detail panel and make zoom target only the active detail surface.

**Tech Stack:** HTML5, embedded CSS, embedded vanilla JavaScript, Node.js built-in test runner.

---

The workspace is not a Git repository. Commit steps are intentionally replaced by test checkpoints; no algorithm project files are modified.

### Task 1: Strengthen The Architecture Contract Tests

**Files:**
- Modify: `tests/architecture-review.test.mjs`
- Test: `tests/architecture-review.test.mjs`

- [ ] **Step 1: Add a section extraction helper**

Add this helper after loading `html`:

```js
function sectionById(id) {
  const start = html.indexOf(`id="${id}"`);
  assert.notEqual(start, -1, `missing section ${id}`);
  const nextSection = html.indexOf("<section", start + 1);
  return html.slice(start, nextSection === -1 ? html.length : nextSection);
}
```

- [ ] **Step 2: Replace old layer-filter assertions with new view assertions**

Assert the overview and four detail views:

```js
test("contains a concise overview and four independent detail views", () => {
  assert.match(html, /id=["']platform-overview["']/);

  for (const view of ["offline", "online", "realtime", "deployment"]) {
    assert.match(html, new RegExp(`data-view=["']${view}["']`));
    assert.match(html, new RegExp(`id=["']view-${view}["']`));
  }

  assert.doesNotMatch(html, /data-filter=/);
  assert.doesNotMatch(html, /class=["'][^"']*connectors/);
});
```

- [ ] **Step 3: Add flow-boundary assertions**

Add tests that scope important decisions to their detail panels:

```js
test("offline view shows shared media preparation and three processing lanes", () => {
  const offline = sectionById("view-offline");
  assert.match(offline, /\/data\/course\/\{course_job_id\}/);
  assert.match(offline, /T\.mp4/);
  assert.match(offline, /S\.mp4/);
  assert.match(offline, /P\.mp4/);
  assert.match(offline, /ASR 泳道/);
  assert.match(offline, /PPT 泳道/);
  assert.match(offline, /视觉分析泳道/);
  assert.match(offline, /ai_quality/);
  assert.match(offline, /帧结果聚合/);
  assert.match(offline, /CLEANUP_WORKSPACE/);
});

test("online view starts with Base64 and routes one whole request to one instance", () => {
  const online = sectionById("view-online");
  assert.match(online, /Base64/);
  assert.match(online, /\/v1\/online\/tias\/analyze/);
  assert.match(online, /\/v1\/online\/face\/recognize/);
  assert.match(online, /\/v1\/online\/image-quality\/detect/);
  assert.match(online, /完整请求只选择一个实例/);
  assert.match(online, /多图请求不跨实例拆分/);
  assert.doesNotMatch(online, /RTSP|拉流|截图/);
});

test("deployment view shows three infrastructure containers and the shared mount", () => {
  const deployment = sectionById("view-deployment");
  assert.match(deployment, /PostgreSQL 容器/);
  assert.match(deployment, /Kafka 容器/);
  assert.match(deployment, /Redis 容器/);
  assert.match(deployment, /\/data\/course/);
  assert.match(deployment, /同一台服务器/);
});
```

- [ ] **Step 4: Update interaction assertions**

Require tab activation and active-view zoom behavior:

```js
test("contains tab, zoom, reset, detail and print interaction contracts", () => {
  for (const view of ["offline", "online", "realtime", "deployment"]) {
    assert.match(html, new RegExp(`data-view-tab=["']${view}["']`));
  }

  for (const action of ["zoom-in", "zoom-out", "reset", "print"]) {
    assert.match(html, new RegExp(`data-action=["']${action}["']`));
  }

  assert.match(html, /function setActiveView/);
  assert.match(html, /function updateActiveScale/);
  assert.match(html, /id=["']component-detail["']/);
});
```

- [ ] **Step 5: Run the tests and observe the expected failure**

Run:

```bash
node --test tests/architecture-review.test.mjs
```

Expected: FAIL because `platform-overview`, `data-view-tab`, `view-deployment`, the three offline swimlanes, and request-level online wording do not yet exist.

### Task 2: Replace The Single Canvas With Overview And Detail Surfaces

**Files:**
- Modify: `algorithm-scheduling-architecture-review.html`
- Test: `tests/architecture-review.test.mjs`

- [ ] **Step 1: Replace old absolute-canvas CSS with reusable diagram primitives**

Remove `.lane-*`, `.connectors`, `.edge-*`, absolute `.node` coordinates, `.is-muted`, `.diagram-stage`, and `.diagram` rules. Add reusable surfaces and flow primitives:

```css
.overview-surface,
.detail-surface {
  position: relative;
  min-width: 1180px;
  overflow: hidden;
  border: 1px solid var(--border);
  background-color: #fbfcfd;
  background-image: radial-gradient(circle, #d6dde2 1.15px, transparent 1.25px);
  background-size: 20px 20px;
}

.overview-surface { padding: 34px; }

.detail-viewport {
  min-width: 0;
  overflow: auto;
  overscroll-behavior: contain;
}

.detail-stage {
  width: 1420px;
  transform: scale(var(--view-scale, 1));
  transform-origin: top left;
}

.flow-row {
  display: grid;
  grid-template-columns: repeat(var(--steps), minmax(0, 1fr));
  align-items: stretch;
  gap: 30px;
}

.flow-node {
  position: relative;
  min-width: 0;
  min-height: 82px;
  padding: 12px 14px;
  border: 1px solid rgba(23, 32, 42, 0.26);
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 5px 14px rgba(23, 32, 42, 0.09);
  text-align: left;
}

.flow-node[data-next]::after {
  content: "";
  position: absolute;
  top: 50%;
  left: calc(100% + 1px);
  width: 30px;
  border-top: 2px solid var(--flow-color, #263238);
}

.flow-node[data-next]::before {
  content: "";
  position: absolute;
  top: calc(50% - 4px);
  left: calc(100% + 25px);
  border-width: 4px 0 4px 6px;
  border-style: solid;
  border-color: transparent transparent transparent var(--flow-color, #263238);
}

.detail-view[hidden] { display: none; }
```

Use `.flow-node--control`, `.flow-node--adapter`, `.flow-node--data`, `.flow-node--realtime`, `.flow-node--resource`, and `.flow-node--business` variants to preserve the current color semantics.

- [ ] **Step 2: Build the concise platform overview**

Create `id="platform-overview"` above the detail tabs. Use three independent horizontal rows:

```html
<section class="overview-surface" id="platform-overview" aria-label="算法调度平台总览">
  <div class="overview-origin">A 上游</div>
  <div class="overview-flows">
    <div class="overview-flow overview-flow--offline">
      <span>离线任务接入</span><span aria-hidden="true">→</span>
      <span>离线编排与执行</span><span aria-hidden="true">→</span>
      <span>业务结果库</span>
    </div>
    <div class="overview-flow overview-flow--online">
      <span>在线图片网关</span><span aria-hidden="true">→</span>
      <span>请求级实例路由</span><span aria-hidden="true">→</span>
      <span>同步响应</span>
    </div>
    <div class="overview-flow overview-flow--realtime">
      <span>实时 ASR 网关</span><span aria-hidden="true">→</span>
      <span>会话实例路由</span><span aria-hidden="true">→</span>
      <span>实时字幕</span>
    </div>
  </div>
  <div class="overview-support">注册中心 · PostgreSQL · Kafka · Redis · /data/course</div>
</section>
```

The overview uses six directional arrows and no SVG connector layer.

- [ ] **Step 3: Replace filter controls with four detail tabs**

Use buttons with tab semantics:

```html
<div class="segmented" role="tablist" aria-label="详细流程视图">
  <button type="button" role="tab" data-view-tab="offline" aria-selected="true" aria-controls="view-offline">离线任务</button>
  <button type="button" role="tab" data-view-tab="online" aria-selected="false" aria-controls="view-online">在线图片</button>
  <button type="button" role="tab" data-view-tab="realtime" aria-selected="false" aria-controls="view-realtime">实时语音</button>
  <button type="button" role="tab" data-view-tab="deployment" aria-selected="false" aria-controls="view-deployment">注册部署</button>
</div>
```

Keep zoom, reset, and print controls beside the tabs.

### Task 3: Build The Four Independent Detail Diagrams

**Files:**
- Modify: `algorithm-scheduling-architecture-review.html`
- Test: `tests/architecture-review.test.mjs`

- [ ] **Step 1: Build the offline view**

Create `section#view-offline.detail-view[data-view="offline"]`. Structure it as:

```text
接入与准备：A → 任务接入 → PostgreSQL + Outbox → Kafka → DAG → 媒体准备 → /data/course/{course_job_id}
ASR 泳道：T.mp4 → teacher.wav → 离线 ASR → 课程脑图 → 结果落库
PPT 泳道：P.mp4 → PPT 切片 → OCR × N → 关键词 × N → 结果落库
视觉分析泳道：T/S → ai_quality（抽帧 → TIAS → 帧结果聚合）→ 结果落库
完成：必需结果已落库 → CLEANUP_WORKSPACE → COMPLETED
```

Render each lane as its own `.flow-lane` with a `.flow-lane__label` and one `.flow-row`. Do not connect one lane to another with an SVG line. Use a shared caption above the lanes to state that all lanes read the same course workspace.

- [ ] **Step 2: Build the online view**

Create `section#view-online.detail-view[data-view="online"]` with a visible boundary sentence containing “Base64 输入 · 完整请求只选择一个实例 · 多图请求不跨实例拆分”. Add three rows:

```text
/v1/online/tias/analyze → Redis 容量占位 → TIAS 适配器 → /AE/SyncTasks2 → 同步响应
/v1/online/face/recognize → Redis 容量占位 → 人脸适配器 → /recognize → 同步响应
/v1/online/image-quality/detect → Redis 容量占位 → 图像质量适配器 → /detect_all → 同步响应
```

Do not include the strings `RTSP`, `拉流`, or `截图` inside `view-online`; the absence of those nodes is the boundary.

- [ ] **Step 3: Build the realtime view**

Create `section#view-realtime.detail-view[data-view="realtime"]` with one row:

```text
直播音频 → WebSocket 网关 → 注册中心选实例 → 会话固定绑定 → 增量字幕 → 播放器
```

Add boundary chips: “不进离线 Kafka”, “默认不入库”, and “断线后重新选实例”.

- [ ] **Step 4: Build the deployment view**

Create `section#view-deployment.detail-view[data-view="deployment"]`. Include two short routing rows and one server enclosure:

```text
算法 Docker 实例 → 注册/心跳 API → Redis TTL 注册表
平台执行器/网关 → 查询与容量占位 → 目标算法实例
```

The server enclosure must contain separate nodes named `PostgreSQL 容器`, `Kafka 容器`, `Redis 容器`, `平台服务容器`, `算法服务容器`, and `宿主机 /data/course 共享挂载`.

- [ ] **Step 5: Update component details**

Keep existing useful detail entries and add entries for these new `data-node` identifiers:

```js
"media-ready": {
  title: "课程媒体准备",
  responsibility: "异步下载 T/S/P 到统一课程工作目录并发布本地媒体已就绪。",
  input: "course_job_id 与三个可下载视频 URL。",
  output: "/data/course/{course_job_id} 下的 T.mp4、S.mp4、P.mp4。",
  boundary: "不在任务接入 HTTP 请求中下载大文件。"
},
"ai-quality-worker": {
  title: "ai_quality 复合 Worker",
  responsibility: "读取本地 T/S，完成抽帧、TIAS 推理、聚合和视觉结果写入。",
  input: "本地 T/S 路径、task_id 和 student_count。",
  output: "时间线、快照、行为统计和指标结果。",
  boundary: "第一版保留复合边界，暂不拆成多个平台 DAG 节点。"
},
"cleanup-workspace": {
  title: "课程工作目录清理",
  responsibility: "在全部必需结果落库后删除课程临时媒体和中间产物。",
  input: "已完成的 course_job_id。",
  output: "释放后的本地磁盘空间。",
  boundary: "业务长期资产必须先迁移，不能随工作目录删除。"
},
"request-router": {
  title: "请求级实例路由",
  responsibility: "为一次完整 HTTP 请求选择并占用一个算子实例。",
  input: "operator_code、实例状态和 inflight。",
  output: "绑定的 instance_id。",
  boundary: "不按图片拆分多图请求，不执行跨实例聚合。"
}
```

- [ ] **Step 6: Run tests**

Run:

```bash
node --test tests/architecture-review.test.mjs
```

Expected: the new overview, flow-boundary, view, deployment, and interaction contract tests PASS.

### Task 4: Implement True Tab Switching And Active-View Zoom

**Files:**
- Modify: `algorithm-scheduling-architecture-review.html`
- Test: `tests/architecture-review.test.mjs`

- [ ] **Step 1: Replace filter state with active view state**

Use one active view and one scale value per view:

```js
let activeView = "offline";
const viewScales = {
  offline: 1,
  online: 1,
  realtime: 1,
  deployment: 1
};

function setActiveView(view) {
  activeView = view;
  document.querySelectorAll("[data-view-tab]").forEach((button) => {
    const selected = button.dataset.viewTab === view;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  document.querySelectorAll(".detail-view[data-view]").forEach((panel) => {
    panel.hidden = panel.dataset.view !== view;
  });
  updateActiveScale();
}
```

- [ ] **Step 2: Scope zoom to the active detail stage**

```js
function activeStage() {
  return document.querySelector(`.detail-view[data-view="${activeView}"] .detail-stage`);
}

function updateActiveScale() {
  const scale = viewScales[activeView];
  const stage = activeStage();
  stage.style.setProperty("--view-scale", scale.toFixed(2));
  stage.parentElement.style.setProperty("--scaled-height", `${Math.round(stage.scrollHeight * scale)}px`);
  document.getElementById("zoom-value").textContent = `${Math.round(scale * 100)}%`;
}
```

Zoom in and out clamp between `0.75` and `1.25`. Reset sets only the active view to `1` and scrolls the active `.detail-viewport` to the top-left.

- [ ] **Step 3: Keep node detail and print interactions**

Bind `.flow-node[data-node]` clicks to the existing `showDetail` logic. Keep `window.print()` for the print action.

- [ ] **Step 4: Add print behavior**

In `@media print`:

```css
.architecture-toolbar { display: none; }
.detail-view[hidden] { display: block; }
.detail-view { break-before: page; }
.detail-viewport { overflow: visible; }
.detail-stage { transform: none !important; width: 100% !important; }
.detail-panel { break-before: page; }
```

- [ ] **Step 5: Run tests and script syntax verification**

Run:

```bash
node --test tests/architecture-review.test.mjs
```

Expected: all tests PASS.

Run:

```bash
node -e 'const fs=require("node:fs");const h=fs.readFileSync("algorithm-scheduling-architecture-review.html","utf8");const a=h.indexOf("<script>");const b=h.indexOf("</script>",a);new Function(h.slice(a+8,b));console.log("inline script syntax: ok")'
```

Expected: `inline script syntax: ok`.

### Task 5: Responsive, Accessibility, And Final Verification

**Files:**
- Modify: `algorithm-scheduling-architecture-review.html`
- Test: `tests/architecture-review.test.mjs`

- [ ] **Step 1: Add narrow-screen rules**

At widths below `760px`:

```css
.page-header__inner,
.architecture-toolbar { align-items: stretch; flex-direction: column; }
.segmented { overflow-x: auto; }
.overview-viewport,
.detail-viewport { margin-inline: -18px; padding-inline: 18px; }
.detail-panel__body { grid-template-columns: 1fr; }
```

Keep diagram surfaces at stable minimum widths so nodes do not squeeze or overlap; narrow screens scroll horizontally.

- [ ] **Step 2: Verify accessible tab state in markup and JavaScript**

Check that each tab has `role="tab"`, `aria-controls`, and one selected tab; each detail panel has `role="tabpanel"` and `aria-labelledby`.

- [ ] **Step 3: Run the full test suite**

Run:

```bash
node --test tests/architecture-review.test.mjs
```

Expected: all tests PASS with zero failures.

- [ ] **Step 4: Run the standalone-resource audit**

Run:

```bash
node -e 'const fs=require("node:fs");const h=fs.readFileSync("algorithm-scheduling-architecture-review.html","utf8");if(/https?:\/\//i.test(h)||/<script[^>]+src=/i.test(h)||/<link[^>]+href=/i.test(h))throw new Error("external dependency found");console.log("external resources: none")'
```

Expected: `external resources: none`.

- [ ] **Step 5: Perform visual verification when local browser policy permits**

Check desktop around `1440 × 1000` and narrow layout around `390 × 844` for text fit, horizontal scrolling, tab replacement, detail updates, zoom, reset, and print. If the in-app browser rejects the local `file://` page, do not bypass the policy; record visual verification as unavailable and rely on the contract, syntax, and structural checks.
