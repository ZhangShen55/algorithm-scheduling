import type { ActiveLeaseResponse, CapacitySnapshot, ConsoleConfig, ConsoleData, GatewayMetrics, GpuMetrics, KafkaMetrics, OperatorInstance, QueueSnapshot, TaskDetail, TaskListResponse } from './types'

const DEFAULT_CONFIG: ConsoleConfig = {
  controlBaseUrl: import.meta.env.VITE_CONTROL_BASE_URL || '/control',
  gatewayBaseUrl: import.meta.env.VITE_GATEWAY_BASE_URL || '/gateway',
  gpuBaseUrl: import.meta.env.VITE_GPU_BASE_URL || 'http://192.168.29.11:9400',
  refreshSeconds: 10,
  leaseRefreshSeconds: 5,
  gpuRefreshSeconds: 5,
}

const CONFIG_KEY = 'algorithm-scheduling-ops-console-config'
const LEGACY_CONFIG_KEY = 'ops-console-config'

function cleanBaseUrl(value: string, fallback: string): string {
  const normalized = value.trim().replace(/\/$/, '')
  return normalized || fallback
}

function clampNumber(value: number, minimum: number, maximum: number, fallback: number): number {
  return Number.isFinite(value) ? Math.min(maximum, Math.max(minimum, Math.round(value))) : fallback
}

export function loadConsoleConfig(): ConsoleConfig {
  try {
    const stored = JSON.parse(window.localStorage.getItem(CONFIG_KEY) || window.localStorage.getItem(LEGACY_CONFIG_KEY) || '{}') as Partial<ConsoleConfig>
    return {
      controlBaseUrl: cleanBaseUrl(stored.controlBaseUrl || DEFAULT_CONFIG.controlBaseUrl, DEFAULT_CONFIG.controlBaseUrl),
      gatewayBaseUrl: cleanBaseUrl(stored.gatewayBaseUrl || DEFAULT_CONFIG.gatewayBaseUrl, DEFAULT_CONFIG.gatewayBaseUrl),
      gpuBaseUrl: cleanBaseUrl(stored.gpuBaseUrl || DEFAULT_CONFIG.gpuBaseUrl, DEFAULT_CONFIG.gpuBaseUrl),
      refreshSeconds: clampNumber(Number(stored.refreshSeconds), 1, 60, DEFAULT_CONFIG.refreshSeconds),
      leaseRefreshSeconds: clampNumber(Number(stored.leaseRefreshSeconds), 1, 30, DEFAULT_CONFIG.leaseRefreshSeconds),
      gpuRefreshSeconds: clampNumber(Number(stored.gpuRefreshSeconds), 1, 30, DEFAULT_CONFIG.gpuRefreshSeconds),
    }
  } catch {
    return { ...DEFAULT_CONFIG }
  }
}

export function saveConsoleConfig(config: ConsoleConfig): ConsoleConfig {
  const next = {
    controlBaseUrl: cleanBaseUrl(config.controlBaseUrl, DEFAULT_CONFIG.controlBaseUrl),
    gatewayBaseUrl: cleanBaseUrl(config.gatewayBaseUrl, DEFAULT_CONFIG.gatewayBaseUrl),
    gpuBaseUrl: cleanBaseUrl(config.gpuBaseUrl, DEFAULT_CONFIG.gpuBaseUrl),
    refreshSeconds: clampNumber(config.refreshSeconds, 1, 60, DEFAULT_CONFIG.refreshSeconds),
    leaseRefreshSeconds: clampNumber(config.leaseRefreshSeconds, 1, 30, DEFAULT_CONFIG.leaseRefreshSeconds),
    gpuRefreshSeconds: clampNumber(config.gpuRefreshSeconds, 1, 30, DEFAULT_CONFIG.gpuRefreshSeconds),
  }
  window.localStorage.setItem(CONFIG_KEY, JSON.stringify(next))
  return next
}

export function defaultConsoleConfig(): ConsoleConfig { return { ...DEFAULT_CONFIG } }

type HttpError = Error & { status: number; payload?: unknown }

