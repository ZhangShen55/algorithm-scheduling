import { useEffect, useMemo, useRef } from 'react'
import { init, use } from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsCoreOption, EChartsType } from 'echarts/core'
import type { CapacitySnapshot, OperationsTrendPoint, VisualStyle } from './types'

use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

type ChartTheme = {
  text: string
  muted: string
  grid: string
  panel: string
  teal: string
  blue: string
  orange: string
  red: string
  remaining: string
}

const chartThemes: Record<VisualStyle, ChartTheme> = {
  industrial: { text: '#26322f', muted: '#74817e', grid: '#dce2e0', panel: '#fbfcfb', teal: '#007f73', blue: '#3975a6', orange: '#d98222', red: '#c64d4d', remaining: '#e1e7e4' },
  command: { text: '#dbe9e5', muted: '#718985', grid: '#29403b', panel: '#111a18', teal: '#48d6b6', blue: '#65a1e8', orange: '#f0a84b', red: '#ef6f6f', remaining: '#22332f' },
}

function formatTime(value: string): string {
  return new Date(value).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })
}

function baseOption(theme: ChartTheme): EChartsCoreOption {
  return {
    animationDuration: 260,
    textStyle: { color: theme.text, fontFamily: 'DM Mono, Noto Sans SC, sans-serif' },
    tooltip: {
      trigger: 'axis',
      backgroundColor: theme.panel,
      borderColor: theme.grid,
      borderWidth: 1,
      textStyle: { color: theme.text, fontSize: 10 },
      axisPointer: { type: 'line', lineStyle: { color: theme.muted, width: 1 } },
    },
  }
}

