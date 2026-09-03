export type Lifecycle = 'ONLINE' | 'DRAINING' | 'OFFLINE'
export type VisualStyle = 'industrial' | 'command'

export type ConsoleConfig = {
  controlBaseUrl: string
  gatewayBaseUrl: string
  gpuBaseUrl: string
  refreshSeconds: number
  leaseRefreshSeconds: number
  gpuRefreshSeconds: number
}

export type ConfigSource = 'browser' | 'build' | 'deployment-template'
export type ConnectionTestResult = {
  service: 'control' | 'gateway' | 'gpu'
  ok: boolean
  message: string
}

export type OperatorInstance = {
  instance_id: string
  operator_code: string
  capabilities: string[]
  service_url: string
  declared_capacity: number
  model_version?: string | null
  api_version?: string | null
  labels: Record<string, string>
  lifecycle: Lifecycle
  inflight: number
  model_ready: boolean
  last_heartbeat_at: string
  capacity_pools?: Record<string, number>
  inflight_by_pool?: Record<string, number>
}

export type CapacitySnapshot = {
  instance_id: string
  operator_code: string
  lifecycle: Lifecycle
  model_ready: boolean
  declared_capacity: number
  reported_inflight: number
  active_lease_count: number
  schedulable_used: number
  attribution_difference: number
  capacity_mismatch: boolean
  capacity_pools: Record<string, number>
  inflight_by_pool: Record<string, number>
}

export type WorkContext = {
  source_service?: string
  work_type?: string
  work_id?: string
  task_id?: string | null
  node_id?: string | null
  item_id?: string | null
  trace_id?: string | null
  capacity_pool?: string
}

export type ActiveLease = {
  lease_id: string
  instance_id: string
  capability: string
  service_url: string
  acquired_at: string
  expires_at: string
  context_status: 'BOUND' | 'UNBOUND'
  work_context?: WorkContext | null
  capacity_pool?: string
}

export type ActiveLeaseResponse = {
  instance_id: string
  active_lease_count: number
  reported_inflight: number
  attribution_difference: number
  leases: ActiveLease[]
}

export type QueueItem = {
  status: number
  status_text: string
  priority: number
  capability: string | null
  count: number
}

export type QueueSnapshot = { queues: QueueItem[]; outbox_pending: number }

export type TaskNode = {
  node_code?: string
  status?: number
  status_text?: string
  capability?: string | null
  updated_at?: string | null
  started_at?: string | null
  finished_at?: string | null
  ready_at?: string | null
  claimed_at?: string | null
  queue_wait_ms?: number | null
  startup_ms?: number | null
  processing_duration_ms?: number | null
  total_duration_ms?: number | null
  reason?: string | null
  result_summary?: Record<string, unknown>
  effective_params?: Record<string, unknown> | null
  error_message?: string | null
  [key: string]: unknown
}

export type TaskType = { task_type: string; status?: number; status_text?: string; nodes?: TaskNode[]; [key: string]: unknown }
export type TaskDetail = { task_id: string; tasks: TaskType[] }
export type TaskListItem = {
  task_id: string
  created_at: string
  updated_at: string
  status: number
  status_text: string
  task_count: number
  tasks: TaskType[]
}
export type TaskListResponse = {
  items: TaskListItem[]
  page: number
  page_size: number
  total: number
  total_pages: number
  sort_by: 'updated_at' | 'created_at' | 'task_id'
  order: 'asc' | 'desc'
}

export type TaskCode = 'PPT' | 'ASR' | 'TEACHER_BEHAVIOR' | 'STUDENT_BEHAVIOR'
export type TaskListFilters = {
  taskTypes: TaskCode[]
  statusScope: 'overall' | 'task'
  overallStatus?: number
  taskStatusType?: TaskCode
  taskStatus?: number
  updatedFrom?: string
  updatedTo?: string
  taskIdLike?: string
}
export type TaskTypeDetail = TaskType & {
  reason?: string
  priority?: string
  updated_at?: string
  effective_params?: Record<string, unknown> | null
  nodes: TaskNode[]
}
export type TaskResultPage = {
  task_id: string
  task_type: TaskCode
  section: string
  results: Array<{
    node_code: string
    value?: unknown
    items?: unknown[]
    page?: number
    page_size?: number
    total?: number
    total_pages?: number
  }>
}
export type OutboxPublishStatus = 'PENDING' | 'PUBLISHING' | 'RETRY_PENDING' | 'PUBLISHED'
export type OutboxEvent = {
  event_id: string
  aggregate_type: string
  aggregate_id: string
  event_type: string
  task_id?: string | null
  task_type?: string | null
  publish_status: OutboxPublishStatus
  available_at: string
  claimed_at?: string | null
  published_at?: string | null
  publish_attempts: number
  last_error?: string | null
  created_at: string
  payload?: Record<string, unknown>
}
export type OutboxEventList = {
  items: OutboxEvent[]
  page: number
  page_size: number
  total: number
  total_pages: number
  order: 'asc' | 'desc'
}
export type OutboxEventFilters = {
  taskId?: string
  taskIdLike?: string
  eventType?: string
  publishStatus?: OutboxPublishStatus
  createdFrom?: string
  createdTo?: string
  order?: 'asc' | 'desc'
}

export type GatewayMetrics = {
  requestTotal: number
  errorTotal: number
  latencyCount: number
  latencySum: number
  p95LatencyMs: number
  capacityRejected: number
  byOperator: { name: string; value: number }[]
  sampledAt: string
}

export type KafkaMetrics = {
  status: 'ok' | 'degraded' | 'unavailable'
  publisherStatus: 'ok' | 'unavailable'
  outboxPending: number
  published: number
  publishFailed: number
  consumerLag: number
  sampledAt: string
}

export type GpuDevice = {
  index: number
  name: string
  utilization_percent: number
  memory_used_bytes: number
  memory_total_bytes: number
  temperature_celsius?: number | null
  power_watts?: number | null
  process_count?: number | null
}

export type GpuMetrics = {
  status: 'ok' | 'unavailable'
  sampled_at: number
  devices: GpuDevice[]
  error?: string
}

export type OperationsTrendPoint = {
  sampledAt: string
  requestTotal: number
  errorTotal: number
  requestRate: number
  errorRate: number
  p95LatencyMs: number
  queuePending: number
  queueProcessing: number
  queueWaiting: number
}

export type ConsoleData = {
  instances: OperatorInstance[]
  snapshots: CapacitySnapshot[]
  queues: QueueSnapshot
  storage: { roots: { kind: string; path: string; total_bytes?: number; used_bytes?: number; free_bytes?: number; filesystem?: { total?: number; used?: number; free?: number }; [key: string]: unknown }[] }
  readiness: { status?: string; checks?: Record<string, unknown>; [key: string]: unknown }
  gateway: GatewayMetrics
  kafka: KafkaMetrics
  gpu: GpuMetrics
  source: 'live' | 'demo'
  refreshedAt: string
}
