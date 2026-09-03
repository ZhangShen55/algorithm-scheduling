import type { ConsoleData, OperationsTrendPoint } from './types'

function queueCount(data: ConsoleData, status: number): number {
  return data.queues.queues.filter((item) => item.status === status).reduce((sum, item) => sum + item.count, 0)
}

function pointFromData(data: ConsoleData, previous?: OperationsTrendPoint): OperationsTrendPoint {
  const sampledAt = data.refreshedAt
  const elapsedSeconds = previous ? Math.max(1, (new Date(sampledAt).getTime() - new Date(previous.sampledAt).getTime()) / 1000) : 1
  const requestDelta = previous ? Math.max(0, data.gateway.requestTotal - previous.requestTotal) : 0
  const errorDelta = previous ? Math.max(0, data.gateway.errorTotal - previous.errorTotal) : 0
  return {
    sampledAt,
    requestTotal: data.gateway.requestTotal,
    errorTotal: data.gateway.errorTotal,
    requestRate: requestDelta / elapsedSeconds,
    errorRate: errorDelta / elapsedSeconds,
    p95LatencyMs: Math.round(data.gateway.p95LatencyMs),
    queuePending: queueCount(data, 10),
    queueProcessing: queueCount(data, 20),
    queueWaiting: queueCount(data, 30),
  }
}

export function initialTrend(data: ConsoleData): OperationsTrendPoint[] {
  if (data.source === 'live') return [pointFromData(data)]
  const now = new Date(data.refreshedAt).getTime()
  const currentPending = queueCount(data, 10)
  const currentProcessing = queueCount(data, 20)
  const currentWaiting = queueCount(data, 30)
  return Array.from({ length: 18 }, (_, index) => {
    const progress = index / 17
    const wave = Math.sin(index * 0.8)
    return {
      sampledAt: new Date(now - (17 - index) * 60_000).toISOString(),
      requestTotal: data.gateway.requestTotal - Math.round((17 - index) * 420),
      errorTotal: Math.max(0, data.gateway.errorTotal - Math.round((17 - index) * 0.9)),
      requestRate: Math.max(0, 31 + progress * 11 + wave * 5),
      errorRate: Math.max(0, 0.08 + (index % 6 === 0 ? 0.18 : 0.03)),
      p95LatencyMs: Math.max(120, Math.round(data.gateway.p95LatencyMs * (0.72 + progress * 0.22) + wave * 55)),
      queuePending: Math.max(0, Math.round(currentPending + (1 - progress) * 8 + wave * 2)),
      queueProcessing: Math.max(0, Math.round(currentProcessing - 2 + progress * 2 - wave)),
      queueWaiting: Math.max(0, Math.round(currentWaiting + (index % 7 === 0 ? 3 : 0) - progress)),
    }
  })
}

export function appendTrend(previous: OperationsTrendPoint[], data: ConsoleData): OperationsTrendPoint[] {
  if (!previous.length) return initialTrend(data)
  const last = previous[previous.length - 1]
  let next = pointFromData(data, last)
  if (data.source === 'demo') {
    const index = previous.length
    const requestRate = Math.max(0, last.requestRate + Math.sin(index * 0.9) * 3.2)
    next = {
      ...next,
      requestTotal: last.requestTotal + Math.round(requestRate * 10),
      errorTotal: last.errorTotal + (index % 8 === 0 ? 1 : 0),
      requestRate,
      errorRate: index % 8 === 0 ? 0.1 : 0,
      p95LatencyMs: Math.max(120, last.p95LatencyMs + Math.round(Math.cos(index * 0.7) * 35)),
      queuePending: Math.max(0, last.queuePending + (index % 3) - 1),
      queueProcessing: Math.max(0, last.queueProcessing + (index % 4 === 0 ? 1 : 0) - (index % 5 === 0 ? 1 : 0)),
      queueWaiting: Math.max(0, last.queueWaiting + (index % 7 === 0 ? 1 : 0) - (index % 9 === 0 ? 1 : 0)),
    }
  }
  return [...previous, next].slice(-30)
}
