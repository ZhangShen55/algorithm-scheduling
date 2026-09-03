import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { AlertTriangle, ChevronDown, ChevronLeft, ChevronRight, X } from 'lucide-react'
import { fetchOutboxEvent, fetchOutboxEvents } from './api'
import type { ConsoleConfig, OutboxEvent, OutboxEventFilters, OutboxEventList, OutboxPublishStatus } from './types'

const STATUS_TEXT: Record<OutboxPublishStatus, string> = {
  PENDING: '待发布',
  PUBLISHING: '发布中',
  RETRY_PENDING: '失败待重试',
  PUBLISHED: 'Broker 已确认',
}

export function KafkaEvents({ config }: { config: ConsoleConfig }) {
  const [filters, setFilters] = useState<OutboxEventFilters>({ order: 'desc' })
  const [page, setPage] = useState(1)
  const [value, setValue] = useState<OutboxEventList | null>(null)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<OutboxEvent | null>(null)
  const [payloadOpen, setPayloadOpen] = useState(false)

  const load = useCallback(async () => {
    try {
      setValue(await fetchOutboxEvents(page, 20, filters, config))
      setError('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '任务发布记录读取失败')
    }
  }, [config, filters, page])

  useEffect(() => { void load() }, [load])

  function submit(event: FormEvent) { event.preventDefault(); setPage(1); void load() }
  async function togglePayload(event: OutboxEvent) {
    if (payloadOpen && selected?.event_id === event.event_id) { setPayloadOpen(false); return }
    setSelected(event)
    setPayloadOpen(true)
    if (event.payload) return
    try { setSelected(await fetchOutboxEvent(event.event_id, config)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '事件 payload 读取失败') }
  }

  return <div className="panel full-panel kafka-events">
    <div className="panel-title"><div><h2>任务发布记录</h2><span className="panel-subtitle">课程任务 PostgreSQL Outbox</span></div><span>{value?.total || 0} 条</span></div>
    <form className="event-filter-bar" onSubmit={submit}>
      <label>Task ID<input value={filters.taskIdLike || ''} onChange={(event) => setFilters((current) => ({ ...current, taskIdLike: event.target.value }))} placeholder="模糊查询" /></label>
      <label>事件类型<input value={filters.eventType || ''} onChange={(event) => setFilters((current) => ({ ...current, eventType: event.target.value }))} placeholder="COURSE_TASK_REQUESTED" /></label>
      <label>发布状态<select value={filters.publishStatus || ''} onChange={(event) => setFilters((current) => ({ ...current, publishStatus: event.target.value as OutboxPublishStatus || undefined }))}><option value="">全部</option>{Object.entries(STATUS_TEXT).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
      <label>创建时间从<input type="datetime-local" value={filters.createdFrom || ''} onChange={(event) => setFilters((current) => ({ ...current, createdFrom: event.target.value || undefined }))} /></label>
      <label>到<input type="datetime-local" value={filters.createdTo || ''} onChange={(event) => setFilters((current) => ({ ...current, createdTo: event.target.value || undefined }))} /></label>
      <button type="submit">查询</button><button type="button" className="clear-filter" onClick={() => { setFilters({ order: 'desc' }); setPage(1) }}><X size={14} />清除</button>
    </form>
    {error && <div className="task-list-error"><AlertTriangle size={14} />发布记录局部不可用：{error}</div>}
    <div className="event-table-wrap"><table><thead><tr><th>Task ID</th><th>任务类型</th><th>事件</th><th>发布状态</th><th>创建时间</th><th>Broker 确认时间</th><th>尝试</th><th /></tr></thead><tbody>{(value?.items || []).map((event) => <tr key={event.event_id}><td><strong>{event.task_id || '-'}</strong></td><td>{event.task_type || '-'}</td><td>{event.event_type}</td><td><span className={`pill event-${event.publish_status.toLowerCase()}`}>{STATUS_TEXT[event.publish_status]}</span></td><td>{formatDate(event.created_at)}</td><td>{formatDate(event.published_at)}</td><td>{event.publish_attempts}</td><td><button className="icon-button" onClick={() => void togglePayload(event)} aria-label="查看发布内容" title="查看发布内容"><ChevronDown size={15} /></button></td></tr>)}</tbody></table></div>
    <div className="task-list-footer"><span>“Broker 已确认”仅表示 Kafka Broker 已确认写入，不代表下游消费完成。</span><div className="task-page-actions"><button className="icon-button" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="上一页"><ChevronLeft size={15} /></button><b>{page} / {Math.max(1, value?.total_pages || 0)}</b><button className="icon-button" disabled={page >= Math.max(1, value?.total_pages || 0)} onClick={() => setPage(page + 1)} aria-label="下一页"><ChevronRight size={15} /></button></div></div>
    {payloadOpen && selected && <div className="event-payload"><div><strong>发布内容</strong><button className="icon-button" onClick={() => setPayloadOpen(false)} aria-label="关闭发布内容"><X size={15} /></button></div><pre>{selected.payload ? JSON.stringify(selected.payload, null, 2) : '读取中'}</pre></div>}
  </div>
}

function formatDate(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
