import { useEffect, useMemo, useState } from 'react'
import { cancelRun, createRun, exportPine, generateReport, getPlot, getResults, getTelemetry, listRuns } from '../api/client'
import type { PlotPayload, ResultSummary, RunStatus, TelemetrySnapshot } from '../api/types'
import { PlotPanel } from '../components/PlotPanel'

const PLOTS = ['horizon_heatmap', 'forecast_overlay', 'novelty_pareto', 'timeframe_error']

function fmtSecs(value?: number | null): string {
  if (value == null || !Number.isFinite(value) || value < 0) {
    return 'n/a'
  }
  const total = Math.round(value)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) {
    return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

export function App() {
  const [runs, setRuns] = useState<RunStatus[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [summary, setSummary] = useState<ResultSummary | null>(null)
  const [plots, setPlots] = useState<Record<string, PlotPayload>>({})
  const [telemetry, setTelemetry] = useState<TelemetrySnapshot[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loadingCreate, setLoadingCreate] = useState(false)

  const selectedRun = useMemo(() => runs.find((r) => r.run_id === selectedRunId) ?? null, [runs, selectedRunId])
  const latestTelemetry = useMemo(() => telemetry[telemetry.length - 1] ?? null, [telemetry])

  useEffect(() => {
    const refresh = async () => {
      try {
        const data = await listRuns()
        setRuns(data)
        if (!selectedRunId && data.length > 0) {
          setSelectedRunId(data[0].run_id)
        }
      } catch (e) {
        setError((e as Error).message)
      }
    }
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [selectedRunId])

  useEffect(() => {
    const loadTelemetry = async () => {
      if (!selectedRunId) {
        setTelemetry([])
        return
      }
      try {
        const feed = await getTelemetry(selectedRunId, 240)
        setTelemetry(feed.snapshots)
      } catch {
        setTelemetry([])
      }
    }
    loadTelemetry()
    const t = setInterval(loadTelemetry, 2000)
    return () => clearInterval(t)
  }, [selectedRunId])

  useEffect(() => {
    const load = async () => {
      if (!selectedRunId) {
        return
      }
      const run = runs.find((r) => r.run_id === selectedRunId)
      if (!run || run.status !== 'completed') {
        setSummary(null)
        setPlots({})
        return
      }
      try {
        const result = await getResults(selectedRunId)
        setSummary(result)
        const loaded: Record<string, PlotPayload> = {}
        await Promise.all(
          PLOTS.map(async (plotId) => {
            try {
              loaded[plotId] = await getPlot(selectedRunId, plotId)
            } catch {
              // optional payloads
            }
          }),
        )
        setPlots(loaded)
      } catch (e) {
        setError((e as Error).message)
      }
    }
    load()
  }, [runs, selectedRunId])

  const onCreate = async () => {
    setLoadingCreate(true)
    setError(null)
    try {
      const created = await createRun({})
      setSelectedRunId(created.run_id)
      const data = await listRuns()
      setRuns(data)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoadingCreate(false)
    }
  }

  return (
    <div className="page-shell">
      <div className="aurora" />
      <header className="top-header">
        <h1>Novel Indicator Lab</h1>
        <p>AI-driven symbolic indicator discovery with leakage-safe forecasting and Pine export.</p>
        <div className="controls">
          <button onClick={onCreate} disabled={loadingCreate}>
            {loadingCreate ? 'Launching...' : 'Start Run'}
          </button>
          {selectedRunId && (
            <button className="secondary" onClick={() => cancelRun(selectedRunId)}>
              Cancel Run
            </button>
          )}
          {selectedRunId && (
            <button className="secondary" onClick={() => generateReport(selectedRunId)}>
              Rebuild PDF
            </button>
          )}
          {selectedRunId && (
            <button className="secondary" onClick={() => exportPine(selectedRunId, 3)}>
              Export Pine
            </button>
          )}
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <section className="panel runs-panel">
        <h2>Runs</h2>
        <div className="run-list">
          {runs.map((run) => (
            <button
              key={run.run_id}
              className={`run-item ${selectedRunId === run.run_id ? 'selected' : ''}`}
              onClick={() => setSelectedRunId(run.run_id)}
            >
              <div>
                <strong>{run.run_id}</strong>
                <span>{run.status.toUpperCase()}</span>
              </div>
              <div>
                <small>{run.stage}</small>
                <small>{Math.round(run.progress * 100)}%</small>
              </div>
            </button>
          ))}
        </div>
      </section>

      <section className="panel monitor-panel">
        <h2>Run Monitor</h2>
        {selectedRun ? (
          <>
            <div className="kpis">
              <div>
                <label>Status</label>
                <span>{selectedRun.status}</span>
              </div>
              <div>
                <label>Stage</label>
                <span>{selectedRun.stage}</span>
              </div>
              <div>
                <label>Progress</label>
                <span>{Math.round(selectedRun.progress * 100)}%</span>
              </div>
              <div>
                <label>Updated</label>
                <span>{new Date(selectedRun.updated_at).toLocaleString()}</span>
              </div>
            </div>
            <div className="log-panel">
              {selectedRun.logs.slice(-30).map((log, idx) => (
                <div key={`${log.timestamp}-${idx}`} className="log-row">
                  <span>{new Date(log.timestamp).toLocaleTimeString()}</span>
                  <b>{log.stage}</b>
                  <p>{log.message}</p>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p>No run selected.</p>
        )}
      </section>

      <section className="panel telemetry-panel">
        <h2>Live Telemetry</h2>
        {latestTelemetry ? (
          <>
            <div className="telemetry-bars">
              <div>
                <label>Overall {Math.round(latestTelemetry.overall_progress * 100)}%</label>
                <div className="bar-track">
                  <div className="bar-fill" style={{ width: `${Math.max(0, Math.min(100, latestTelemetry.overall_progress * 100))}%` }} />
                </div>
              </div>
              <div>
                <label>Current Task {Math.round(latestTelemetry.stage_progress * 100)}%</label>
                <div className="bar-track">
                  <div className="bar-fill bar-fill-alt" style={{ width: `${Math.max(0, Math.min(100, latestTelemetry.stage_progress * 100))}%` }} />
                </div>
              </div>
            </div>
            <div className="kpis">
              <div>
                <label>Working On</label>
                <span>{latestTelemetry.working_on}</span>
              </div>
              <div>
                <label>Elapsed / ETA</label>
                <span>{fmtSecs(latestTelemetry.run_elapsed_sec)} / {fmtSecs(latestTelemetry.eta_total_sec)}</span>
              </div>
              <div>
                <label>Task Elapsed / ETA</label>
                <span>{fmtSecs(latestTelemetry.stage_elapsed_sec)} / {fmtSecs(latestTelemetry.eta_stage_sec)}</span>
              </div>
              <div>
                <label>Rate</label>
                <span>{latestTelemetry.rate_units_per_sec.toFixed(4)} u/s</span>
              </div>
              <div>
                <label>CPU (sys/proc)</label>
                <span>{latestTelemetry.system_cpu_percent.toFixed(1)}% / {latestTelemetry.process_cpu_percent.toFixed(1)}%</span>
              </div>
              <div>
                <label>RAM</label>
                <span>{latestTelemetry.ram_used_gb.toFixed(2)} / {latestTelemetry.ram_total_gb.toFixed(2)} GB ({latestTelemetry.ram_percent.toFixed(1)}%)</span>
              </div>
              <div>
                <label>CPU Temp</label>
                <span>{latestTelemetry.cpu_temp_c == null ? 'n/a' : `${latestTelemetry.cpu_temp_c.toFixed(1)} C`}</span>
              </div>
            </div>
            <div className="telemetry-footnote">
              <strong>Achieved:</strong> {latestTelemetry.achieved}
              <br />
              <strong>Remaining:</strong> {latestTelemetry.remaining}
            </div>
          </>
        ) : (
          <p>Telemetry will appear as soon as the run starts writing snapshots.</p>
        )}
      </section>

      <section className="panel results-panel">
        <h2>Results Explorer</h2>
        {summary ? (
          <>
            <div className="universal-card">
              <h3>Universal Recommendation</h3>
              <p>
                Horizon: <b>{summary.universal_recommendation.best_horizon}</b> bars | Composite Error:{' '}
                <b>{summary.universal_recommendation.score.composite_error.toFixed(6)}</b>
              </p>
              <ul>
                {summary.universal_recommendation.indicator_combo.map((i) => (
                  <li key={i.indicator_id}>
                    <code>{i.indicator_id}</code> {i.expression}
                  </li>
                ))}
              </ul>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>TF</th>
                    <th>Horizon</th>
                    <th>Error</th>
                    <th>HitRate</th>
                    <th>PnL</th>
                    <th>MaxDD</th>
                  </tr>
                </thead>
                <tbody>
                  {summary.per_asset_recommendations.slice(0, 30).map((rec) => (
                    <tr key={`${rec.symbol}-${rec.timeframe}`}>
                      <td>{rec.symbol}</td>
                      <td>{rec.timeframe}</td>
                      <td>{rec.best_horizon}</td>
                      <td>{rec.score.composite_error.toFixed(6)}</td>
                      <td>{rec.score.directional_hit_rate.toFixed(3)}</td>
                      <td>{rec.score.pnl_total.toFixed(4)}</td>
                      <td>{rec.score.max_drawdown.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <p>Results will appear when the selected run is completed.</p>
        )}
      </section>

      <section className="panel plots-panel">
        <h2>Visual Diagnostics</h2>
        <div className="plot-grid">
          {Object.values(plots).map((plot) => (
            <PlotPanel key={plot.plot_id} plot={plot} />
          ))}
        </div>
      </section>
    </div>
  )
}