async function getJson<T>(base: string, path: string): Promise<T> {
  const response = await fetch(`${base}${path}`, { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    const payload = await response.json().catch(() => undefined)
    const error = Object.assign(new Error(`${path} ${response.status}`), { status: response.status, payload }) as HttpError
    throw error
  }
  return response.json() as Promise<T>
}

async function getText(base: string, path: string): Promise<string> {
  const response = await fetch(`${base}${path}`, { headers: { Accept: 'text/plain' } })
  if (!response.ok) throw new Error(`${path} ${response.status}`)
  return response.text()
}

function parseNumber(value: string | undefined): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function parseLabels(value: string): Record<string, string> {
  const labels: Record<string, string> = {}
  value.replace(/(\w+)="((?:\\.|[^"])*)"/g, (_, key: string, item: string) => {
    labels[key] = item.replace(/\\"/g, '"')
    return ''
  })
  return labels
}

export function parsePrometheus(text: string): GatewayMetrics {
  let requestTotal = 0
  let errorTotal = 0
  let latencyCount = 0
  let latencySum = 0
  let capacityRejected = 0
  const operatorCounts = new Map<string, number>()
  const latencyBuckets = new Map<number, number>()
  for (const line of text.split('\n')) {
    if (!line || line.startsWith('#')) continue
    const match = line.match(/^([\w:]+)(?:\{([^}]*)\})?\s+([-+\d.eE]+)$/)
    if (!match) continue
    const [, metric, rawLabels, rawValue] = match
    const value = parseNumber(rawValue)
    const labels = parseLabels(rawLabels || '')
    if (metric === 'algorithm_operator_request_latency_seconds_count') {
      requestTotal += value
      latencyCount += value
      operatorCounts.set(labels.operator_code || 'unknown', (operatorCounts.get(labels.operator_code || 'unknown') || 0) + value)
    }
    if (metric === 'algorithm_operator_request_latency_seconds_sum') latencySum += value
    if (metric === 'algorithm_operator_request_latency_seconds_bucket') {
      const upperBound = labels.le === '+Inf' ? Number.POSITIVE_INFINITY : parseNumber(labels.le)
      latencyBuckets.set(upperBound, (latencyBuckets.get(upperBound) || 0) + value)
    }
    if (metric === 'algorithm_operator_request_errors_total') errorTotal += value
    if (metric === 'algorithm_capacity_lease_events_total' && labels.outcome === 'rejected') capacityRejected += value
  }
  const p95Threshold = latencyCount * 0.95
  const p95Bucket = latencyCount > 0
    ? [...latencyBuckets.entries()]
      .sort(([left], [right]) => left - right)
      .find(([, count]) => count >= p95Threshold)?.[0]
    : undefined
  const averageLatencyMs = latencyCount ? latencySum / latencyCount * 1000 : 0
  const p95LatencyMs = Number.isFinite(p95Bucket) ? Number(p95Bucket) * 1000 : averageLatencyMs
  return {
    requestTotal, errorTotal, latencyCount, latencySum, p95LatencyMs, capacityRejected,
    byOperator: [...operatorCounts.entries()].map(([name, value]) => ({ name, value })).sort((a, b) => b.value - a.value),
    sampledAt: new Date().toISOString(),
  }
}

export function parsePlatformMetrics(text: string): KafkaMetrics {
  let outboxPending = 0
  let published = 0
  let publishFailed = 0
  let consumerLag = 0
  for (const line of text.split('\n')) {
    const match = line.match(/^([\w:]+)(?:\{([^}]*)\})?\s+([-+\d.eE]+)$/)
    if (!match) continue
    const [, metric, rawLabels, rawValue] = match
    const value = parseNumber(rawValue)
    const labels = parseLabels(rawLabels || '')
    if (metric === 'algorithm_outbox_pending') outboxPending = value
    if (metric === 'algorithm_outbox_publish_total' && labels.outcome === 'published') published += value
    if (metric === 'algorithm_outbox_publish_total' && ['failed', 'error'].includes(labels.outcome || '')) publishFailed += value
    if (metric === 'algorithm_kafka_consumer_lag') consumerLag += value
  }
  return { status: text ? 'ok' : 'unavailable', publisherStatus: text ? 'ok' : 'unavailable', outboxPending, published, publishFailed, consumerLag, sampledAt: new Date().toISOString() }
}

function normalizeStorage(payload: ConsoleData['storage']): ConsoleData['storage'] {
  return {
    roots: (payload.roots || []).map((root) => ({
      ...root,
      total_bytes: root.total_bytes ?? root.filesystem?.total,
      used_bytes: root.used_bytes ?? root.filesystem?.used,
      free_bytes: root.free_bytes ?? root.filesystem?.free,
    })),
  }
}

