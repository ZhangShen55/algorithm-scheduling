import { configuredGpuNodes, deploymentTemplateConfig, emptyConsoleData, fetchConsoleData, fetchGpuNodeStates, fetchTaskList, fetchTaskSummary, loadConsoleConfig, mergeConsoleGpuSnapshots, mergeGpuNodeStates, saveConsoleConfig, validateGpuNodeConfigs } from './api'

const config = {
  controlBaseUrl: 'http://control.test:18100',
  gatewayBaseUrl: 'http://gateway.test:18103',
  gpuBaseUrl: 'http://gpu.test:9400',
  refreshSeconds: 10,
  leaseRefreshSeconds: 5,
  gpuRefreshSeconds: 5,
}

test('根据页面主机生成三个部署地址模板并读取浏览器保存值', () => {
  expect(deploymentTemplateConfig('192.168.29.11')).toMatchObject({
    controlBaseUrl: 'http://192.168.29.11:18100',
    gatewayBaseUrl: 'http://192.168.29.11:18103',
    gpuBaseUrl: 'http://192.168.29.11:9400',
  })
  saveConsoleConfig({ ...config, refreshSeconds: 0, gpuRefreshSeconds: 99 })
  expect(loadConsoleConfig()).toMatchObject({
    controlBaseUrl: config.controlBaseUrl,
    refreshSeconds: 1,
    gpuRefreshSeconds: 30,
  })
})

test('没有旧 GPU 配置时保留全部构建节点，旧单地址只迁移一次', () => {
  const defaults = [
    { hostId: '192.168.29.11', name: 'A', url: 'http://192.168.29.11:9400', enabled: true },
    { hostId: '192.168.29.12', name: 'B', url: 'http://192.168.29.12:9400', enabled: true },
  ]
  expect(configuredGpuNodes({}, defaults)).toEqual(defaults)
  expect(configuredGpuNodes({ gpuBaseUrl: 'http://192.168.29.99:9400' }, defaults)).toEqual([
    { hostId: '192.168.29.11', name: 'A', url: 'http://192.168.29.99:9400', enabled: true },
  ])
})

test('任务筛选生成重复 task_type、任务项状态和自定义分页参数', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ items: [], page: 1, page_size: 30, total: 0, total_pages: 0, sort_by: 'updated_at', order: 'desc' }), { status: 200, headers: { 'content-type': 'application/json' } }))
  await fetchTaskList(1, 30, 'updated_at', 'desc', config, {
    taskTypes: ['PPT', 'ASR'],
    statusScope: 'task',
    taskStatusType: 'ASR',
    taskStatus: 50,
    taskIdLike: 'test_all_0903',
  })
  const url = new URL(String(fetchMock.mock.calls[0][0]))
  expect(url.searchParams.getAll('task_type')).toEqual(['PPT', 'ASR'])
  expect(url.searchParams.get('page_size')).toBe('30')
  expect(url.searchParams.get('task_status_type')).toBe('ASR')
  expect(url.searchParams.get('task_status')).toBe('50')
  expect(url.searchParams.get('task_id_like')).toBe('test_all_0903')
})

test('返回 HTML 且地址指向 5174 时给出后端端口提示', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('<!doctype html>', { status: 200, headers: { 'content-type': 'text/html' } }))
  await expect(fetchTaskSummary('course-1', { ...config, controlBaseUrl: 'http://192.168.29.11:5174' })).rejects.toThrow('前端端口 5174')
})

test('存储或网关指标失败时保留其他成功观测数据', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (request: RequestInfo | URL) => {
    const url = new URL(String(request))
    if (url.pathname === '/ops/storage') return new Response('storage busy', { status: 503 })
    if (url.hostname === 'gateway.test') return new Response('gateway busy', { status: 503 })
    const payloads: Record<string, unknown> = {
      '/ops/operator-instances': [],
      '/ops/operator-instances/snapshot': [],
      '/ops/queues': { queues: [], outbox_pending: 0 },
      '/ops/readiness': { status: 'ready', checks: {} },
      '/ops/kafka': { status: 'ok', publisher_status: 'ok', outbox_pending: 0, published: 1, publish_failed: 0, consumer_lag: 0, sampled_at: '2026-09-03T00:00:00Z' },
      '/gpu': { status: 'ok', sampled_at: 1, devices: [{ index: 0, name: 'GPU', utilization_percent: 10, memory_used_bytes: 1, memory_total_bytes: 2 }] },
    }
    if (url.pathname === '/metrics') return new Response('algorithm_outbox_pending 0', { status: 200 })
    return new Response(JSON.stringify(payloads[url.pathname]), { status: 200 })
  })

  const data = await fetchConsoleData(config)

  expect(data.source).toBe('live')
  expect(data.storage.roots).toEqual([])
  expect(data.gateway.requestTotal).toBe(0)
  expect(data.gpu.devices).toHaveLength(1)
  expect(fetchMock.mock.calls.some(([request]) => String(request).includes('/ops/storage?include_directory_bytes=false'))).toBe(true)
})

