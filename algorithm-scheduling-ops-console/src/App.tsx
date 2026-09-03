import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Activity, AlertTriangle, ArrowDownUp, ArrowUpRight, Boxes, CheckCircle2, ChevronLeft, ChevronRight, CircleDot, Clock3, Cpu, Database, Gauge, LayoutDashboard, ListFilter, Menu, Network, Palette, PanelLeftClose, PanelLeftOpen, RefreshCw, Search, Server, SlidersHorizontal, Wifi, X } from 'lucide-react'
import { defaultConsoleConfig, demoActiveLeases, demoData, demoTask, demoTaskList, emptyConsoleData, fetchActiveLeases, fetchConsoleData, fetchGpuMetrics, fetchTask, fetchTaskList, loadConsoleConfig, saveConsoleConfig } from './api'
import { appendTrend, initialTrend } from './trend'
import type { ActiveLeaseResponse, CapacitySnapshot, ConsoleConfig, ConsoleData, GpuMetrics, OperationsTrendPoint, OperatorInstance, TaskDetail, TaskListResponse, VisualStyle } from './types'

const operatorNames: Record<string, string> = { vbas: 'VBAS 行为分析', ocr: 'OCR 文字识别', facerec: 'FaceRec 人脸', screen_det: 'ScreenDet 画质', asr_offline: 'ASR 离线', asr_online: 'ASR 在线', ppt_slice: 'PPT 切片' }
const taskNames: Record<string, string> = { PPT: 'PPT 解析', ASR: '语音转写', TEACHER_BEHAVIOR: '教师行为', STUDENT_BEHAVIOR: '学生行为' }
const fmt = new Intl.NumberFormat('zh-CN')
const QueueTrendChart = lazy(() => import('./charts').then((module) => ({ default: module.QueueTrendChart })))
const GatewayTrendChart = lazy(() => import('./charts').then((module) => ({ default: module.GatewayTrendChart })))

type View = 'overview' | 'instances' | 'tasks' | 'gateway' | 'system'
type TaskSortField = 'flow' | 'task_type' | 'status' | 'updated_at'

const visualStyles: { id: VisualStyle; name: string; code: string; description: string }[] = [
  { id: 'industrial', name: '日间模式', code: 'A', description: '明亮的设备巡检工作台' },
  { id: 'command', name: '深色模式', code: 'B', description: '深色的持续值守界面' },
]
const STYLE_KEY = 'algorithm-scheduling-ops-console-style'
const LEGACY_STYLE_KEY = 'ops-console-style'

function initialVisualStyle(): VisualStyle {
  const queryStyle = new URLSearchParams(window.location.search).get('style')
  const storedStyle = window.localStorage.getItem(STYLE_KEY) || window.localStorage.getItem(LEGACY_STYLE_KEY)
  const candidate = queryStyle || storedStyle
  return visualStyles.some((item) => item.id === candidate) ? candidate as VisualStyle : 'industrial'
}

