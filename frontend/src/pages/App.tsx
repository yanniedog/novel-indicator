import { useEffect, useMemo, useState } from 'react'
import { cancelRun, createRun, exportPine, generateReport, getPlot, getResults, listRuns } from '../api/client'
import type { PlotPayload, ResultSummary, RunStatus } from '../api/types'
import { PlotPanel } from '../components/PlotPanel'

const PLOTS = ['horizon_heatmap', 'forecast_overlay', 'novelty_pareto', 'timeframe_error']

export function App() {
  const [runs, setRuns] = useState<RunStatus[]>([])
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [summary, setSummary] = useState<ResultSummary | null>(null)
  const [plots, setPlots] = useState<Record<string, PlotPayload>>({})
  const [error, setError] = useState<string | null>(null)
  const [loadingCreate, setLoadingCreate] = useState(false)

  const selectedRun = useMemo(() => runs.find((r) => r.run_id === selectedRunId) ?? null, [runs, selectedRunId])

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
              <div><label>Status</label><span>{selectedRun.status}</span></div>
              <div><label>Stage</label><span>{selectedRun.stage}</span></div>
              <div><label>Progress</label><span>{Math.round(selectedRun.progress * 100)}%</span></div>
              <div><label>Updated</label><span>{new Date(selectedRun.updated_at).toLocaleString()}</span></div>
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
