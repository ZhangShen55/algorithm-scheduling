import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { AlertTriangle, ArrowDownUp, ChevronDown, ChevronLeft, ChevronRight, Search, X } from 'lucide-react'
import { demoTaskList, fetchTaskEvents, fetchTaskList, fetchTaskResult, fetchTaskSummary, fetchTaskTypeDetail } from './api'
import type { ConsoleConfig, ConsoleData, OutboxEventList, TaskCode, TaskListFilters, TaskListResponse, TaskResultPage, TaskTypeDetail } from './types'

const TASK_TYPES: Array<{ value: TaskCode; label: string }> = [
  { value: 'PPT', label: 'PPT 解析' },
  { value: 'ASR', label: '语音转写' },
  { value: 'TEACHER_BEHAVIOR', label: '教师行为' },
  { value: 'STUDENT_BEHAVIOR', label: '学生行为' },
]
const STATUSES = [
  [10, '待处理'], [20, '等待前置节点'], [30, '等待算子'], [40, '已排队'],
  [50, '处理中'], [60, '已完成'], [70, '处理失败'], [80, '已取消'],
] as const
const RESULT_SECTIONS: Record<string, Array<{ key: string; label: string }>> = {
  PPT_SLICE: [{ key: 'dynamic_segments', label: '疑似视频播放区间' }],
  PPT_OCR: [{ key: 'ocr_pages', label: '逐页识别结果' }],
  ASR_TRANSCRIPTION: [
    { key: 'transcript', label: '完整转写文本' },
    { key: 'segments', label: '分段转写' },
    { key: 'speed_info', label: '速度数据' },
    { key: 'parameters', label: '携带参数' },
  ],
  TEACHER_BEHAVIOR_ANALYSIS: [
    { key: 'scan', label: '扫描摘要' },
    { key: 'behavior_intervals', label: '教师行为区间' },
    { key: 'evidence', label: '证据' },
  ],
  STUDENT_BEHAVIOR_ANALYSIS: [
    { key: 'scan', label: '扫描与趋势摘要' },
    { key: 'behavior_intervals', label: '学生行为区间' },
    { key: 'frames', label: '逐帧结果' },
    { key: 'evidence', label: '证据' },
  ],
}
const REASON_TEXT: Record<string, string> = {
  no_operator_available: '暂无可用算子实例',
  capacity_unavailable: '算子容量不足',
  timeout: '处理超时',
  invalid_input: '输入参数不合法',
  operator_error: '算子执行失败',
  sustained_visual_change: '持续画面变化，疑似播放视频',
}

type Props = { config: ConsoleConfig; source: ConsoleData['source']; autoRefresh: boolean }