export async function fetchConsoleData(config = loadConsoleConfig()): Promise<ConsoleData> {
  const controlBase = cleanBaseUrl(config.controlBaseUrl, DEFAULT_CONFIG.controlBaseUrl)
  const gatewayBase = cleanBaseUrl(config.gatewayBaseUrl, DEFAULT_CONFIG.gatewayBaseUrl)
  const gpuBase = cleanBaseUrl(config.gpuBaseUrl, DEFAULT_CONFIG.gpuBaseUrl)
  const [instances, snapshots, queues, storage, readiness, controlMetricsText, metricsText, gpu, kafka] = await Promise.all([
    getJson<OperatorInstance[]>(controlBase, '/ops/operator-instances'),
    getJson<CapacitySnapshot[]>(controlBase, '/ops/operator-instances/snapshot'),
    getJson<QueueSnapshot>(controlBase, '/ops/queues'),
    getJson<ConsoleData['storage']>(controlBase, '/ops/storage').then(normalizeStorage),
    getJson<ConsoleData['readiness']>(controlBase, '/ops/readiness').catch((error: HttpError) => {
      if (error.payload && typeof error.payload === 'object') return error.payload as ConsoleData['readiness']
      return { status: 'unknown', error: String(error) }
    }),
    getText(controlBase, '/metrics').catch(() => ''),
    getText(gatewayBase, '/metrics'),
    getJson<GpuMetrics>(gpuBase, '/gpu').catch((error) => ({ status: 'unavailable', sampled_at: Date.now() / 1000, devices: [], error: String(error) }) as GpuMetrics),
    fetchKafkaMetrics(config).catch(() => null),
  ])
  return { instances, snapshots, queues, storage, readiness, gateway: parsePrometheus(metricsText), kafka: kafka || parsePlatformMetrics(controlMetricsText), gpu, source: 'live', refreshedAt: new Date().toISOString() }
}

export async function fetchTask(taskId: string, config = loadConsoleConfig()): Promise<TaskDetail> {
  return getJson<TaskDetail>(cleanBaseUrl(config.controlBaseUrl, DEFAULT_CONFIG.controlBaseUrl), `/ops/course-jobs/${encodeURIComponent(taskId)}`)
}

export async function fetchTaskList(
  page = 1,
  pageSize = 10,
  sortBy: TaskListResponse['sort_by'] = 'updated_at',
  order: TaskListResponse['order'] = 'desc',
  config = loadConsoleConfig(),
): Promise<TaskListResponse> {
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    sort_by: sortBy,
    order,
  })
  return getJson<TaskListResponse>(cleanBaseUrl(config.controlBaseUrl, DEFAULT_CONFIG.controlBaseUrl), `/ops/course-jobs?${query.toString()}`)
}

export async function fetchActiveLeases(instanceId: string, config = loadConsoleConfig()): Promise<ActiveLeaseResponse> {
  return getJson<ActiveLeaseResponse>(cleanBaseUrl(config.controlBaseUrl, DEFAULT_CONFIG.controlBaseUrl), `/ops/operator-instances/${encodeURIComponent(instanceId)}/active-leases`)
}

export async function fetchGpuMetrics(config = loadConsoleConfig()): Promise<GpuMetrics> {
  return getJson<GpuMetrics>(cleanBaseUrl(config.gpuBaseUrl, DEFAULT_CONFIG.gpuBaseUrl), '/gpu')
}

export async function fetchKafkaMetrics(config = loadConsoleConfig()): Promise<KafkaMetrics> {
  const payload = await getJson<{ status?: 'ok' | 'degraded'; publisher_status?: 'ok' | 'unavailable'; outbox_pending: number; published: number; publish_failed: number; consumer_lag: number; sampled_at: string }>(cleanBaseUrl(config.controlBaseUrl, DEFAULT_CONFIG.controlBaseUrl), '/ops/kafka')
  return { status: payload.status || 'degraded', publisherStatus: payload.publisher_status || 'unavailable', outboxPending: payload.outbox_pending, published: payload.published, publishFailed: payload.publish_failed, consumerLag: payload.consumer_lag, sampledAt: payload.sampled_at }
}