test('多 GPU 主机按 host_id 聚合且同编号 GPU 不冲突', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (request: RequestInfo | URL) => {
    const url = new URL(String(request))
    const hostId = url.hostname === 'gpu-a.test' ? '192.168.29.11' : '192.168.29.12'
    return new Response(JSON.stringify({ status: 'ok', host_id: hostId, sampled_at: 1, devices: [{ index: 0, name: 'GPU', utilization_percent: 10, memory_used_bytes: 1, memory_total_bytes: 2 }] }), { status: 200 })
  })
  const states = await fetchGpuNodeStates({ ...config, gpuNodes: [
    { hostId: '192.168.29.11', name: 'A', url: 'http://gpu-a.test:9400', enabled: true },
    { hostId: '192.168.29.12', name: 'B', url: 'http://gpu-b.test:9400', enabled: true },
  ] })
  expect(states.every((state) => state.status === 'ok')).toBe(true)
  expect(states.map((state) => state.config.hostId)).toEqual(['192.168.29.11', '192.168.29.12'])
  expect(validateGpuNodeConfigs(states.map((state) => state.config))).toBeNull()
  expect(validateGpuNodeConfigs([{ hostId: '192.168.29.11', name: 'A', url: 'http://gpu-a.test:9400', enabled: true }, { hostId: '192.168.29.11', name: 'B', url: 'http://gpu-b.test:9400', enabled: true }])).toContain('重复')
})

test('多节点身份不一致只标记该节点', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ status: 'ok', host_id: '192.168.29.99', sampled_at: 1, devices: [] }), { status: 200 }))
  const states = await fetchGpuNodeStates({ ...config, gpuNodes: [{ hostId: '192.168.29.12', name: 'B', url: 'http://gpu-b.test:9400', enabled: true }] })
  expect(states[0].status).toBe('identity_error')
  expect(states[0].error).toContain('身份不一致')
})

test('同一 GPU 节点的并发刷新复用一个在途请求', async () => {
  let resolveResponse!: (response: Response) => void
  const response = new Promise<Response>((resolve) => { resolveResponse = resolve })
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockReturnValue(response)
  const nodeConfig = { ...config, gpuNodes: [{ hostId: '192.168.29.12', name: 'B', url: 'http://gpu-b.test:9400', enabled: true }] }

  const first = fetchGpuNodeStates(nodeConfig)
  const second = fetchGpuNodeStates(nodeConfig)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  resolveResponse(new Response(JSON.stringify({ status: 'ok', host_id: '192.168.29.12', sampled_at: 1, devices: [] }), { status: 200 }))

  await expect(first).resolves.toEqual(await second)
})

test('节点失败时保留最近成功快照并允许后续恢复', () => {
  const node = { hostId: '192.168.29.12', name: 'B', url: 'http://gpu-b.test:9400', enabled: true }
  const previous = [{ config: node, status: 'ok' as const, metrics: { status: 'ok' as const, host_id: node.hostId, sampled_at: 1, devices: [{ index: 0, name: 'GPU', utilization_percent: 10, memory_used_bytes: 1, memory_total_bytes: 2 }] }, lastSuccessAt: '2026-09-07T10:00:00Z' }]
  const failed = mergeGpuNodeStates(previous, [{ config: node, status: 'unavailable', metrics: null, error: 'timeout' }])
  expect(failed[0].metrics?.devices).toHaveLength(1)
  expect(failed[0].error).toBe('timeout')
  const recovered = mergeGpuNodeStates(failed, [{ ...previous[0], lastSuccessAt: '2026-09-07T10:01:00Z' }])
  expect(recovered[0].status).toBe('ok')
  expect(recovered[0].lastSuccessAt).toBe('2026-09-07T10:01:00Z')
})

test('总览刷新不会覆盖失败 GPU 节点的最近成功快照', () => {
  const node = { hostId: '192.168.29.12', name: 'B', url: 'http://gpu-b.test:9400', enabled: true }
  const previous = emptyConsoleData()
  previous.gpuNodes = [{ config: node, status: 'ok', metrics: { status: 'ok', host_id: node.hostId, sampled_at: 1, devices: [{ index: 0, name: 'GPU', utilization_percent: 10, memory_used_bytes: 1, memory_total_bytes: 2 }] }, lastSuccessAt: '2026-09-07T10:00:00Z' }]
  const current = emptyConsoleData()
  current.gpuNodes = [{ config: node, status: 'unavailable', metrics: null, error: 'timeout' }]

  const merged = mergeConsoleGpuSnapshots(previous, current)

  expect(merged.gpuNodes[0].status).toBe('unavailable')
  expect(merged.gpuNodes[0].metrics?.devices).toHaveLength(1)
  expect(merged.gpuNodes[0].lastSuccessAt).toBe('2026-09-07T10:00:00Z')
})