function App() {
  const demoMode = new URLSearchParams(window.location.search).get('demo') === '1'
  const [data, setData] = useState<ConsoleData>(() => demoMode ? demoData() : emptyConsoleData())
  const [view, setView] = useState<View>('overview')
  const [visualStyle, setVisualStyle] = useState<VisualStyle>(initialVisualStyle)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [styleMenuOpen, setStyleMenuOpen] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [consoleConfig, setConsoleConfig] = useState<ConsoleConfig>(loadConsoleConfig)
  const [trendPoints, setTrendPoints] = useState<OperationsTrendPoint[]>([])
  const trendSourceRef = useRef<ConsoleData['source']>(data.source)
  const [loading, setLoading] = useState(!demoMode)
  const [error, setError] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [instanceFilter, setInstanceFilter] = useState('all')
  const [selectedInstance, setSelectedInstance] = useState<OperatorInstance | null>(null)
  const [activeLeases, setActiveLeases] = useState<ActiveLeaseResponse | null>(null)
  const [leaseLoading, setLeaseLoading] = useState(false)
  const [leaseError, setLeaseError] = useState('')
  const [taskInput, setTaskInput] = useState('')
  const [task, setTask] = useState<TaskDetail | null>(null)
  const [taskLoading, setTaskLoading] = useState(false)
  const [taskError, setTaskError] = useState('')
  const taskRequestVersion = useRef(0)
  const gpuRequestVersion = useRef(0)

  const refreshVersion = useRef(0)
  const refresh = useCallback(async () => {
    const version = ++refreshVersion.current
    if (demoMode) { setData(demoData()); setError(''); setLoading(false); return }
    setLoading(true)
    try {
      const next = await fetchConsoleData(consoleConfig)
      if (version !== refreshVersion.current) return
      setData(next)
      setError('')
    } catch (reason) {
      if (version !== refreshVersion.current) return
      setData(emptyConsoleData())
      setError(`实时接口读取失败：${reason instanceof Error ? reason.message : '请检查 Control Service、网关地址、端口或 CORS'}`)
    } finally { if (version === refreshVersion.current) setLoading(false) }
  }, [consoleConfig, demoMode])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    setTrendPoints((points) => {
      if (trendSourceRef.current !== data.source) {
        trendSourceRef.current = data.source
        return initialTrend(data)
      }
      return points.length ? appendTrend(points, data) : initialTrend(data)
    })
  }, [data.refreshedAt, data.source])
  useEffect(() => {
    window.localStorage.setItem(STYLE_KEY, visualStyle)
    const url = new URL(window.location.href)
    url.searchParams.set('style', visualStyle)
    window.history.replaceState(null, '', url)
  }, [visualStyle])
  useEffect(() => { setMobileNavOpen(false) }, [view])
  useEffect(() => {
    if (!autoRefresh) return
    const timer = window.setInterval(() => { void refresh() }, consoleConfig.refreshSeconds * 1000)
    return () => window.clearInterval(timer)
  }, [autoRefresh, consoleConfig.refreshSeconds, refresh])

  const refreshGpu = useCallback(async () => {
    const version = ++gpuRequestVersion.current
    try {
      const gpu = await fetchGpuMetrics(consoleConfig)
      if (version !== gpuRequestVersion.current) return
      setData((current) => ({ ...current, gpu }))
    } catch {
      if (version !== gpuRequestVersion.current) return
      setData((current) => ({ ...current, gpu: { status: 'unavailable', sampled_at: Date.now() / 1000, devices: [], error: 'GPU 指标接口暂不可用' } }))
    }
  }, [consoleConfig])

  useEffect(() => {
    if (!autoRefresh) return
    const timer = window.setInterval(() => { void refreshGpu() }, consoleConfig.gpuRefreshSeconds * 1000)
    return () => window.clearInterval(timer)
  }, [autoRefresh, consoleConfig.gpuRefreshSeconds, refreshGpu])

  useEffect(() => {
    if (!selectedInstance) { setActiveLeases(null); return }
    let cancelled = false
    const loadLeases = async () => {
      setLeaseLoading(true)
      try {
        const result = await fetchActiveLeases(selectedInstance.instance_id, consoleConfig)
        if (!cancelled) { setActiveLeases(result); setLeaseError('') }
      } catch {
        if (!cancelled) {
          if (data.source === 'demo') setActiveLeases(demoActiveLeases(selectedInstance.instance_id))
          else { setActiveLeases(null); setLeaseError('当前实例租约暂不可用') }
        }
      } finally { if (!cancelled) setLeaseLoading(false) }
    }
    void loadLeases()
    if (!autoRefresh) return () => { cancelled = true }
    const timer = window.setInterval(() => { void loadLeases() }, consoleConfig.leaseRefreshSeconds * 1000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [selectedInstance, data.source, autoRefresh, consoleConfig])

  const totals = useMemo(() => {
    const online = data.instances.filter((item) => item.lifecycle === 'ONLINE').length
    const ready = data.instances.filter((item) => item.model_ready).length
    const capacity = data.snapshots.reduce((sum, item) => sum + item.declared_capacity, 0)
    const used = data.snapshots.reduce((sum, item) => sum + item.schedulable_used, 0)
    const waiting = data.queues.queues.filter((item) => item.status === 30).reduce((sum, item) => sum + item.count, 0)
    return { online, ready, capacity, used, waiting }
  }, [data])

  const filteredInstances = data.instances.filter((instance) => instanceFilter === 'all' || instance.operator_code === instanceFilter)

  async function searchTask(event?: FormEvent, requestedId?: string) {
    event?.preventDefault()
    const taskId = (requestedId || taskInput).trim()
    if (!taskId) return
    const version = ++taskRequestVersion.current
    setTaskLoading(true); setTaskError(''); setTask(null)
    try {
      const result = await fetchTask(taskId, consoleConfig)
      if (version !== taskRequestVersion.current) return
      setTask(result)
      setView('tasks')
    }
    catch { if (version !== taskRequestVersion.current) return; if (data.source === 'demo') setTask(demoTask(taskId)); else setTaskError('未找到任务，或任务查询接口暂不可用') }
    finally { if (version === taskRequestVersion.current) setTaskLoading(false) }
  }

  return <div className={`shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`} data-style={visualStyle}>
    <aside className={`sidebar ${mobileNavOpen ? 'mobile-open' : ''}`}>
      <div className="brand"><div className="brand-mark"><Activity size={20} /></div><div className="brand-copy"><strong>算法调度</strong></div><button className="sidebar-collapse icon-button" onClick={() => setSidebarCollapsed((collapsed) => !collapsed)} aria-label={sidebarCollapsed ? '展开平台工作区' : '收缩平台工作区'} title={sidebarCollapsed ? '展开平台工作区' : '收缩平台工作区'}>{sidebarCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}</button><button className="sidebar-close icon-button" onClick={() => setMobileNavOpen(false)} aria-label="关闭菜单"><X size={18} /></button></div>
      <div className="workspace-label">平台工作区</div>
      <nav className="nav-list">
        <NavItem icon={<LayoutDashboard size={17} />} label="运行总览" active={view === 'overview'} onClick={() => setView('overview')} />
        <NavItem icon={<Boxes size={17} />} label="算子实例" count={data.instances.length} active={view === 'instances'} onClick={() => setView('instances')} />
        <NavItem icon={<ListFilter size={17} />} label="任务追踪" active={view === 'tasks'} onClick={() => setView('tasks')} />
        <NavItem icon={<Network size={17} />} label="网关流量" active={view === 'gateway'} onClick={() => setView('gateway')} />
        <NavItem icon={<Server size={17} />} label="系统状态" active={view === 'system'} onClick={() => setView('system')} />
      </nav>
    </aside>
    {mobileNavOpen && <button className="mobile-nav-backdrop" onClick={() => setMobileNavOpen(false)} aria-label="关闭菜单遮罩" />}
    <main className="main">
      <header className="topbar"><button className="mobile-menu icon-button" onClick={() => setMobileNavOpen((open) => !open)} aria-label="打开菜单" aria-expanded={mobileNavOpen}><Menu size={19} /></button><div className="breadcrumb">平台 / <b>{view === 'overview' ? '运行总览' : view === 'instances' ? '算子实例' : view === 'tasks' ? '任务追踪' : view === 'gateway' ? '网关流量' : '系统状态'}</b></div><div className="top-actions"><div className="style-picker"><button className={`style-trigger ${styleMenuOpen ? 'active' : ''}`} onClick={() => setStyleMenuOpen((open) => !open)} aria-label="切换界面风格" aria-expanded={styleMenuOpen}><Palette size={15} /><span>{visualStyles.find((item) => item.id === visualStyle)?.name}</span><ChevronRight size={13} /></button>{styleMenuOpen && <StyleMenu value={visualStyle} onChange={(style) => { setVisualStyle(style); setStyleMenuOpen(false) }} onClose={() => setStyleMenuOpen(false)} />}</div><button className={`icon-button ${configOpen ? 'active' : ''}`} onClick={() => setConfigOpen(true)} aria-label="连接与观测配置" title="连接与观测配置"><SlidersHorizontal size={17} /></button><button className={`icon-button ${loading ? 'spinning' : ''}`} onClick={() => void refresh()} aria-label="刷新数据" title="刷新数据"><RefreshCw size={17} /></button><label className="refresh-toggle"><input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} /><span>自动刷新</span></label></div></header>
      {error && <div className="notice"><AlertTriangle size={15} />{error}<button onClick={() => setError('')} aria-label="关闭提示"><X size={15} /></button></div>}
      <div className="content">
        <section className="page-heading"><div><h1>{view === 'overview' ? '运行总览' : view === 'instances' ? '算子实例' : view === 'tasks' ? '任务追踪' : view === 'gateway' ? '网关流量' : '系统状态'}</h1><p>采样于 {formatTime(data.refreshedAt)} <span className={`source-chip ${error ? 'error' : data.source}`}>{error ? '读取失败' : data.source === 'live' ? '实时数据' : '演示数据'}</span></p></div><div className="heading-meta"><span>数据窗口</span><strong>{data.source === 'live' ? '会话采样' : '演示窗口'}</strong></div></section>
        {view === 'overview' && <Overview data={data} totals={totals} trendPoints={trendPoints} visualStyle={visualStyle} onInstance={(item) => setSelectedInstance(item)} onView={setView} />}
        {view === 'instances' && <Instances instances={data.instances} snapshots={data.snapshots} gpu={data.gpu} filter={instanceFilter} setFilter={setInstanceFilter} onSelect={setSelectedInstance} />}
        {view === 'tasks' && <Tasks input={taskInput} setInput={setTaskInput} onSearch={searchTask} loading={taskLoading} task={task} error={taskError} config={consoleConfig} source={data.source} autoRefresh={autoRefresh} onSelectTask={(id) => { setTaskInput(id); void searchTask(undefined, id) }} />}
        {view === 'gateway' && <Gateway gateway={data.gateway} trendPoints={trendPoints} visualStyle={visualStyle} source={data.source} />}
        {view === 'system' && <System data={data} />}
      </div>
    </main>
    {configOpen && <ConsoleConfigPanel value={consoleConfig} onSave={(next) => { setConsoleConfig(saveConsoleConfig(next)); setConfigOpen(false) }} onReset={() => setConsoleConfig(saveConsoleConfig(defaultConsoleConfig()))} onClose={() => setConfigOpen(false)} />}
    {selectedInstance && <InstanceDrawer instance={selectedInstance} snapshot={data.snapshots.find((item) => item.instance_id === selectedInstance.instance_id)} activeLeases={activeLeases} leaseLoading={leaseLoading} leaseError={leaseError} onClose={() => setSelectedInstance(null)} />}
  </div>
}

