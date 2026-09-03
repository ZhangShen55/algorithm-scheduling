import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { TaskWorkspace } from './TaskWorkspace'

const config = {
  controlBaseUrl: 'http://control.test',
  gatewayBaseUrl: 'http://gateway.test',
  gpuBaseUrl: 'http://gpu.test',
  refreshSeconds: 1,
  leaseRefreshSeconds: 5,
  gpuRefreshSeconds: 5,
}

const taskId = 'test_all_0903_15'
const taskTypes = ['PPT', 'ASR', 'TEACHER_BEHAVIOR', 'STUDENT_BEHAVIOR'] as const
const list = {
  items: [{
    task_id: taskId,
    created_at: '2026-09-03T09:48:46+08:00',
    updated_at: '2026-09-03T10:32:56+08:00',
    status: 60,
    status_text: '已完成',
    task_count: 4,
    tasks: taskTypes.map((task_type) => ({ task_type, status: 60, status_text: '已完成' })),
  }],
  page: 1,
  page_size: 10,
  total: 1,
  total_pages: 1,
  sort_by: 'updated_at',
  order: 'desc',
}

const details = {
  PPT: {
    task_type: 'PPT', status: 60, status_text: '已完成', reason: 'PPT 所有节点处理完成',
    nodes: [
      { node_code: 'PPT_SLICE', status: 60, status_text: '已完成', reason: '节点执行完成', processing_duration_ms: 432455, total_duration_ms: 505258, result_summary: { slice_count: 18, dynamic_segment_count: 5 } },
      { node_code: 'PPT_OCR', status: 60, status_text: '已完成', processing_duration_ms: 19331, result_summary: { page_count: 18, success_count: 18 } },
    ],
  },
  ASR: {
    task_type: 'ASR', status: 60, status_text: '已完成', reason: '节点执行完成: ASR_TRANSCRIPTION',
    nodes: [{ node_code: 'ASR_TRANSCRIPTION', status: 60, status_text: '已完成', processing_duration_ms: 225568, result_summary: { audio_duration_seconds: 2802.69, segment_count: 708, language: 'auto' } }],
  },
  TEACHER_BEHAVIOR: {
    task_type: 'TEACHER_BEHAVIOR', status: 60, status_text: '已完成', reason: '教师行为处理完成',
    nodes: [{ node_code: 'TEACHER_BEHAVIOR_ANALYSIS', status: 60, status_text: '已完成', processing_duration_ms: 2302000, result_summary: { duration_seconds: 2880.958, evidence_count: 9 } }],
  },
  STUDENT_BEHAVIOR: {
    task_type: 'STUDENT_BEHAVIOR', status: 60, status_text: '已完成', reason: '学生行为处理完成',
    nodes: [{ node_code: 'STUDENT_BEHAVIOR_ANALYSIS', status: 60, status_text: '已完成', processing_duration_ms: 2247103, result_summary: { frame_count: 288, evidence_count: 6 } }],
  },
}

test('任务详情展示四类摘要，长结果按需加载且刷新保持展开状态', async () => {
  let resultRequests = 0
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (request: RequestInfo | URL) => {
    const url = new URL(String(request))
    let payload: unknown = list
    if (url.pathname.endsWith('/summary')) payload = list.items[0]
    else if (url.pathname.endsWith('/events')) payload = { items: [], page: 1, page_size: 100, total: 0, total_pages: 0, order: 'asc' }
    else if (url.pathname.endsWith('/result')) {
      resultRequests += 1
      payload = {
        task_id: taskId,
        task_type: 'PPT',
        section: 'dynamic_segments',
        results: [{
          node_code: 'PPT_SLICE', page: 1, page_size: 20, total: 1, total_pages: 1,
          items: [{ start_ms: 283695, end_ms: 323795, reason: 'repeated_dynamic_cluster' }],
        }],
      }
    } else {
      const taskType = taskTypes.find((value) => url.pathname.endsWith(`/task-types/${value}`))
      if (taskType) payload = details[taskType]
    }
    return new Response(JSON.stringify(payload), { status: 200 })
  })

  const view = render(<TaskWorkspace config={config} source="live" autoRefresh={false} />)
  fireEvent.click(await screen.findByText(taskId))

  expect(await screen.findByText('PPT 切片')).toBeInTheDocument()
  expect(screen.getAllByText('语音转写', { selector: '.task-type-detail strong' })).toHaveLength(2)
  expect(screen.getByText('教师行为分析')).toBeInTheDocument()
  expect(screen.getByText('学生行为分析')).toBeInTheDocument()
  expect(screen.getByText('708')).toBeInTheDocument()
  expect(screen.getByText('288')).toBeInTheDocument()
  expect(screen.getByText('38:22')).toBeInTheDocument()

  const disclosure = screen.getByRole('button', { name: '疑似视频播放区间' })
  expect(disclosure).toHaveAttribute('aria-expanded', 'false')
  expect(resultRequests).toBe(0)

  fireEvent.click(disclosure)
  await waitFor(() => expect(resultRequests).toBe(1))
  expect(await screen.findByText(/"start_time": "00:04:43"/)).toBeInTheDocument()
  expect(screen.getByText(/重复动态画面聚集，疑似播放视频/)).toBeInTheDocument()

  vi.useFakeTimers()
  view.rerender(<TaskWorkspace config={config} source="live" autoRefresh />)
  await act(async () => { vi.advanceTimersByTime(1000) })
  expect(screen.getByRole('button', { name: '疑似视频播放区间' })).toHaveAttribute('aria-expanded', 'true')
  expect(resultRequests).toBe(1)
  vi.useRealTimers()
  fetchMock.mockRestore()
})