export function TaskWorkspace({ config, source, autoRefresh }: Props) {
  const [filters, setFilters] = useState<TaskListFilters>({ taskTypes: [], statusScope: 'overall' })
  const [list, setList] = useState<TaskListResponse | null>(() => source === 'demo' ? demoTaskList() : null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [customPageSize, setCustomPageSize] = useState('30')
  const [sortBy, setSortBy] = useState<TaskListResponse['sort_by']>('updated_at')
  const [order, setOrder] = useState<TaskListResponse['order']>('desc')
  const [jumpPage, setJumpPage] = useState('1')
  const [listError, setListError] = useState('')
  const [listLoading, setListLoading] = useState(false)
  const listVersion = useRef(0)
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState<TaskListResponse | null>(null)
  const [querying, setQuerying] = useState(false)
  const [selectedId, setSelectedId] = useState('')
  const [details, setDetails] = useState<TaskTypeDetail[]>([])
  const [timeline, setTimeline] = useState<OutboxEventList | null>(null)
  const [detailError, setDetailError] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [results, setResults] = useState<Record<string, TaskResultPage>>({})
  const [resultErrors, setResultErrors] = useState<Record<string, string>>({})

  const loadList = useCallback(async () => {
    const version = ++listVersion.current
    setListLoading(true)
    try {
      const value = source === 'demo'
        ? demoTaskList(page, pageSize, sortBy, order)
        : await fetchTaskList(page, pageSize, sortBy, order, config, filters)
      if (version !== listVersion.current) return
      setList(value)
      setListError('')
    } catch (reason) {
      if (version !== listVersion.current) return
      setListError(reason instanceof Error ? reason.message : '课程任务列表读取失败')
    } finally {
      if (version === listVersion.current) setListLoading(false)
    }
  }, [config, filters, order, page, pageSize, sortBy, source])

  const loadDetail = useCallback(async (taskId: string, quiet = false) => {
    if (!quiet) setDetailError('')
    try {
      const summary = await fetchTaskSummary(taskId, config)
      const requested = summary.tasks.map((item) => item.task_type as TaskCode)
      const [taskDetails, events] = await Promise.all([
        Promise.all(requested.map((taskType) => fetchTaskTypeDetail(taskId, taskType, config))),
        fetchTaskEvents(taskId, config).catch(() => null),
      ])
      setSelectedId(taskId)
      setDetails(taskDetails)
      setTimeline(events)
    } catch (reason) {
      setDetailError(reason instanceof Error ? reason.message : '任务详情读取失败')
    }
  }, [config])

  useEffect(() => { void loadList() }, [loadList])
  useEffect(() => {
    if (!autoRefresh) return
    const timer = window.setInterval(() => {
      void loadList()
      if (selectedId) void loadDetail(selectedId, true)
    }, config.refreshSeconds * 1000)
    return () => window.clearInterval(timer)
  }, [autoRefresh, config.refreshSeconds, loadDetail, loadList, selectedId])
  useEffect(() => { setJumpPage(String(page)) }, [page])

  const totalPages = Math.max(1, list?.total_pages || 0)
  const filterKey = useMemo(() => JSON.stringify(filters), [filters])
  useEffect(() => { setPage(1) }, [filterKey])

  function patchFilters(patch: Partial<TaskListFilters>) {
    setFilters((current) => ({ ...current, ...patch }))
  }

  function toggleTaskType(value: TaskCode) {
    patchFilters({ taskTypes: filters.taskTypes.includes(value) ? filters.taskTypes.filter((item) => item !== value) : [...filters.taskTypes, value] })
  }

  async function fuzzySearch(event: FormEvent) {
    event.preventDefault()
    const value = query.trim()
    if (!value) return
    setQuerying(true)
    try {
      setCandidates(await fetchTaskList(1, 20, 'updated_at', 'desc', config, { taskTypes: [], statusScope: 'overall', taskIdLike: value }))
      setDetailError('')
    } catch (reason) {
      setCandidates(null)
      setDetailError(reason instanceof Error ? reason.message : '模糊查询失败')
    } finally { setQuerying(false) }
  }

  async function toggleResult(taskType: TaskCode, nodeCode: string, section: string) {
    const key = `${selectedId}:${taskType}:${nodeCode}:${section}`
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
    if (results[key]) return
    try {
      const value = await fetchTaskResult(selectedId, taskType, nodeCode, section, 1, 20, config)
      setResults((current) => ({ ...current, [key]: value }))
    } catch (reason) {
      setResultErrors((current) => ({ ...current, [key]: reason instanceof Error ? reason.message : '结果读取失败' }))
    }
  }

  function applyPageSize(value: number) {
    setPageSize(Math.min(100, Math.max(1, Math.trunc(value))))
    setPage(1)
  }

  return <section className="task-workspace">
    <div className="section-heading task-section-heading"><h2>课程任务</h2><span>组合筛选数据库中的课程任务</span></div>
    <div className="panel full-panel task-list-panel">
      <div className="task-filter-bar">
        <fieldset><legend>任务类型（同时满足）</legend><div className="check-filter">{TASK_TYPES.map((item) => <label key={item.value}><input type="checkbox" checked={filters.taskTypes.includes(item.value)} onChange={() => toggleTaskType(item.value)} />{item.label}</label>)}</div></fieldset>
        <label>状态对象<select value={filters.statusScope} onChange={(event) => patchFilters({ statusScope: event.target.value as TaskListFilters['statusScope'], overallStatus: undefined, taskStatus: undefined })}><option value="overall">课程整体</option><option value="task">任务项</option></select></label>
        {filters.statusScope === 'task' && <label>任务项<select value={filters.taskStatusType || ''} onChange={(event) => patchFilters({ taskStatusType: event.target.value as TaskCode || undefined })}><option value="">请选择</option>{TASK_TYPES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>}
        <label>状态<select value={(filters.statusScope === 'overall' ? filters.overallStatus : filters.taskStatus) ?? ''} onChange={(event) => patchFilters(filters.statusScope === 'overall' ? { overallStatus: event.target.value ? Number(event.target.value) : undefined } : { taskStatus: event.target.value ? Number(event.target.value) : undefined })}><option value="">全部</option>{STATUSES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>更新时间从<input type="datetime-local" value={filters.updatedFrom || ''} onChange={(event) => patchFilters({ updatedFrom: event.target.value || undefined })} /></label>
        <label>到<input type="datetime-local" value={filters.updatedTo || ''} onChange={(event) => patchFilters({ updatedTo: event.target.value || undefined })} /></label>
        <label>Task ID<input value={filters.taskIdLike || ''} onChange={(event) => patchFilters({ taskIdLike: event.target.value })} placeholder="支持模糊查询" /></label>
        <button type="button" className="clear-filter" onClick={() => setFilters({ taskTypes: [], statusScope: 'overall' })}><X size={14} />清除</button>
      </div>
      <div className="task-list-toolbar"><div className="task-list-controls"><label>每页<select value={[10, 20, 50, 100].includes(pageSize) ? pageSize : 'custom'} onChange={(event) => event.target.value !== 'custom' && applyPageSize(Number(event.target.value))}><option value="10">10</option><option value="20">20</option><option value="50">50</option><option value="100">100</option><option value="custom">自定义</option></select></label><label>自定义<input type="number" min="1" max="100" value={customPageSize} onChange={(event) => setCustomPageSize(event.target.value)} onBlur={() => applyPageSize(Number(customPageSize) || 10)} /></label><label>排序<select value={sortBy} onChange={(event) => { setSortBy(event.target.value as TaskListResponse['sort_by']); setPage(1) }}><option value="updated_at">最近更新时间</option><option value="created_at">创建时间</option><option value="task_id">Task ID</option></select></label><button type="button" className="sort-direction" onClick={() => { setOrder(order === 'desc' ? 'asc' : 'desc'); setPage(1) }}><ArrowDownUp size={14} />{order === 'desc' ? '降序' : '升序'}</button></div><span className="task-list-total">{listLoading ? '读取中' : listError ? '读取失败' : `共 ${list?.total || 0} 条`}</span></div>
      {listError && <div className="task-list-error"><AlertTriangle size={14} />{listError}</div>}
      <TaskTable value={list} onSelect={(id) => void loadDetail(id)} />
      <Pagination page={page} totalPages={totalPages} total={list?.total || 0} pageSize={pageSize} jumpPage={jumpPage} setJumpPage={setJumpPage} onPage={setPage} />
    </div>

    <div className="section-heading task-section-heading"><h2>查询课程任务</h2><span>模糊查询候选后打开精确详情</span></div>
    <div className="panel task-query"><div className="task-query-label"><Search size={18} /><div><h2>Task ID</h2><span>输入部分编号即可查询</span></div></div><form onSubmit={fuzzySearch}><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如 test_all_0903" /><button disabled={querying}>{querying ? '查询中' : '查询'}</button></form></div>
    {candidates && <div className="panel query-candidates"><div className="panel-title"><h2>匹配结果</h2><span>{candidates.total} 条</span></div><TaskTable value={candidates} onSelect={(id) => void loadDetail(id)} compact /></div>}
    {detailError && <div className="inline-error"><AlertTriangle size={16} />{detailError}</div>}
    {selectedId && <TaskDetail taskId={selectedId} details={details} timeline={timeline} expanded={expanded} results={results} resultErrors={resultErrors} onToggle={toggleResult} />}
  </section>
}

function TaskTable({ value, onSelect, compact = false }: { value: TaskListResponse | null; onSelect: (id: string) => void; compact?: boolean }) {
  return <div className={`task-list-scroll ${compact ? 'compact' : ''}`}><table className="task-list-table"><thead><tr><th>Task ID</th><th>任务类型</th><th>状态</th><th>最近更新时间</th><th /></tr></thead><tbody>{(value?.items || []).map((item) => <tr key={item.task_id} onClick={() => onSelect(item.task_id)}><td><strong>{item.task_id}</strong><small>创建于 {formatDate(item.created_at)}</small></td><td><div className="task-type-tags">{item.tasks.map((task) => <span className="tag" key={task.task_type}>{taskName(task.task_type)}</span>)}</div></td><td><span className={`pill status-${item.status}`}>{item.status_text}</span></td><td>{formatDate(item.updated_at)}</td><td><ChevronRight size={15} /></td></tr>)}</tbody></table>{!value?.items.length && <div className="task-list-empty">暂无任务记录</div>}</div>
}

function Pagination({ page, totalPages, total, pageSize, jumpPage, setJumpPage, onPage }: { page: number; totalPages: number; total: number; pageSize: number; jumpPage: string; setJumpPage: (value: string) => void; onPage: (value: number) => void }) {
  function jump(event: FormEvent) { event.preventDefault(); onPage(Math.min(totalPages, Math.max(1, Number(jumpPage) || 1))) }
  return <div className="task-list-footer"><span>显示 {(total ? (page - 1) * pageSize + 1 : 0)}-{Math.min(page * pageSize, total)} 条</span><div className="task-page-actions"><button className="icon-button" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="上一页"><ChevronLeft size={15} /></button><b>第 {page} / {totalPages} 页</b><button className="icon-button" disabled={page >= totalPages} onClick={() => onPage(page + 1)} aria-label="下一页"><ChevronRight size={15} /></button><form className="task-page-jump" onSubmit={jump}><label>跳转到<input type="number" min="1" max={totalPages} value={jumpPage} onChange={(event) => setJumpPage(event.target.value)} /></label><button>前往</button></form></div></div>
}

function TaskDetail({ taskId, details, timeline, expanded, results, resultErrors, onToggle }: { taskId: string; details: TaskTypeDetail[]; timeline: OutboxEventList | null; expanded: Set<string>; results: Record<string, TaskResultPage>; resultErrors: Record<string, string>; onToggle: (taskType: TaskCode, nodeCode: string, section: string) => void }) {
  return <div className="panel full-panel layered-task-detail"><div className="result-heading"><div><span>课程任务详情</span><h2>{taskId}</h2></div><span>{details.length} 类任务</span></div><div className="task-detail-types">{details.map((task) => <section className="task-type-detail" key={task.task_type}><header><div><strong>{taskName(task.task_type)}</strong><span>{reasonText(task.reason)}</span></div><span className={`pill status-${task.status}`}>{task.status_text}</span></header><div className="node-summary-list">{task.nodes.map((node) => <article key={node.node_code}><div className="node-summary-head"><div><strong>{nodeName(node.node_code)}</strong><span>{reasonText(node.reason)}</span></div><span className={`pill status-${node.status}`}>{node.status_text}</span></div><SummaryGrid node={node} />{(RESULT_SECTIONS[node.node_code || ''] || []).map((section) => { const key = `${taskId}:${task.task_type}:${node.node_code}:${section.key}`; const open = expanded.has(key); return <div className="result-disclosure" key={section.key}><button type="button" aria-expanded={open} onClick={() => onToggle(task.task_type as TaskCode, String(node.node_code), section.key)}><ChevronDown size={15} />{section.label}</button>{open && <div className="result-content">{resultErrors[key] ? <div className="inline-error">{resultErrors[key]}</div> : results[key] ? <ResultValue value={results[key]} /> : <span className="muted">读取中</span>}</div>}</div> })}</article>)}</div></section>)}</div><section className="task-event-timeline"><h3>任务发布记录</h3><p>第一版仅覆盖课程任务 PostgreSQL Outbox；“Broker 已确认”不代表下游消费完成。</p>{timeline?.items.length ? timeline.items.map((event) => <div className="event-row" key={event.event_id}><span className={`event-state ${event.publish_status.toLowerCase()}`} /> <strong>{eventName(event.event_type)}</strong><span>{taskName(event.task_type || '')}</span><b>{publishText(event.publish_status)}</b><time>{formatDate(event.created_at)}</time></div>) : <span className="muted">暂无发布记录或发布记录接口不可用</span>}</section></div>
}

function SummaryGrid({ node }: { node: TaskTypeDetail['nodes'][number] }) {
  const summary = node.result_summary || {}
  const values: Array<[string, unknown]> = [
    ['排队耗时', formatDuration(node.queue_wait_ms)], ['启动耗时', formatDuration(node.startup_ms)],
    ['算子处理耗时', formatDuration(node.processing_duration_ms)], ['节点总耗时', formatDuration(node.total_duration_ms)],
    ...Object.entries(summary).filter(([, value]) => value !== null && value !== undefined).slice(0, 8).map(([key, value]) => [summaryLabel(key), summaryValue(key, value)] as [string, unknown]),
  ]
  return <div className="node-summary-grid">{values.map(([label, value]) => <div key={label}><span>{label}</span><strong>{String(value)}</strong></div>)}</div>
}

function ResultValue({ value }: { value: TaskResultPage }) {
  const result = value.results[0]
  if (!result) return <span className="muted">暂无结果</span>
  return <><div className="result-meta">{result.total !== undefined ? `共 ${result.total} 项，本次 ${result.items?.length || 0} 项` : '按需读取结果'}</div><pre>{JSON.stringify(displayResult(value.section, result.items ?? result.value ?? null), null, 2)}</pre></>
}

function taskName(value: string) { return TASK_TYPES.find((item) => item.value === value)?.label || value }
function nodeName(value?: string) { const names: Record<string, string> = { PPT_SLICE: 'PPT 切片', PPT_OCR: 'PPT OCR', ASR_TRANSCRIPTION: '语音转写', TEACHER_BEHAVIOR_ANALYSIS: '教师行为分析', STUDENT_BEHAVIOR_ANALYSIS: '学生行为分析' }; return names[value || ''] || value || '任务节点' }
function reasonText(value?: string | null) { if (!value) return '暂无说明'; return REASON_TEXT[value] || value }
function publishText(value: string) { return ({ PENDING: '待发布', PUBLISHING: '发布中', RETRY_PENDING: '失败待重试', PUBLISHED: 'Broker 已确认' } as Record<string, string>)[value] || value }
function eventName(value: string) { return value === 'COURSE_TASK_REQUESTED' ? '课程任务已写入 Outbox' : value }
function formatDate(value?: string | null) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '无精确时间' }
function formatDuration(value?: number | null) { if (value === null || value === undefined) return '无精确耗时'; const seconds = Math.floor(value / 1000); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` }
function formatRelativeSeconds(value: unknown) { const seconds = Number(value); if (!Number.isFinite(seconds)) return value; return `${String(Math.floor(seconds / 3600)).padStart(2, '0')}:${String(Math.floor(seconds % 3600 / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}` }
function displayResult(section: string, value: unknown): unknown {
  if (!Array.isArray(value)) return value
  return value.map((item) => {
    if (!item || typeof item !== 'object') return item
    const record = item as Record<string, unknown>
    if (section === 'dynamic_segments') return { ...record, start_time: formatRelativeSeconds(Number(record.start_ms) / 1000), end_time: formatRelativeSeconds(Number(record.end_ms) / 1000), reason_text: reasonText(String(record.reason || '')) }
    if (section === 'behavior_intervals') return { ...record, start_time: formatRelativeSeconds(record.start_seconds), end_time: formatRelativeSeconds(record.end_seconds) }
    if (section === 'segments') return { ...record, begin_time: formatRelativeSeconds(record.bg), end_time: formatRelativeSeconds(record.ed) }
    return record
  })
}
function summaryValue(key: string, value: unknown) { return key.includes('duration_seconds') ? formatRelativeSeconds(value) : key.endsWith('_ms') ? formatDuration(Number(value)) : value }
function summaryLabel(key: string) { const labels: Record<string, string> = { completed_count: '已完成', total_count: '总数', slice_count: '切片页数', dynamic_segment_count: '疑似视频段数', dynamic_duration_ms: '疑似视频总时长', page_count: '识别页数', success_count: '成功页数', empty_count: '空结果页数', failed_count: '失败页数', audio_duration_seconds: '音频时长', language: '语种', segment_count: '分段数', text_length: '文本长度', duration_seconds: '视频时长', analysis_quality: '分析质量', total_frame_count: '总帧数', valid_frame_count: '有效帧数', valid_frame_ratio: '有效帧率', evidence_count: '证据数', student_count: '配置学生数', stable_person_count: '稳定人数', recognized_total_person_count: '识别人数', attendance_rate: '出勤率', front_occupancy_ratio: '前区占用', back_occupancy_ratio: '后区占用' }; return labels[key] || key }