function ConsoleConfigPanel({ value, onSave, onReset, onClose }: { value: ConsoleConfig; onSave: (value: ConsoleConfig) => void; onReset: () => void; onClose: () => void }) {
  const [draft, setDraft] = useState(value)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => { setDraft(value) }, [value])

  async function testConnection() {
    setTesting(true)
    setTestResult(null)
    try {
      const result = await fetchConsoleData(draft)
      setTestResult({ ok: true, text: `读取成功 · ${result.instances.length} 个实例 · ${result.source === 'live' ? '实时接口' : '演示数据'}` })
    } catch {
      setTestResult({ ok: false, text: '读取失败 · 请检查地址、端口、服务状态或浏览器 CORS' })
    } finally { setTesting(false) }
  }

  function update(field: keyof ConsoleConfig, rawValue: string) {
    setDraft((current) => ({ ...current, [field]: field.includes('Seconds') ? Number(rawValue) : rawValue }))
    setTestResult(null)
  }

  return <div className="drawer-backdrop" onClick={onClose}><aside className="drawer config-drawer" onClick={event => event.stopPropagation()}><div className="drawer-head"><div><h2>连接与观测配置</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭配置"><X size={18} /></button></div><div className="config-intro">配置仅保存在当前浏览器，用于决定页面从哪些只读接口读取数据，不会修改 Control Service、网关、Kafka 或算子运行配置。</div><form className="config-form" onSubmit={(event) => { event.preventDefault(); onSave(draft) }}><label><span>Control Service 访问地址（协议 / IP / 端口）</span><input value={draft.controlBaseUrl} onChange={(event) => update('controlBaseUrl', event.target.value)} placeholder="http://192.168.29.11:18100" required /><small>实例、容量、队列、任务、Kafka 发布指标和系统状态来源</small></label><label><span>gateway-online 访问地址（协议 / IP / 端口）</span><input value={draft.gatewayBaseUrl} onChange={(event) => update('gatewayBaseUrl', event.target.value)} placeholder="http://192.168.29.11:18103" required /><small>网关 `/metrics` 请求、错误、延迟和容量指标来源</small></label><label><span>GPU 指标容器地址（协议 / IP / 端口）</span><input value={draft.gpuBaseUrl} onChange={(event) => update('gpuBaseUrl', event.target.value)} placeholder="http://192.168.29.11:9400" required /><small>独立 GPU exporter 的 `/gpu` 接口，读取整机显卡状态</small></label><div className="config-grid"><label><span>总览刷新周期（秒）</span><input type="number" min="1" max="60" step="1" value={draft.refreshSeconds} onChange={(event) => update('refreshSeconds', event.target.value)} /><small>建议 10 秒，范围 1～60 秒</small></label><label><span>实例任务刷新周期（秒）</span><input type="number" min="1" max="30" step="1" value={draft.leaseRefreshSeconds} onChange={(event) => update('leaseRefreshSeconds', event.target.value)} /><small>建议 5 秒，范围 1～30 秒</small></label><label><span>GPU 刷新周期（秒）</span><input type="number" min="1" max="30" step="1" value={draft.gpuRefreshSeconds} onChange={(event) => update('gpuRefreshSeconds', event.target.value)} /><small>建议 5 秒，范围 1～30 秒</small></label></div>{testResult && <div className={`config-result ${testResult.ok ? 'success' : 'failure'}`}><span className="health-dot" />{testResult.text}</div>}<div className="config-actions"><button type="button" className="secondary-button" onClick={onReset}>恢复默认</button><button type="button" className="secondary-button" onClick={() => void testConnection()} disabled={testing}>{testing ? '读取中...' : '测试读取'}</button><button type="submit" className="primary-button">保存并应用</button></div></form><div className="config-note"><strong>当前部署方式</strong><p>内部工具可以直接填写 `http://IP:端口`。Control Service、online-gateway-service 和 GPU exporter 需要允许当前页面来源的跨域请求；也可以填写同源代理路径。</p></div><div className="config-boundary"><span className="pill online">只读</span><span>不会调用注册、心跳、租约、任务发布、排空、恢复上线或重启接口。</span></div></aside></div>
}

function StyleMenu({ value, onChange, onClose }: { value: VisualStyle; onChange: (style: VisualStyle) => void; onClose: () => void }) {
  return <div className="style-menu"><div className="style-menu-head"><div><strong>界面风格</strong><span>功能与数据完全一致</span></div><button onClick={onClose} aria-label="关闭风格选择"><X size={15} /></button></div><div className="style-options">{visualStyles.map((item) => <button key={item.id} className={`style-option ${value === item.id ? 'selected' : ''}`} onClick={() => onChange(item.id)}><span className={`style-swatch swatch-${item.id}`}><i>{item.code}</i><b /><b /><b /></span><span><strong>{item.name}</strong><small>{item.description}</small></span>{value === item.id && <CheckCircle2 size={16} />}</button>)}</div></div>
}

function NavItem({ icon, label, count, active, onClick }: { icon: ReactNode; label: string; count?: number; active: boolean; onClick: () => void }) { return <button className={`nav-item ${active ? 'active' : ''}`} onClick={onClick}>{icon}<span>{label}</span>{count !== undefined && <small>{count}</small>}<ChevronRight className="nav-arrow" size={14} /></button> }

function ChartFallback({ className }: { className: string }) { return <div className={`chart-loading ${className}`}><span>图表加载中</span></div> }

