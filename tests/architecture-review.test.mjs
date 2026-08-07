import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const currentDir = dirname(fileURLToPath(import.meta.url));
const htmlPath = resolve(currentDir, "../algorithm-scheduling-architecture-review.html");
const html = readFileSync(htmlPath, "utf8");

function sectionById(id) {
  const start = html.indexOf(`id="${id}"`);
  assert.notEqual(start, -1, `missing section ${id}`);
  const nextSection = html.indexOf("<section", start + 1);
  return html.slice(start, nextSection === -1 ? html.length : nextSection);
}

test("is a standalone Chinese architecture review", () => {
  assert.match(html, /算法调度平台架构评审/);
  assert.doesNotMatch(html, /<script[^>]+src=/i);
  assert.doesNotMatch(html, /<link[^>]+href=["']https?:/i);
  assert.doesNotMatch(html, /url\(["']?https?:/i);
});

test("contains a concise overview and four independent detail views", () => {
  assert.match(html, /id=["']platform-overview["']/);

  for (const view of ["offline", "online", "realtime", "deployment"]) {
    assert.match(html, new RegExp(`data-view=["']${view}["']`));
    assert.match(html, new RegExp(`id=["']view-${view}["']`));
  }

  assert.doesNotMatch(html, /data-filter=/);
  assert.doesNotMatch(html, /class=["'][^"']*connectors/);
});

test("contains every required execution capability", () => {
  for (const node of [
    "task-ingress",
    "outbox",
    "kafka",
    "orchestrator",
    "task-db",
    "registry",
    "media-ready",
    "ai-quality-worker",
    "cleanup-workspace",
    "ppt-callback",
    "result-writer",
    "sync-gateway",
    "realtime-gateway",
    "request-router",
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

test("deployment view shows infrastructure containers and the shared mount", () => {
  const deployment = sectionById("view-deployment");
  assert.match(deployment, /PostgreSQL 容器/);
  assert.match(deployment, /Kafka 容器/);
  assert.match(deployment, /Redis 容器/);
  assert.match(deployment, /\/data\/course/);
  assert.match(deployment, /同一台服务器/);
});

test("contains tab, zoom, detail and print interaction contracts", () => {
  for (const view of ["offline", "online", "realtime", "deployment"]) {
    assert.match(html, new RegExp(`data-view-tab=["']${view}["']`));
  }

  for (const action of ["zoom-in", "zoom-out", "reset", "print"]) {
    assert.match(html, new RegExp(`data-action=["']${action}["']`));
  }

  assert.match(html, /request_id/);
  assert.match(html, /X-Operator-Instance/);
  assert.match(html, /HTTP 429/);
  assert.match(html, /HTTP 503/);
  assert.match(html, /HTTP 504/);
  assert.match(html, /function setActiveView/);
  assert.match(html, /function updateActiveScale/);
  assert.match(html, /id=["']component-detail["']/);
});

test("detail tabs do not inherit section gaps and all panels expand for print", () => {
  assert.match(html, /\.detail-view\s*\{[^}]*margin-top:\s*0/s);
  assert.match(
    html,
    /@media print[\s\S]*\.detail-view\[hidden\]\s*\{[^}]*display:\s*block\s*!important/s,
  );
});

test("shows course and node runtime states separately", () => {
  assert.match(html, /课程任务状态/);
  assert.match(html, /节点执行状态/);

  for (const state of [
    "RECEIVED",
    "PUBLISH_PENDING",
    "QUEUED",
    "PENDING",
    "READY",
    "DISPATCHED",
    "RUNNING",
    "WAITING_CALLBACK",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
  ]) {
    assert.match(html, new RegExp(state));
  }
});