export function demoActiveLeases(instanceId: string): ActiveLeaseResponse {
  const isBusy = instanceId.endsWith('1') || instanceId.includes('asr')
  return {
    instance_id: instanceId,
    active_lease_count: isBusy ? 2 : 0,
    reported_inflight: isBusy ? 2 : 0,
    attribution_difference: 0,
    leases: isBusy ? [
      { lease_id: `lease-${instanceId}-001`, instance_id: instanceId, capability: instanceId.startsWith('vbas') ? 'student_behavior' : instanceId.split('-')[0], service_url: `http://${instanceId}:8000`, acquired_at: new Date(Date.now() - 62000).toISOString(), expires_at: new Date(Date.now() + 38000).toISOString(), context_status: 'BOUND', capacity_pool: 'default', work_context: { source_service: instanceId.startsWith('vbas') ? 'vision-orchestrator-service' : 'orchestrator-service', work_type: instanceId.startsWith('vbas') ? 'vbas_student_behavior_batch' : 'asr_transcription', work_id: 'node-work-20260831-001', task_id: 'course-20260831-001', node_id: instanceId.startsWith('vbas') ? 'STUDENT_BEHAVIOR' : 'ASR_TRANSCRIPTION', item_id: 'batch-0007' } },
      { lease_id: `lease-${instanceId}-002`, instance_id: instanceId, capability: instanceId.startsWith('vbas') ? 'teacher_behavior' : instanceId.split('-')[0], service_url: `http://${instanceId}:8000`, acquired_at: new Date(Date.now() - 19000).toISOString(), expires_at: new Date(Date.now() + 81000).toISOString(), context_status: 'BOUND', capacity_pool: 'default', work_context: { source_service: 'online-gateway-service', work_type: 'online_image_quality', work_id: 'online-request-0092', task_id: null, node_id: null, item_id: null } },
    ] : [],
  }
}

export function demoTask(taskId: string): TaskDetail {
  return {
    task_id: taskId,
    tasks: [
      { task_type: 'PPT', status: 40, status_text: '已完成', nodes: [{ node_code: 'PPT_SLICE', capability: 'ppt_slice', status: 40, status_text: '已完成', finished_at: new Date(Date.now() - 180000).toISOString() }, { node_code: 'PPT_OCR', capability: 'ocr', status: 40, status_text: '已完成' }] },
      { task_type: 'ASR', status: 20, status_text: '处理中', nodes: [{ node_code: 'ASR_TRANSCRIPTION', capability: 'asr_offline', status: 20, status_text: '处理中', started_at: new Date(Date.now() - 420000).toISOString() }] },
      { task_type: 'STUDENT_BEHAVIOR', status: 40, status_text: '已完成', nodes: [{ node_code: 'VBAS', capability: 'student_behavior', status: 40, status_text: '已完成' }] },
    ],
  }
}

export function demoTaskList(page = 1, pageSize = 10, sortBy: TaskListResponse['sort_by'] = 'updated_at', order: TaskListResponse['order'] = 'desc'): TaskListResponse {
  const allItems: TaskListResponse['items'] = Array.from({ length: 27 }, (_, index) => {
    const id = `course-202609${String(2 + Math.floor(index / 9)).padStart(2, '0')}-${String(index + 1).padStart(3, '0')}`
    const updatedAt = new Date(Date.now() - index * 8 * 60 * 1000).toISOString()
    const detail = demoTask(id)
    return { task_id: id, created_at: new Date(Date.parse(updatedAt) - 3600000).toISOString(), updated_at: updatedAt, status: index % 5 === 1 ? 50 : index % 7 === 0 ? 70 : 60, status_text: index % 5 === 1 ? '处理中' : index % 7 === 0 ? '处理失败' : '已完成', task_count: detail.tasks.length, tasks: detail.tasks }
  })
  const sorted = [...allItems].sort((left, right) => {
    const leftValue = sortBy === 'task_id' ? left.task_id : Date.parse(left[sortBy])
    const rightValue = sortBy === 'task_id' ? right.task_id : Date.parse(right[sortBy])
    const result = typeof leftValue === 'number' && typeof rightValue === 'number' ? leftValue - rightValue : String(leftValue).localeCompare(String(rightValue))
    return order === 'asc' ? result : -result
  })
  const totalPages = Math.ceil(sorted.length / pageSize)
  return { items: sorted.slice((page - 1) * pageSize, page * pageSize), page, page_size: pageSize, total: sorted.length, total_pages: totalPages, sort_by: sortBy, order }
}

