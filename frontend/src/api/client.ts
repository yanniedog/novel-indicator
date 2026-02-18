import type { PlotPayload, ResultSummary, RunConfig, RunCreated, RunStatus, TelemetryFeed } from './types'

async function asJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text()
    throw new Error(text || `HTTP ${response.status}`)
  }
  return (await response.json()) as T
}

export async function createRun(config: Partial<RunConfig>): Promise<RunCreated> {
  const payload = {
    top_n_symbols: config.top_n_symbols ?? 10,
    timeframes: config.timeframes ?? ['5m', '1h', '4h'],
    budget_minutes: config.budget_minutes ?? 120,
    random_seed: config.random_seed ?? 42,
  }
  const response = await fetch('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return asJson<RunCreated>(response)
}

export async function listRuns(): Promise<RunStatus[]> {
  const response = await fetch('/api/runs')
  return asJson<RunStatus[]>(response)
}

export async function getRun(runId: string): Promise<RunStatus> {
  const response = await fetch(`/api/runs/${runId}`)
  return asJson<RunStatus>(response)
}

export async function getResults(runId: string): Promise<ResultSummary> {
  const response = await fetch(`/api/runs/${runId}/results`)
  return asJson<ResultSummary>(response)
}

export async function getPlot(runId: string, plotId: string): Promise<PlotPayload> {
  const response = await fetch(`/api/runs/${runId}/plots/${plotId}`)
  return asJson<PlotPayload>(response)
}

export async function getTelemetry(runId: string, limit = 300): Promise<TelemetryFeed> {
  const response = await fetch(`/api/runs/${runId}/telemetry?limit=${limit}`)
  return asJson<TelemetryFeed>(response)
}

export async function cancelRun(runId: string): Promise<void> {
  const response = await fetch(`/api/runs/${runId}/cancel`, { method: 'POST' })
  if (!response.ok) {
    throw new Error(await response.text())
  }
}

export async function generateReport(runId: string): Promise<void> {
  const response = await fetch(`/api/runs/${runId}/report`, { method: 'POST' })
  if (!response.ok) {
    throw new Error(await response.text())
  }
}

export async function exportPine(runId: string, topN = 3): Promise<void> {
  const response = await fetch(`/api/runs/${runId}/exports/pine`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ top_n: topN }),
  })
  if (!response.ok) {
    throw new Error(await response.text())
  }
}
