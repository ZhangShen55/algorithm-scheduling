import { fireEvent, render, screen } from '@testing-library/react'
import App, { gpuIndexFor, hostIdFor, Instances, topologyDisplayFor } from './App'
import type { GpuNodeState, OperatorInstance } from './types'

function instance(labels: Record<string, string>, instanceId = 'asr-offline-gpu9'): OperatorInstance {
  return {
    instance_id: instanceId,
    operator_code: 'asr_offline',
    capabilities: ['asr_offline'],
    service_url: 'http://operator:8083',
    declared_capacity: 1,
    labels,
    lifecycle: 'ONLINE',
    inflight: 0,
    model_ready: true,
    last_heartbeat_at: new Date().toISOString(),
  }
}

test('实例拓扑只读取显式 labels，不解析实例名和地址', () => {
  expect(hostIdFor(instance({ host_id: '192.168.29.12', gpu: '0' }))).toBe('192.168.29.12')
  expect(gpuIndexFor(instance({ host_id: '192.168.29.12', gpu: '0' }))).toBe(0)
  expect(gpuIndexFor(instance({}, 'asr-offline-gpu9'))).toBeUndefined()
  expect(gpuIndexFor(instance({ gpu: '-1' }))).toBeUndefined()
  expect(gpuIndexFor(instance({ gpu: 'not-a-number' }))).toBeUndefined()
})

test('实例页面展示服务器筛选与主机分组', async () => {
  window.history.replaceState(null, '', '/?demo=1')
  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: /算子实例/ }))
  expect(await screen.findByText('全部服务器')).toBeInTheDocument()
  expect(screen.getAllByText('192.168.29.11').length).toBeGreaterThan(0)
  expect(screen.getAllByText('平台 GPU 主机').length).toBeGreaterThan(0)
})

function gpuNode(hostId: string, deviceIndexes = [0]): GpuNodeState {
  return {
    config: { hostId, name: `节点 ${hostId}`, url: `http://${hostId}:9400`, enabled: true },
    status: 'ok',
    metrics: {
      status: 'ok',
      host_id: hostId,
      sampled_at: 1,
      devices: deviceIndexes.map((index) => ({ index, name: 'RTX 4090', utilization_percent: 10, memory_used_bytes: 1, memory_total_bytes: 2 })),
    },
  }
}

test('使用 host_id 与 gpu_index 精确关联同编号 GPU，并显示空卡和异常标签', () => {
  const nodes = [gpuNode('192.168.29.11'), gpuNode('192.168.29.12', [0, 1])]
  const instances = [
    instance({ host_id: '192.168.29.11', gpu: '0' }, 'instance-a'),
    instance({ host_id: '192.168.29.12', gpu: '0' }, 'instance-b'),
    instance({ gpu: '1' }, 'missing-host'),
    instance({ host_id: '192.168.29.11', gpu: 'bad' }, 'invalid-gpu'),
  ]

  render(<Instances instances={instances} snapshots={[]} gpu={{ status: 'ok', sampled_at: 1, devices: [] }} gpuNodes={nodes} filter="all" setFilter={() => undefined} onSelect={() => undefined} />)

  expect(screen.getByTitle('instance-a').closest('.gpu-host-group')).toHaveTextContent('192.168.29.11')
  expect(screen.getByTitle('instance-b').closest('.gpu-host-group')).toHaveTextContent('192.168.29.12')
  expect(screen.getAllByText('未部署算子')).toHaveLength(1)
  expect(screen.getAllByText('未标记主机').length).toBeGreaterThan(0)
  expect(screen.getAllByText('GPU 标签无效').length).toBeGreaterThan(0)
})

test('主机筛选同时过滤 GPU 分组和实例清单', () => {
  const nodes = [gpuNode('192.168.29.11'), gpuNode('192.168.29.12')]
  const instances = [
    instance({ host_id: '192.168.29.11', gpu: '0' }, 'instance-a'),
    instance({ host_id: '192.168.29.12', gpu: '0' }, 'instance-b'),
  ]

  render(<Instances instances={instances} snapshots={[]} gpu={{ status: 'ok', sampled_at: 1, devices: [] }} gpuNodes={nodes} filter="all" setFilter={() => undefined} onSelect={() => undefined} />)
  fireEvent.change(screen.getByLabelText('服务器'), { target: { value: '192.168.29.12' } })

  expect(screen.queryByText('instance-a')).not.toBeInTheDocument()
  expect(screen.getAllByText('instance-b').length).toBeGreaterThan(0)
  expect(screen.queryByText('节点 192.168.29.11')).not.toBeInTheDocument()
  expect(screen.getByText('节点 192.168.29.12')).toBeInTheDocument()
})

test('单节点旧实例显示兼容映射，多节点禁止兼容', () => {
  const legacy = instance({ gpu: '0' }, 'legacy-instance')
  expect(topologyDisplayFor(legacy, [gpuNode('192.168.29.11')])).toEqual({ host: '192.168.29.11 · 兼容映射', gpu: 'GPU 0' })
  expect(topologyDisplayFor(legacy, [gpuNode('192.168.29.11'), gpuNode('192.168.29.12')])).toEqual({ host: '未标记主机', gpu: 'GPU 0' })
})

test('身份异常节点不关联算子实例', () => {
  const node = { ...gpuNode('192.168.29.12'), status: 'identity_error' as const, error: '主机身份不一致' }
  render(<Instances instances={[instance({ host_id: '192.168.29.12', gpu: '0' }, 'wrong-host-instance')]} snapshots={[]} gpu={{ status: 'unavailable', sampled_at: 1, devices: [] }} gpuNodes={[node]} filter="all" setFilter={() => undefined} onSelect={() => undefined} />)

  expect(screen.getByText('身份异常')).toBeInTheDocument()
  expect(screen.queryByTitle('wrong-host-instance')).not.toBeInTheDocument()
  expect(screen.getByText('未部署算子')).toBeInTheDocument()
})
