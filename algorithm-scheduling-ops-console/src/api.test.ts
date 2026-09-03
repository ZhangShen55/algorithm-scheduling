import { deploymentTemplateConfig, fetchConsoleData, fetchTaskList, fetchTaskSummary, loadConsoleConfig, saveConsoleConfig } from './api'

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