function Overview({ data, totals, trendPoints, visualStyle, onInstance, onView }: { data: ConsoleData; totals: { online: number; ready: number; capacity: number; used: number; waiting: number }; trendPoints: OperationsTrendPoint[]; visualStyle: VisualStyle; onInstance: (instance: OperatorInstance) => void; onView: (view: View) => void }) {
  const loadPercent = totals.capacity ? Math.round(totals.used / totals.capacity * 100) : 0
  const gpuBusy = data.gpu.devices.length ? Math.round(data.gpu.devices.reduce((sum, device) => sum + device.utilization_percent, 0) / data.gpu.devices.length) : 0
  return <>
    <section className="kpi-grid"><Kpi icon={<Server />} label="在线实例" value={`${totals.online} / ${data.instances.length}`} detail={`${totals.ready} 个模型就绪`} tone="teal" /><Kpi icon={<Gauge />} label="调度容量使用" value={`${loadPercent}%`} detail={`${fmt.format(totals.used)} / ${fmt.format(totals.capacity)} 槽位`} tone="blue" meter={loadPercent} /><Kpi icon={<Clock3 />} label="等待调度" value={fmt.format(totals.waiting)} detail="容量不足队列" tone={totals.waiting ? 'orange' : 'teal'} /><Kpi icon={<Wifi />} label="网关请求" value={fmt.format(data.gateway.requestTotal)} detail={`${data.gateway.errorTotal} 次异常 · 5 分钟`} tone="purple" /><Kpi icon={<Cpu />} label="GPU 平均利用率" value={`${gpuBusy}%`} detail={`${data.gpu.devices.length} 张显卡 · ${data.gpu.status === 'ok' ? '实时采样' : '不可用'}`} tone="blue" /></section>
    <section className="overview-grid"><div className="panel capacity-panel"><PanelTitle title="容量与实例健康" action="查看全部" onAction={() => onView('instances')} /><div className="capacity-list">{groupInstances(data.instances, data.snapshots).map((group) => <CapacityRow key={group.code} group={group} onInstance={onInstance} />)}</div></div><div className="panel queue-panel"><PanelTitle title="调度队列" action="任务追踪" onAction={() => onView('tasks')} /><div className="queue-summary compact"><div><strong>{fmt.format(data.queues.outbox_pending)}</strong><span>Outbox 待发布</span></div><span className="chart-source">{data.source === 'live' ? '本次会话' : '演示趋势'}</span></div><Suspense fallback={<ChartFallback className="queue-trend-chart" />}><QueueTrendChart points={trendPoints} visualStyle={visualStyle} /></Suspense><div className="queue-list compact">{data.queues.queues.length ? data.queues.queues.map((item) => <div className="queue-row" key={`${item.capability}-${item.status}`}><span className={`status-marker status-${item.status}`} /><span className="queue-name">{item.capability || '未分配'}</span><span className="queue-state">{item.status_text}</span><strong>{item.count}</strong></div>) : <Empty text="当前没有队列积压" />}</div></div></section>
    <section className="lower-grid"><div className="panel gateway-panel"><PanelTitle title="在线网关" action="流量详情" onAction={() => onView('gateway')} /><div className="gateway-head"><div><span className="big-number">{fmt.format(data.gateway.requestTotal)}</span><span className="unit">requests</span></div><span className="trend up"><ArrowUpRight size={15} /> 会话趋势</span></div><Suspense fallback={<ChartFallback className="gateway-trend-chart compact" />}><GatewayTrendChart points={trendPoints} visualStyle={visualStyle} compact /></Suspense><div className="gateway-stats"><span><b>{avgLatency(data.gateway)} ms</b><small>平均耗时</small></span><span><b>{Math.round(data.gateway.p95LatencyMs)} ms</b><small>P95 延迟</small></span><span><b className="danger-text">{data.gateway.errorTotal}</b><small>异常请求</small></span><span><b className="warning-text">{data.gateway.capacityRejected}</b><small>容量拒绝</small></span></div></div><KafkaPanel kafka={data.kafka} onView={onView} /></section>
    <section className="panel gpu-overview-panel"><PanelTitle title="整机 GPU 状态" action="查看实例清单" onAction={() => onView('instances')} /><GpuCards gpu={data.gpu} instances={data.instances} /></section>
  </>
}

function KafkaPanel({ kafka, onView }: { kafka: ConsoleData['kafka']; onView?: (view: View) => void }) { const degraded = kafka.status !== 'ok'; return <div className="panel kafka-panel"><PanelTitle title="任务发布 / Kafka" action={onView ? '系统状态' : undefined} onAction={onView ? () => onView('system') : undefined} /><div className="pipeline-metrics"><MetricValue icon={<Database size={15} />} label="Outbox 待发布" value={kafka.outboxPending} tone="warning" /><MetricValue icon={<ArrowUpRight size={15} />} label="已推送 Kafka" value={kafka.published} tone="success" /><MetricValue icon={<AlertTriangle size={15} />} label="推送失败" value={kafka.publishFailed} tone="danger" /><MetricValue icon={<Activity size={15} />} label="消费者积压" value={kafka.consumerLag} tone="info" /></div><div className="flow compact"><FlowStep label="课程任务" status="写入 Outbox" active /><ChevronRight /><FlowStep label="发布器" status={degraded ? '指标降级' : '推送 Kafka'} active /><ChevronRight /><FlowStep label="消费者" status={degraded ? '不可用' : kafka.consumerLag ? '存在积压' : '正常'} /></div><div className="metric-foot"><span>指标来源</span><code>control-service /ops/kafka</code><span>采样时间</span><b>{formatTime(kafka.sampledAt)}</b></div></div> }

function MetricValue({ icon, label, value, tone }: { icon: ReactNode; label: string; value: number; tone: string }) { return <div className="pipeline-metric"><span className={`pipeline-icon ${tone}`}>{icon}</span><div><strong>{fmt.format(value)}</strong><small>{label}</small></div></div> }

function Kpi({ icon, label, value, detail, tone, meter }: { icon: ReactNode; label: string; value: string; detail: string; tone: string; meter?: number }) { return <div className="kpi"><div className={`kpi-icon ${tone}`}>{icon}</div><div className="kpi-body"><span>{label}</span><strong>{value}</strong><small>{detail}</small>{meter !== undefined && <div className="meter"><i style={{ width: `${Math.min(100, meter)}%` }} /></div>}</div></div> }

function PanelTitle({ title, action, onAction }: { title: string; action?: string; onAction?: () => void }) { return <div className="panel-title"><h2>{title}</h2>{action && <button onClick={onAction}>{action}<ChevronRight size={14} /></button>}</div> }

function groupInstances(instances: OperatorInstance[], snapshots: CapacitySnapshot[]) { return Object.values(instances.reduce<Record<string, { code: string; name: string; instances: OperatorInstance[]; capacity: number; used: number }>>((acc, instance) => { const snapshot = snapshots.find(item => item.instance_id === instance.instance_id); const existing = acc[instance.operator_code] || { code: instance.operator_code, name: operatorNames[instance.operator_code] || instance.operator_code, instances: [], capacity: 0, used: 0 }; existing.instances.push(instance); existing.capacity += instance.declared_capacity; existing.used += snapshot?.schedulable_used || instance.inflight; acc[instance.operator_code] = existing; return acc }, {})) }

function CapacityRow({ group, onInstance }: { group: ReturnType<typeof groupInstances>[number]; onInstance: (instance: OperatorInstance) => void }) { const percent = group.capacity ? Math.round(group.used / group.capacity * 100) : 0; return <div className="capacity-row"><div className="capacity-label"><span className="operator-badge">{group.code.slice(0, 2).toUpperCase()}</span><div><strong>{group.name}</strong><small>{group.code} · {group.instances.length} 实例</small></div></div><div className="capacity-track"><i style={{ width: `${Math.min(100, percent)}%` }} /><span>{fmt.format(group.used)} / {fmt.format(group.capacity)}</span></div><div className="instance-dots">{group.instances.map(instance => <button key={instance.instance_id} className={instance.lifecycle.toLowerCase()} onClick={() => onInstance(instance)} title={instance.instance_id}><span /></button>)}</div></div> }