function ChartCanvas({ option, className, ariaLabel, height }: { option: EChartsCoreOption; className: string; ariaLabel: string; height?: number }) {
  const elementRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<EChartsType | null>(null)
  useEffect(() => {
    if (!elementRef.current) return
    const chart = init(elementRef.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(elementRef.current)
    return () => { observer.disconnect(); chart.dispose(); chartRef.current = null }
  }, [])
  useEffect(() => { chartRef.current?.setOption(option, { notMerge: true }) }, [option])
  return <div ref={elementRef} className={`echart ${className}`} style={height ? { height } : undefined} role="img" aria-label={ariaLabel} />
}

export function QueueTrendChart({ points, visualStyle }: { points: OperationsTrendPoint[]; visualStyle: VisualStyle }) {
  const option = useMemo<EChartsCoreOption>(() => {
    const theme = chartThemes[visualStyle]
    return {
      ...baseOption(theme),
      color: [theme.blue, theme.teal, theme.orange],
      legend: { top: 0, right: 0, itemWidth: 8, itemHeight: 5, textStyle: { color: theme.muted, fontSize: 9 }, data: ['待处理', '处理中', '等容量'] },
      grid: { left: 27, right: 6, top: 27, bottom: 20 },
      xAxis: { type: 'category', boundaryGap: true, data: points.map((point) => formatTime(point.sampledAt)), axisLine: { lineStyle: { color: theme.grid } }, axisTick: { show: false }, axisLabel: { color: theme.muted, fontSize: 8, interval: Math.max(0, Math.ceil(points.length / 5) - 1) } },
      yAxis: { type: 'value', minInterval: 1, splitNumber: 3, axisLabel: { color: theme.muted, fontSize: 8 }, splitLine: { lineStyle: { color: theme.grid, type: 'dashed' } } },
      series: [
        { name: '待处理', type: 'bar', stack: 'queue', barMaxWidth: 13, data: points.map((point) => point.queuePending), emphasis: { focus: 'series' } },
        { name: '处理中', type: 'bar', stack: 'queue', barMaxWidth: 13, data: points.map((point) => point.queueProcessing), emphasis: { focus: 'series' } },
        { name: '等容量', type: 'bar', stack: 'queue', barMaxWidth: 13, data: points.map((point) => point.queueWaiting), emphasis: { focus: 'series' } },
      ],
    }
  }, [points, visualStyle])
  return <ChartCanvas option={option} className="queue-trend-chart" ariaLabel="调度队列会话趋势图" />
}

export function GatewayTrendChart({ points, visualStyle, compact = false }: { points: OperationsTrendPoint[]; visualStyle: VisualStyle; compact?: boolean }) {
  const option = useMemo<EChartsCoreOption>(() => {
    const theme = chartThemes[visualStyle]
    return {
      ...baseOption(theme),
      color: [theme.teal, theme.red, theme.blue],
      legend: compact ? { show: false } : { top: 0, right: 4, itemWidth: 14, itemHeight: 3, textStyle: { color: theme.muted, fontSize: 9 }, data: ['请求速率', '错误速率', 'P95 延迟'] },
      grid: { left: compact ? 4 : 42, right: compact ? 4 : 45, top: compact ? 4 : 31, bottom: compact ? 3 : 25, containLabel: false },
      xAxis: { type: 'category', boundaryGap: false, data: points.map((point) => formatTime(point.sampledAt)), axisLine: { show: !compact, lineStyle: { color: theme.grid } }, axisTick: { show: false }, axisLabel: { show: !compact, color: theme.muted, fontSize: 8, interval: Math.max(0, Math.ceil(points.length / 6) - 1) } },
      yAxis: [
        { type: 'value', name: compact ? '' : 'req/s', nameTextStyle: { color: theme.muted, fontSize: 8 }, splitNumber: 3, axisLabel: { show: !compact, color: theme.muted, fontSize: 8 }, splitLine: { show: !compact, lineStyle: { color: theme.grid, type: 'dashed' } } },
        { type: 'value', name: compact ? '' : 'ms', nameTextStyle: { color: theme.muted, fontSize: 8 }, splitNumber: 3, axisLabel: { show: !compact, color: theme.muted, fontSize: 8 }, splitLine: { show: false } },
      ],
      series: [
        { name: '请求速率', type: 'line', data: points.map((point) => Number(point.requestRate.toFixed(2))), showSymbol: false, smooth: 0.18, lineStyle: { width: compact ? 2 : 2 } },
        { name: '错误速率', type: 'line', data: points.map((point) => Number(point.errorRate.toFixed(2))), showSymbol: false, smooth: 0.18, lineStyle: { width: 1 } },
        { name: 'P95 延迟', type: 'line', yAxisIndex: 1, data: points.map((point) => point.p95LatencyMs), showSymbol: false, smooth: 0.18, lineStyle: { width: compact ? 1 : 2, type: compact ? 'dashed' : 'solid' } },
      ],
    }
  }, [compact, points, visualStyle])
  return <ChartCanvas option={option} className={compact ? 'gateway-trend-chart compact' : 'gateway-trend-chart'} ariaLabel="在线网关请求速率错误速率和P95延迟趋势图" />
}

export function InstanceCapacityChart({ snapshots, visualStyle }: { snapshots: CapacitySnapshot[]; visualStyle: VisualStyle }) {
  const sorted = useMemo(() => [...snapshots].sort((left, right) => right.schedulable_used / right.declared_capacity - left.schedulable_used / left.declared_capacity), [snapshots])
  const option = useMemo<EChartsCoreOption>(() => {
    const theme = chartThemes[visualStyle]
    const used = sorted.map((item) => Number(Math.min(100, item.schedulable_used / item.declared_capacity * 100).toFixed(1)))
    return {
      ...baseOption(theme),
      color: [theme.teal, theme.remaining],
      legend: { top: 0, right: 5, itemWidth: 10, itemHeight: 5, textStyle: { color: theme.muted, fontSize: 9 }, data: ['已用容量', '剩余容量'] },
      grid: { left: 126, right: 43, top: 29, bottom: 20 },
      xAxis: { type: 'value', max: 100, axisLabel: { color: theme.muted, fontSize: 8, formatter: '{value}%' }, splitLine: { lineStyle: { color: theme.grid, type: 'dashed' } } },
      yAxis: { type: 'category', inverse: true, data: sorted.map((item) => item.instance_id), axisLabel: { color: theme.text, fontSize: 9, width: 114, overflow: 'truncate' }, axisLine: { lineStyle: { color: theme.grid } }, axisTick: { show: false } },
      tooltip: {
        trigger: 'axis',
        backgroundColor: theme.panel,
        borderColor: theme.grid,
        borderWidth: 1,
        textStyle: { color: theme.text, fontSize: 10 },
        axisPointer: { type: 'shadow', shadowStyle: { color: theme.grid, opacity: 0.18 } },
        formatter: (params: unknown) => {
          const items = params as { dataIndex: number }[]
          const item = sorted[items[0]?.dataIndex ?? 0]
          return `${item.instance_id}<br/>容量：${item.schedulable_used} / ${item.declared_capacity}<br/>有效租约：${item.active_lease_count}<br/>心跳在途：${item.reported_inflight}`
        },
      },
      series: [
        { name: '已用容量', type: 'bar', stack: 'capacity', barMaxWidth: 11, data: used, emphasis: { focus: 'series' } },
        { name: '剩余容量', type: 'bar', stack: 'capacity', barMaxWidth: 11, data: used.map((value) => 100 - value), emphasis: { disabled: true }, silent: true },
      ],
    }
  }, [sorted, visualStyle])
  const height = Math.max(260, sorted.length * 23 + 54)
  return <ChartCanvas option={option} className="instance-capacity-chart" height={height} ariaLabel="算子实例容量占用横向对比图" />
}