export function emptyConsoleData(): ConsoleData {
  return {
    instances: [],
    snapshots: [],
    queues: { outbox_pending: 0, queues: [] },
    storage: { roots: [] },
    readiness: { status: 'unknown', checks: {} },
    gateway: { requestTotal: 0, errorTotal: 0, latencyCount: 0, latencySum: 0, p95LatencyMs: 0, capacityRejected: 0, byOperator: [], sampledAt: new Date().toISOString() },
    kafka: { status: 'unavailable', publisherStatus: 'unavailable', outboxPending: 0, published: 0, publishFailed: 0, consumerLag: 0, sampledAt: new Date().toISOString() },
    gpu: { status: 'unavailable', sampled_at: Date.now() / 1000, devices: [] },
    source: 'live',
    refreshedAt: new Date().toISOString(),
  }
}

export function demoData(): ConsoleData {
  const definitions = [
    ['vbas', 3, 1024, 'GPU'], ['ocr', 3, 256, 'GPU'], ['facerec', 3, 128, 'GPU'],
    ['screen_det', 3, 128, 'GPU'], ['asr_offline', 3, 4, 'GPU'], ['asr_online', 3, 10, 'GPU'], ['ppt_slice', 3, 10, 'CPU'],
  ] as const
  const instances: OperatorInstance[] = definitions.flatMap(([code, count, capacity, device]) => Array.from({ length: count }, (_, index) => {
    const inflight = (index + code.length) % 4
    return { instance_id: `${code}-${device.toLowerCase()}${index}`, operator_code: code, capabilities: [code === 'vbas' ? 'student_behavior' : code], service_url: `http://${code}-${index}:8000`, declared_capacity: capacity, model_version: '2026.08.31', api_version: 'v1', labels: { device }, lifecycle: 'ONLINE', inflight, model_ready: true, last_heartbeat_at: new Date(Date.now() - index * 18000).toISOString() }
  }))
  const snapshots = instances.map((instance) => ({ instance_id: instance.instance_id, operator_code: instance.operator_code, lifecycle: instance.lifecycle, model_ready: instance.model_ready, declared_capacity: instance.declared_capacity, reported_inflight: instance.inflight, active_lease_count: Math.max(0, instance.inflight - 1), schedulable_used: instance.inflight, attribution_difference: 1, capacity_mismatch: true, capacity_pools: { default: instance.declared_capacity }, inflight_by_pool: { default: instance.inflight } }))
  return { instances, snapshots, queues: { outbox_pending: 8, queues: [{ status: 10, status_text: '待处理', priority: 20, capability: 'ppt_slice', count: 4 }, { status: 20, status_text: '处理中', priority: 20, capability: 'asr_offline', count: 7 }, { status: 30, status_text: '等待容量', priority: 10, capability: 'ocr', count: 3 }] }, storage: { roots: [{ kind: 'course', path: '/data/course', total_bytes: 107374182400, used_bytes: 55834574848, free_bytes: 51539607552 }, { kind: 'result', path: '/data/result', total_bytes: 107374182400, used_bytes: 32212254720, free_bytes: 75161927680 }] }, readiness: { status: 'ready', checks: { postgres: 'ok', redis: 'ok', schema: 'ok' } }, gateway: { requestTotal: 12840, errorTotal: 36, latencyCount: 12840, latencySum: 4423, p95LatencyMs: 780, capacityRejected: 12, byOperator: [{ name: 'vbas', value: 6600 }, { name: 'facerec', value: 3210 }, { name: 'ocr', value: 2100 }, { name: 'screen_det', value: 930 }], sampledAt: new Date().toISOString() }, kafka: { status: 'ok', publisherStatus: 'ok', outboxPending: 8, published: 44210, publishFailed: 9, consumerLag: 3, sampledAt: new Date().toISOString() }, gpu: { status: 'ok', sampled_at: Date.now() / 1000, devices: [0, 1, 2].map(index => ({ index, name: 'NVIDIA GPU', utilization_percent: 35 + index * 17, memory_used_bytes: 7_000_000_000 + index * 1_200_000_000, memory_total_bytes: 24_000_000_000, temperature_celsius: 55 + index * 3, power_watts: 100 + index * 8, process_count: 6 + index })) }, source: 'demo', refreshedAt: new Date().toISOString() }
}