function Instances({ instances, snapshots, gpu, filter, setFilter, onSelect }: { instances: OperatorInstance[]; snapshots: CapacitySnapshot[]; gpu: GpuMetrics; filter: string; setFilter: (value: string) => void; onSelect: (item: OperatorInstance) => void }) {
  const [lifecycle, setLifecycle] = useState('all')
  const [device, setDevice] = useState('all')
  const [modelReady, setModelReady] = useState('all')
  const [activity, setActivity] = useState('all')
  const snapshotById = new Map(snapshots.map((item) => [item.instance_id, item]))
  const devices = [...new Set(instances.map((item) => item.labels.device || item.labels.gpu || 'GPU'))]
  const visible = instances.filter((instance) => {
    const snapshot = snapshotById.get(instance.instance_id)
    return (filter === 'all' || instance.operator_code === filter)
      && (lifecycle === 'all' || instance.lifecycle === lifecycle)
      && (device === 'all' || (instance.labels.device || instance.labels.gpu || 'GPU') === device)
      && (modelReady === 'all' || String(instance.model_ready) === modelReady)
      && (activity === 'all' || (activity === 'active' ? (snapshot?.active_lease_count || 0) > 0 : (snapshot?.active_lease_count || 0) === 0))
  })
  const sorted = [...visible].sort((left, right) => (right.inflight / Math.max(1, right.declared_capacity)) - (left.inflight / Math.max(1, left.declared_capacity)))
  return <section className="instances-view"><section className="panel gpu-instance-panel"><PanelTitle title="整机 GPU 与算子部署" /><GpuCards gpu={gpu} instances={instances} /></section><div className="panel full-panel"><div className="table-toolbar instance-toolbar"><div><h2>实例清单</h2><span className="panel-subtitle">容量、运行状态和 GPU 部署集中查看</span></div><div className="instance-filters"><label>算子<select value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部算子</option>{Object.entries(operatorNames).map(([code, name]) => <option value={code} key={code}>{name}</option>)}</select></label><label>生命周期<select value={lifecycle} onChange={event => setLifecycle(event.target.value)}><option value="all">全部状态</option><option value="ONLINE">在线</option><option value="DRAINING">排空中</option><option value="OFFLINE">离线</option></select></label><label>设备<select value={device} onChange={event => setDevice(event.target.value)}><option value="all">全部设备</option>{devices.map(item => <option value={item} key={item}>{item}</option>)}</select></label><label>模型<select value={modelReady} onChange={event => setModelReady(event.target.value)}><option value="all">全部模型</option><option value="true">已就绪</option><option value="false">未就绪</option></select></label><label>活动任务<select value={activity} onChange={event => setActivity(event.target.value)}><option value="all">全部任务</option><option value="active">有活动任务</option><option value="idle">无活动任务</option></select></label></div></div><div className="table-wrap"><table><thead><tr><th>实例</th><th>算子</th><th>GPU</th><th>模型</th><th>容量使用</th><th>有效租约</th><th>心跳</th><th>状态</th><th /></tr></thead><tbody>{sorted.map(instance => { const snapshot = snapshotById.get(instance.instance_id); const percent = Math.round((snapshot?.schedulable_used ?? instance.inflight) / Math.max(1, instance.declared_capacity) * 100); return <tr key={instance.instance_id} onClick={() => onSelect(instance)}><td><div className="instance-cell"><span className={`health-dot ${instance.lifecycle.toLowerCase()}`} /><strong>{instance.instance_id}</strong></div></td><td><span className="code-label">{operatorNames[instance.operator_code] || instance.operator_code}</span></td><td><span className="device-label">GPU {gpuIndexFor(instance) ?? '-'}</span></td><td><span className={instance.model_ready ? 'model-ready' : 'model-not-ready'}>{instance.model_ready ? 'READY' : 'NOT READY'}</span><small>{instance.model_version || '未上报'}</small></td><td><div className="table-capacity"><span>{snapshot?.schedulable_used ?? instance.inflight} / {instance.declared_capacity}</span><i><b style={{ width: `${Math.min(100, percent)}%` }} /></i></div></td><td>{snapshot?.active_lease_count ?? '-'}</td><td><span className="heartbeat">{relativeTime(instance.last_heartbeat_at)}</span></td><td><span className={`pill ${instance.lifecycle.toLowerCase()}`}>{instance.lifecycle === 'ONLINE' ? '在线' : instance.lifecycle === 'DRAINING' ? '排空中' : '离线'}</span></td><td><ChevronRight size={16} className="row-arrow" /></td></tr>})}</tbody></table></div><div className="table-footer">显示 {sorted.length} / {instances.length} 个实例 <span>点击实例查看当前 task_id · work_type</span></div></div></section>
}

function GpuCards({ gpu, instances }: { gpu: GpuMetrics; instances: OperatorInstance[] }) { return <div className="gpu-card-grid">{gpu.status !== 'ok' ? <div className="gpu-unavailable"><AlertTriangle size={15} />GPU 指标不可用{gpu.error ? ` · ${gpu.error}` : ''}</div> : gpu.devices.map((device) => { const deployed = instances.filter((instance) => gpuIndexFor(instance) === device.index); return <div className="gpu-card" key={device.index}><div className="gpu-card-head"><span className="gpu-index">GPU {device.index}</span><strong>{device.name}</strong><span className={`gpu-load ${device.utilization_percent >= 85 ? 'high' : device.utilization_percent >= 60 ? 'medium' : ''}`}>{device.utilization_percent}%</span></div><div className="gpu-bar"><i style={{ width: `${Math.min(100, device.utilization_percent)}%` }} /></div><div className="gpu-stats"><span><b>{formatBytes(device.memory_used_bytes)}</b><small>显存 / {formatBytes(device.memory_total_bytes)}</small></span><span><b>{device.temperature_celsius ?? '-'}°C</b><small>温度</small></span><span><b>{device.power_watts ?? '-'} W</b><small>功耗</small></span></div><div className="gpu-deployed"><span>已部署算子</span><div>{deployed.length ? deployed.map((instance) => <span className="gpu-operator" key={instance.instance_id} title={instance.instance_id}>{instance.operator_code}</span>) : <small>未识别实例映射</small>}</div></div></div> })}</div> }

