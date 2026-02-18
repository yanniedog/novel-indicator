import Plot from 'react-plotly.js'
import type { PlotPayload } from '../api/types'

type HeatmapPayload = {
  type: 'heatmap'
  x: number[]
  y: string[]
  z: number[][]
}

type LinePayload = {
  type: 'line'
  x: number[]
  series: Array<{ name: string; values: number[] }>
}

type ScatterPayload = {
  type: 'scatter'
  points: Array<{ label: string; complexity: number; error: number }>
}

type BarPayload = {
  type: 'bar'
  categories: string[]
  values: number[]
}

type TablePayload = {
  type: 'table'
  rows: Array<{ label: string; error: number; hit_rate: number; horizon: number }>
}

function asObject(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object') {
    return null
  }
  return value as Record<string, unknown>
}

export function PlotPanel({ plot }: { plot: PlotPayload }) {
  const payload = asObject(plot.payload)
  const kind = payload?.type

  if (kind === 'heatmap') {
    const p = payload as unknown as HeatmapPayload
    return (
      <div className="plot-card">
        <h3>{plot.title}</h3>
        <Plot
          data={[{ type: 'heatmap', x: p.x, y: p.y, z: p.z, colorscale: 'Viridis' }]}
          layout={{ margin: { t: 20, r: 10, b: 40, l: 80 }, paper_bgcolor: '#fff', plot_bgcolor: '#fff' }}
          style={{ width: '100%', height: '320px' }}
          useResizeHandler
        />
      </div>
    )
  }

  if (kind === 'line') {
    const p = payload as unknown as LinePayload
    return (
      <div className="plot-card">
        <h3>{plot.title}</h3>
        <Plot
          data={p.series.map((s) => ({ type: 'scatter', mode: 'lines', name: s.name, x: p.x, y: s.values }))}
          layout={{ margin: { t: 20, r: 10, b: 40, l: 40 }, paper_bgcolor: '#fff', plot_bgcolor: '#fff' }}
          style={{ width: '100%', height: '320px' }}
          useResizeHandler
        />
      </div>
    )
  }

  if (kind === 'scatter') {
    const p = payload as unknown as ScatterPayload
    return (
      <div className="plot-card">
        <h3>{plot.title}</h3>
        <Plot
          data={[
            {
              type: 'scatter',
              mode: 'markers',
              x: p.points.map((pt) => pt.complexity),
              y: p.points.map((pt) => pt.error),
              text: p.points.map((pt) => pt.label),
              marker: { size: 8, color: '#d65a31' },
            },
          ]}
          layout={{ margin: { t: 20, r: 10, b: 40, l: 40 }, paper_bgcolor: '#fff', plot_bgcolor: '#fff' }}
          style={{ width: '100%', height: '320px' }}
          useResizeHandler
        />
      </div>
    )
  }

  if (kind === 'bar') {
    const p = payload as unknown as BarPayload
    return (
      <div className="plot-card">
        <h3>{plot.title}</h3>
        <Plot
          data={[{ type: 'bar', x: p.categories, y: p.values, marker: { color: '#005f73' } }]}
          layout={{ margin: { t: 20, r: 10, b: 40, l: 40 }, paper_bgcolor: '#fff', plot_bgcolor: '#fff' }}
          style={{ width: '100%', height: '320px' }}
          useResizeHandler
        />
      </div>
    )
  }

  if (kind === 'table') {
    const p = payload as unknown as TablePayload
    return (
      <div className="plot-card">
        <h3>{plot.title}</h3>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Asset</th>
                <th>Error</th>
                <th>HitRate</th>
                <th>Horizon</th>
              </tr>
            </thead>
            <tbody>
              {p.rows.slice(0, 20).map((r) => (
                <tr key={r.label}>
                  <td>{r.label}</td>
                  <td>{r.error.toFixed(6)}</td>
                  <td>{r.hit_rate.toFixed(3)}</td>
                  <td>{r.horizon}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    )
  }

  return (
    <div className="plot-card">
      <h3>{plot.title}</h3>
      <p>Unsupported payload.</p>
    </div>
  )
}
