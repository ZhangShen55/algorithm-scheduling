import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { KafkaEvents } from './KafkaEvents'

const config = { controlBaseUrl: 'http://control.test', gatewayBaseUrl: 'http://gateway.test', gpuBaseUrl: 'http://gpu.test', refreshSeconds: 10, leaseRefreshSeconds: 5, gpuRefreshSeconds: 5 }
const event = { event_id: '11111111-1111-1111-1111-111111111111', aggregate_type: 'COURSE_TASK_TYPE', aggregate_id: 'course-1:PPT', event_type: 'COURSE_TASK_REQUESTED', task_id: 'course-1', task_type: 'PPT', publish_status: 'PUBLISHED', available_at: '2026-09-03T12:00:00Z', published_at: '2026-09-03T12:00:01Z', publish_attempts: 1, created_at: '2026-09-03T12:00:00Z' }

test('发布记录使用 Broker 确认措辞且 payload 默认不请求', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (request: RequestInfo | URL) => {
    const url = String(request)
    const payload = url.endsWith(event.event_id) ? { ...event, payload: { task_id: 'course-1', task_type: 'PPT' } } : { items: [event], page: 1, page_size: 20, total: 1, total_pages: 1, order: 'desc' }
    return new Response(JSON.stringify(payload), { status: 200 })
  })
  render(<KafkaEvents config={config} />)
  expect(await screen.findByText('Broker 已确认', { selector: '.pill' })).toBeInTheDocument()
  expect(screen.queryByText('"task_id": "course-1"')).not.toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledTimes(1)
  fireEvent.click(screen.getByRole('button', { name: '查看发布内容' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  expect(await screen.findByText(/"task_id": "course-1"/)).toBeInTheDocument()
  expect(screen.queryByText(/Topic|Partition|Offset/)).not.toBeInTheDocument()
})