function Tasks({ input, setInput, onSearch, loading, task, error, config, source, autoRefresh, onSelectTask }: { input: string; setInput: (value: string) => void; onSearch: (event?: FormEvent) => void; loading: boolean; task: TaskDetail | null; error: string; config: ConsoleConfig; source: ConsoleData['source']; autoRefresh: boolean; onSelectTask: (taskId: string) => void }) {
  const [taskList, setTaskList] = useState<TaskListResponse | null>(() => source === 'demo' ? demoTaskList() : null)
  const [listLoading, setListLoading] = useState(false)
  const [listError, setListError] = useState('')
  const listRequestVersion = useRef(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [sortBy, setSortBy] = useState<TaskListResponse['sort_by']>('updated_at')
  const [order, setOrder] = useState<TaskListResponse['order']>('desc')
  const [jumpPage, setJumpPage] = useState('1')

  const loadTaskList = useCallback(async () => {
    const version = ++listRequestVersion.current
    setListLoading(true)
    try {
      const result = await fetchTaskList(page, pageSize, sortBy, order, config)
      if (version !== listRequestVersion.current) return
      setTaskList(result)
      setListError('')
    } catch {
      if (version !== listRequestVersion.current) return
      if (source === 'demo') {
        setTaskList(demoTaskList(page, pageSize, sortBy, order))
        setListError('任务列表接口暂不可用，当前显示演示数据')
      } else {
        setTaskList(null)
        setListError('最新任务列表暂不可用，请检查 Control Service 或连接配置')
      }
    } finally { if (version === listRequestVersion.current) setListLoading(false) }
  }, [config, order, page, pageSize, sortBy, source])

  useEffect(() => { void loadTaskList() }, [loadTaskList])
  useEffect(() => {
    if (!autoRefresh) return
    const timer = window.setInterval(() => { void loadTaskList() }, config.refreshSeconds * 1000)
    return () => window.clearInterval(timer)
  }, [autoRefresh, config.refreshSeconds, loadTaskList])
  useEffect(() => { setJumpPage(String(page)) }, [page])

  const totalPages = Math.max(1, taskList?.total_pages || 0)

  function changeListPageSize(value: string) {
    setPageSize(Number(value))
    setPage(1)
  }

  function changeListSort(value: TaskListResponse['sort_by']) {
    setSortBy(value)
    setPage(1)
  }

  function jumpToPage(event: FormEvent) {
    event.preventDefault()
    const target = Number(jumpPage)
    if (!Number.isFinite(target)) return
    setPage(Math.min(totalPages, Math.max(1, Math.trunc(target))))
  }

  return <section className="task-view"><div className="section-heading task-section-heading"><h2>课程任务</h2><span>按更新时间查看数据库最新任务</span></div><TaskListPanel taskList={taskList} loading={listLoading} error={listError} pageSize={pageSize} sortBy={sortBy} order={order} page={page} totalPages={totalPages} jumpPage={jumpPage} setJumpPage={setJumpPage} onPageSize={changeListPageSize} onSort={changeListSort} onOrder={() => { setOrder((value) => value === 'desc' ? 'asc' : 'desc'); setPage(1) }} onPage={(target) => setPage(Math.min(totalPages, Math.max(1, target)))} onJump={jumpToPage} onSelectTask={onSelectTask} /><div className="section-heading task-section-heading"><h2>查询课程任务</h2><span>通过任务编号查看任务类型和节点状态</span></div><TaskDetailPanel input={input} setInput={setInput} onSearch={onSearch} loading={loading} task={task} error={error} /></section>
}

function TaskListPanel({ taskList, loading, error, pageSize, sortBy, order, page, totalPages, jumpPage, setJumpPage, onPageSize, onSort, onOrder, onPage, onJump, onSelectTask }: { taskList: TaskListResponse | null; loading: boolean; error: string; pageSize: number; sortBy: TaskListResponse['sort_by']; order: TaskListResponse['order']; page: number; totalPages: number; jumpPage: string; setJumpPage: (value: string) => void; onPageSize: (value: string) => void; onSort: (value: TaskListResponse['sort_by']) => void; onOrder: () => void; onPage: (page: number) => void; onJump: (event: FormEvent) => void; onSelectTask: (taskId: string) => void }) {
  const items = taskList?.items || []
  return <div className="panel full-panel task-list-panel"><div className="panel-title"><div><h2>课程任务</h2><span className="panel-subtitle">默认按最近活动时间倒序，数据来自 Control Service 数据库</span></div><span className={`chart-source ${loading ? 'loading' : ''}`}>{loading ? '读取中' : error ? '部分不可用' : '实时列表'}</span></div><div className="task-list-toolbar"><div className="task-list-controls"><label>每页显示<select value={pageSize} onChange={(event) => onPageSize(event.target.value)}><option value="10">10 条</option><option value="20">20 条</option><option value="50">50 条</option><option value="100">100 条</option></select></label><label>排序条件<select value={sortBy} onChange={(event) => onSort(event.target.value as TaskListResponse['sort_by'])}><option value="updated_at">最近更新时间</option><option value="created_at">创建时间</option><option value="task_id">Task ID</option></select></label><button type="button" className="sort-direction" onClick={onOrder} title={order === 'desc' ? '当前降序，点击切换升序' : '当前升序，点击切换降序'} aria-label="切换任务列表排序方向"><ArrowDownUp size={14} /><span>{order === 'desc' ? '降序' : '升序'}</span></button></div><span className="task-list-total">共 {taskList?.total ?? 0} 个任务</span></div>{error && <div className="task-list-error"><AlertTriangle size={14} />{error}</div>}<div className="task-list-scroll"><table className="task-list-table"><thead><tr><th>Task ID</th><th>任务类型</th><th>状态</th><th>最近更新时间</th><th /></tr></thead><tbody>{items.map((item) => <tr key={item.task_id} className={item.task_id === taskList?.items.find((current) => current.task_id === item.task_id)?.task_id ? '' : ''} onClick={() => onSelectTask(item.task_id)}><td><strong>{item.task_id}</strong><small>创建于 {formatTime(item.created_at)}</small></td><td><div className="task-type-tags">{item.tasks.map((task) => <span className="tag" key={task.task_type}>{taskNames[task.task_type] || task.task_type}</span>)}</div></td><td><span className={`pill ${statusClass(item.status_text)}`}>{item.status_text}</span></td><td><span className="task-updated-time">{formatTime(item.updated_at)}</span></td><td><ChevronRight size={15} className="row-arrow" /></td></tr>)}</tbody></table>{!items.length && <div className="task-list-empty"><Search size={22} /><span>{loading ? '正在读取最新任务...' : '暂无任务记录'}</span></div>}</div><div className="task-list-footer"><span>显示第 {(items.length ? (page - 1) * pageSize + 1 : 0)}-{Math.min(page * pageSize, taskList?.total ?? 0)} 条</span><div className="task-page-actions"><button type="button" className="icon-button" onClick={() => onPage(page - 1)} disabled={page <= 1} aria-label="上一页任务" title="上一页"><ChevronLeft size={15} /></button><b>第 {page} / {totalPages} 页</b><button type="button" className="icon-button" onClick={() => onPage(page + 1)} disabled={page >= totalPages} aria-label="下一页任务" title="下一页"><ChevronRight size={15} /></button><form className="task-page-jump" onSubmit={onJump}><label>跳转到<input type="number" min="1" max={totalPages} value={jumpPage} onChange={(event) => setJumpPage(event.target.value)} aria-label="跳转页码" /></label><button type="submit">前往</button></form></div></div></div>
}

function TaskDetailPanel({ input, setInput, onSearch, loading, task, error }: { input: string; setInput: (value: string) => void; onSearch: (event?: FormEvent) => void; loading: boolean; task: TaskDetail | null; error: string }) {
  const [pageSize, setPageSize] = useState(4)
  const [page, setPage] = useState(1)
  const [sortField, setSortField] = useState<TaskSortField>('flow')
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc')

  useEffect(() => { setPage(1) }, [task?.task_id])

  const sortedTasks = useMemo(() => {
    const items = task?.tasks ? [...task.tasks] : []
    if (sortField === 'flow') return items
    return items.sort((left, right) => {
      const leftValue = taskSortValue(left, sortField)
      const rightValue = taskSortValue(right, sortField)
      const result = typeof leftValue === 'number' && typeof rightValue === 'number'
        ? leftValue - rightValue
        : String(leftValue).localeCompare(String(rightValue), 'zh-CN')
      return sortDirection === 'asc' ? result : -result
    })
  }, [task, sortField, sortDirection])

  const totalPages = Math.max(1, Math.ceil(sortedTasks.length / pageSize))
  const safePage = Math.min(page, totalPages)
  const visibleTasks = sortedTasks.slice((safePage - 1) * pageSize, safePage * pageSize)
  const rangeStart = sortedTasks.length ? (safePage - 1) * pageSize + 1 : 0
  const rangeEnd = Math.min(safePage * pageSize, sortedTasks.length)

  function changePageSize(value: string) {
    setPageSize(Number(value))
    setPage(1)
  }

  function changeSort(value: TaskSortField) {
    setSortField(value)
    setPage(1)
  }

  return <section className="task-view"><div className="panel task-query"><div className="task-query-label"><Search size={18} /><div><h2>查询课程任务</h2><span>输入 task_id 查询任务状态</span></div></div><form onSubmit={onSearch}><input value={input} onChange={event => setInput(event.target.value)} placeholder="例如 course-20260831-001" /><button disabled={loading}>{loading ? '查询中...' : '查询'}</button></form></div>{error && <div className="inline-error"><AlertTriangle size={16} />{error}</div>}{task ? <div className="panel full-panel task-result"><div className="result-heading"><div><h2>{task.task_id}</h2></div><span className="tag success">查询成功</span></div><div className="task-result-toolbar"><div className="task-control-group"><label>每页显示<select value={pageSize} onChange={event => changePageSize(event.target.value)}><option value="4">4 个</option><option value="8">8 个</option><option value="12">12 个</option><option value="20">20 个</option></select></label><label>排序条件<select value={sortField} onChange={event => changeSort(event.target.value as TaskSortField)}><option value="flow">平台流程顺序</option><option value="task_type">任务类型</option><option value="status">状态</option><option value="updated_at">最近更新时间</option></select></label><button type="button" className="sort-direction" onClick={() => { setSortDirection((direction) => direction === 'asc' ? 'desc' : 'asc'); setPage(1) }} title={sortDirection === 'asc' ? '当前升序，点击切换降序' : '当前降序，点击切换升序'} aria-label="切换排序方向"><ArrowDownUp size={14} /><span>{sortDirection === 'asc' ? '升序' : '降序'}</span></button></div><span className="task-range">显示 {rangeStart}-{rangeEnd} / {sortedTasks.length} 个任务类型</span></div><div className="task-cards">{visibleTasks.map((item, index) => <div className="task-card" key={`${item.task_type}-${index}`}><div className="task-card-top"><span className="task-number">{String((safePage - 1) * pageSize + index + 1).padStart(2, '0')}</span><strong>{taskNames[item.task_type] || item.task_type}</strong><span className={`pill ${statusClass(item.status_text)}`}>{item.status_text || '未请求'}</span></div><div className="node-list">{(item.nodes || []).length ? item.nodes?.map((node, nodeIndex) => <div className="node-row" key={nodeIndex}><span>{String(node.node_code || node.capability || 'node')}</span><span>{node.status_text || `状态 ${node.status ?? '-'}`}</span></div>) : <span className="muted">没有节点明细</span>}</div></div>)}</div><div className="task-pagination"><span>当前显示任务类型分栏，分页在浏览器内完成</span><div><button type="button" className="icon-button" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={safePage <= 1} aria-label="上一页" title="上一页"><ChevronLeft size={15} /></button><b>{safePage} / {totalPages}</b><button type="button" className="icon-button" onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={safePage >= totalPages} aria-label="下一页" title="下一页"><ChevronRight size={15} /></button></div></div></div> : <div className="empty-state"><Search size={32} /><strong>输入 Task ID 开始追踪</strong><span>支持查询任务下各任务类型、节点状态和执行结果</span></div>}</section>
}

function taskSortValue(task: TaskDetail['tasks'][number], field: TaskSortField): string | number {
  if (field === 'task_type') return task.task_type
  if (field === 'status') return task.status ?? -1
  if (field === 'updated_at') {
    const rawTimestamps = [task.updated_at, ...(task.nodes || []).flatMap((node) => [node.updated_at, node.finished_at, node.started_at])]
    const timestamps = rawTimestamps.filter((value): value is string => typeof value === 'string').map((value) => Date.parse(value)).filter(Number.isFinite)
    return timestamps.length ? Math.max(...timestamps) : 0
  }
  return 0
}

function Gateway({ gateway, trendPoints, visualStyle, source }: { gateway: ConsoleData['gateway']; trendPoints: OperationsTrendPoint[]; visualStyle: VisualStyle; source: ConsoleData['source'] }) { const max = Math.max(...gateway.byOperator.map(item => item.value), 1); return <section className="gateway-view"><div className="gateway-kpis"><Kpi icon={<Activity />} label="请求总量" value={fmt.format(gateway.requestTotal)} detail="累计采样值" tone="teal" /><Kpi icon={<Clock3 />} label="平均延迟" value={`${avgLatency(gateway)} ms`} detail="直方图平均值" tone="blue" /><Kpi icon={<Gauge />} label="P95 延迟" value={`${Math.round(gateway.p95LatencyMs)} ms`} detail="直方图区间估算" tone="purple" /><Kpi icon={<AlertTriangle />} label="错误请求" value={fmt.format(gateway.errorTotal)} detail="算子请求异常" tone="orange" /></div><div className="panel gateway-timeseries"><div className="panel-title"><div><h2>网关实时趋势</h2><span className="panel-subtitle">请求/错误速率与 P95 延迟</span></div><span className="chart-source">{source === 'live' ? '本次会话' : '演示趋势'}</span></div><Suspense fallback={<ChartFallback className="gateway-trend-chart" />}><GatewayTrendChart points={trendPoints} visualStyle={visualStyle} /></Suspense></div><div className="panel full-panel"><PanelTitle title="算子请求分布" action="指标明细" /><div className="bar-chart">{gateway.byOperator.length ? gateway.byOperator.map(item => <div className="bar-row" key={item.name}><div className="bar-label"><strong>{operatorNames[item.name] || item.name}</strong><span>{fmt.format(item.value)}</span></div><div className="bar-track"><i style={{ width: `${item.value / max * 100}%` }} /></div></div>) : <div className="empty-small">暂无算子请求数据</div>}</div><div className="metric-foot"><span>指标来源</span><code>online-gateway-service /metrics</code><span>采样时间</span><b>{formatTime(gateway.sampledAt)}</b></div></div><div className="panel gateway-contract"><PanelTitle title="在线链路" /><div className="flow"><FlowStep label="A 服务" status="上游调用" /><ChevronRight /><FlowStep label="gateway-online" status="转发与租约" active /><ChevronRight /><FlowStep label="七类算子" status="HTTP / WS" /></div><p>当前页面只读读取网关指标，不改变在线图像和 ASR 的原有 HTTP / WebSocket 路由。</p></div></section> }
function FlowStep({ label, status, active }: { label: string; status: string; active?: boolean }) { return <div className={`flow-step ${active ? 'active' : ''}`}><span><CircleDot size={14} /></span><strong>{label}</strong><small>{status}</small></div> }
function System({ data }: { data: ConsoleData }) { const roots = data.storage.roots || []; const readinessChecks = { ...(data.readiness.checks || {}), kafka: data.kafka.status === 'ok' ? 'ok' : data.kafka.status === 'degraded' ? 'degraded' : 'unavailable' }; const checkNames: Record<string, string> = { postgres: 'PostgreSQL', postgresql: 'PostgreSQL', redis: 'Redis', schema: 'Schema', kafka: 'Kafka' }; return <section className="system-grid"><div className="panel readiness-panel"><PanelTitle title="平台就绪检查" /><div className="readiness-main"><div className="readiness-icon"><CheckCircle2 size={25} /></div><div><strong>{data.readiness.status === 'ready' && data.kafka.status === 'ok' ? '系统就绪' : '状态需关注'}</strong><span>Control Service 就绪与 Kafka 发布链路</span></div></div><div className="check-list">{Object.entries(readinessChecks).map(([name, value]) => <div key={name}><span className={`health-dot ${String(value) === 'ok' ? '' : 'attention'}`} />{checkNames[name] || name}<b>{typeof value === 'string' ? value : value ? 'ok' : 'unavailable'}</b></div>)}</div></div><KafkaPanel kafka={data.kafka} /><div className="panel gpu-system-panel"><PanelTitle title="GPU 采集器状态" /><div className="readiness-main"><div className="readiness-icon"><Cpu size={25} /></div><div><strong>{data.gpu.status === 'ok' ? `${data.gpu.devices.length} 张显卡在线` : 'GPU 指标不可用'}</strong><span>独立 GPU 指标容器 /gpu</span></div></div></div><div className="panel storage-panel"><PanelTitle title="存储空间" /><div className="storage-list">{roots.length ? roots.map(root => { const used = Number(root.used_bytes || 0); const total = Number(root.total_bytes || 1); return <div className="storage-row" key={root.kind}><div><strong>{root.kind === 'course' ? '课程临时目录' : '结果持久目录'}</strong><span>{root.path}</span></div><div className="storage-meter"><i style={{ width: `${used / total * 100}%` }} /></div><b>{Math.round(used / total * 100)}%</b></div> }) : <div className="empty-small">暂无存储数据</div>}</div></div><div className="panel contract-panel"><PanelTitle title="观测边界" /><div className="boundary-list"><div><span className="green-dot" />只读</div><div><span className="green-dot" />不触碰 A 服务接口</div><div><span className="green-dot" />GPU 仅采集整卡指标</div><div><span className="orange-dot" />Docker 控制未开放</div></div></div></section> }

function InstanceDrawer({ instance, snapshot, activeLeases, leaseLoading, leaseError, onClose }: { instance: OperatorInstance; snapshot?: CapacitySnapshot; activeLeases: ActiveLeaseResponse | null; leaseLoading: boolean; leaseError: string; onClose: () => void }) { return <div className="drawer-backdrop" onClick={onClose}><aside className="drawer" onClick={event => event.stopPropagation()}><div className="drawer-head"><div><h2>{instance.instance_id}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭详情"><X size={18} /></button></div><div className="drawer-status"><span className={`health-dot ${instance.lifecycle.toLowerCase()}`} />{instance.lifecycle === 'ONLINE' ? '在线服务中' : instance.lifecycle}<span className="pill online">只读</span></div><dl className="detail-list"><dt>算子类型</dt><dd>{operatorNames[instance.operator_code] || instance.operator_code}</dd><dt>GPU</dt><dd>{gpuIndexFor(instance) === undefined ? '未识别' : `GPU ${gpuIndexFor(instance)}`}</dd><dt>服务地址</dt><dd><code>{instance.service_url}</code></dd><dt>模型版本</dt><dd>{instance.model_version || '未上报'}</dd><dt>API 版本</dt><dd>{instance.api_version || '未上报'}</dd><dt>最近心跳</dt><dd>{formatTime(instance.last_heartbeat_at)}</dd></dl><div className="drawer-section"><h3>容量观测</h3><div className="drawer-metric"><span>声明容量</span><strong>{snapshot?.declared_capacity ?? instance.declared_capacity}</strong></div><div className="drawer-metric"><span>在途任务</span><strong>{snapshot?.reported_inflight ?? instance.inflight}</strong></div><div className="drawer-metric"><span>有效租约</span><strong>{activeLeases?.active_lease_count ?? snapshot?.active_lease_count ?? '-'}</strong></div>{snapshot?.capacity_mismatch && <div className="mismatch"><AlertTriangle size={15} />心跳在途与有效租约存在差异</div>}</div><div className="drawer-section live-work-section"><div className="section-heading"><h3>当前执行中的任务</h3><span>{leaseLoading ? '刷新中' : '5 秒采样'}</span></div>{leaseError && <div className="lease-error"><AlertTriangle size={14} />{leaseError}</div>}{activeLeases?.leases.length ? activeLeases.leases.map(lease => <div className="lease-row" key={lease.lease_id}><div className="lease-main"><strong>{lease.work_context?.task_id || '在线请求 · 无课程任务'} · {lease.work_context?.work_type || lease.capability}</strong><span>{lease.work_context?.source_service || '来源服务未上报'}</span><small>{[lease.work_context?.node_id, lease.work_context?.item_id, lease.work_context?.work_id].filter(Boolean).join(' · ') || '上下文未细分'}</small></div><div className="lease-side"><span className={`lease-state ${lease.context_status === 'BOUND' ? 'bound' : 'unbound'}`}>{lease.context_status === 'BOUND' ? '已绑定' : '未绑定'}</span><small>至 {formatClock(lease.expires_at)}</small></div></div>) : leaseLoading ? <div className="lease-empty">正在读取实例租约...</div> : <div className="lease-empty"><CheckCircle2 size={16} />当前没有执行中的租约</div>}</div><div className="drawer-note">当前任务来自有效租约的工作上下文，只代表仍在执行的任务。租约释放后需要历史执行归属接口才能回溯。</div></aside></div> }

function Empty({ text }: { text: string }) { return <div className="empty-small">{text}</div> }
function avgLatency(gateway: ConsoleData['gateway']) { return gateway.latencyCount ? Math.round(gateway.latencySum / gateway.latencyCount * 1000) : 0 }
function statusClass(text?: string) { return text?.includes('完成') ? 'success' : text?.includes('处理') ? 'processing' : 'offline' }
function relativeTime(value: string) { const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000)); return seconds < 60 ? `${seconds} 秒前` : `${Math.round(seconds / 60)} 分钟前` }
function gpuIndexFor(instance: OperatorInstance): number | undefined { const value = instance.labels.gpu || instance.labels.gpu_index || instance.instance_id.match(/gpu(\d+)/i)?.[1]; const parsed = value === undefined ? NaN : Number(value); return Number.isInteger(parsed) && parsed >= 0 ? parsed : undefined }
function formatBytes(value: number): string { if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(0)} MB`; return `${(value / 1024 ** 3).toFixed(1)} GB` }
function formatTime(value: string) { return new Date(value).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) }
function formatClock(value: string) { return new Date(value).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) }

export default App
