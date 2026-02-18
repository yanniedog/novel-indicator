from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import polars as pl

from app.core.schemas import RunConfig, RunStageEnum, RunStatusEnum
from app.data.binance import BinanceClient
from app.data.storage import ArtifactStore
from app.db.sqlite import Database
from app.exporters.pine import PineExporter
from app.reporting.plots import build_plot_payloads
from app.reporting.report_builder import ReportBuilder
from app.research.backtest.engine import run_backtest_from_forecasts
from app.research.ranking import build_result_summary
from app.research.search.optimizer import SearchOutcome, run_indicator_search, search_outcome_to_dict
from app.research.telemetry import LiveTelemetry

logger = logging.getLogger(__name__)


@dataclass
class RunnerDeps:
    db: Database
    store: ArtifactStore
    binance: BinanceClient


class ExperimentRunner:
    def __init__(self, deps: RunnerDeps) -> None:
        self.db = deps.db
        self.store = deps.store
        self.binance = deps.binance
        self.report_builder = ReportBuilder(self.store)
        self.pine_exporter = PineExporter(self.store)

    def execute(
        self,
        run_id: str,
        config: RunConfig,
        is_cancelled: Callable[[], bool],
    ) -> None:
        telemetry = LiveTelemetry(run_id=run_id, run_dir=self.store.run_dir(run_id))
        telemetry.start()
        final_status = "completed"
        final_message = "Run completed"
        try:
            self._update(run_id, RunStatusEnum.running, RunStageEnum.universe, 0.02, "Selecting dynamic universe")
            telemetry.update(
                stage=RunStageEnum.universe.value,
                working_on="Selecting dynamic top-volume universe",
                achieved="0 units completed",
                remaining="Universe snapshot pending",
                overall_done=0.0,
                overall_total=1.0,
                stage_done=0.0,
                stage_total=1.0,
            )

            symbols = config.symbols if config.symbols else self.binance.fetch_top_volume_symbols(top_n=config.top_n_symbols)
            total_jobs = max(1, len(symbols) * len(config.timeframes))
            overall_total_units = float(1 + (2 * total_jobs) + 1 + 3)  # universe + ingest + discovery + ranking + artifacts

            telemetry.update(
                stage=RunStageEnum.universe.value,
                working_on="Locking universe snapshot",
                achieved=f"Selected {len(symbols)} symbols",
                remaining=f"{int(overall_total_units - 0)} units remaining",
                overall_done=0.0,
                overall_total=overall_total_units,
                stage_done=0.5,
                stage_total=1.0,
            )

            snapshot = {
                "run_id": run_id,
                "symbols": symbols,
                "timeframes": config.timeframes,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            snapshot_path = self.store.run_dir(run_id) / "universe_snapshot.json"
            self.store.save_json(snapshot_path, snapshot)
            self.db.add_artifact(run_id, "universe_snapshot", str(snapshot_path))

            outcomes: list[SearchOutcome] = []
            backtests: dict[tuple[str, str], dict] = {}

            overall_done = 1.0
            telemetry.update(
                stage=RunStageEnum.universe.value,
                working_on="Universe snapshot complete",
                achieved=f"Universe locked: {len(symbols)} symbols x {len(config.timeframes)} timeframes",
                remaining=f"{int(overall_total_units - overall_done)} units remaining",
                overall_done=overall_done,
                overall_total=overall_total_units,
                stage_done=1.0,
                stage_total=1.0,
            )

            self._update(run_id, RunStatusEnum.running, RunStageEnum.ingest, 0.08, "Downloading and cleaning OHLCV data")
            telemetry.update(
                stage=RunStageEnum.ingest.value,
                working_on="Downloading and cleaning OHLCV data",
                achieved=f"0/{total_jobs} datasets ingested",
                remaining=f"{total_jobs} ingest units + downstream stages",
                overall_done=overall_done,
                overall_total=overall_total_units,
                stage_done=0.0,
                stage_total=float(total_jobs),
            )
            ingest_done = 0
            for symbol in symbols:
                for timeframe in config.timeframes:
                    if is_cancelled():
                        final_status = "canceled"
                        final_message = "Run canceled by user request during ingestion"
                        self._cancel(run_id)
                        return

                    days = config.history_windows.get(timeframe, 365)
                    raw_rows = self.binance.fetch_lookback_klines(symbol=symbol, interval=timeframe, days=days)
                    frame = self._clean_rows(raw_rows)
                    bars_path = self.store.save_bars(run_id, symbol, timeframe, frame)
                    self.db.add_artifact(run_id, "bars", str(bars_path))

                    ingest_done += 1
                    overall_done = 1.0 + ingest_done
                    self._update(
                        run_id,
                        RunStatusEnum.running,
                        RunStageEnum.ingest,
                        min(0.99, overall_done / overall_total_units),
                        f"Ingested {symbol} {timeframe} ({ingest_done}/{total_jobs})",
                    )
                    telemetry.update(
                        stage=RunStageEnum.ingest.value,
                        working_on=f"Ingesting {symbol} {timeframe}",
                        achieved=f"{ingest_done}/{total_jobs} datasets ingested",
                        remaining=f"{total_jobs - ingest_done} ingest units remaining",
                        overall_done=overall_done,
                        overall_total=overall_total_units,
                        stage_done=float(ingest_done),
                        stage_total=float(total_jobs),
                    )

            self._update(run_id, RunStatusEnum.running, RunStageEnum.discovery, 0.18, "Running symbolic indicator discovery")
            telemetry.update(
                stage=RunStageEnum.discovery.value,
                working_on="Running symbolic discovery and optimization",
                achieved=f"0/{total_jobs} discovery jobs complete",
                remaining=f"{total_jobs} discovery units remaining",
                overall_done=overall_done,
                overall_total=overall_total_units,
                stage_done=0.0,
                stage_total=float(total_jobs),
            )

            done = 0
            for symbol in symbols:
                for timeframe in config.timeframes:
                    if is_cancelled():
                        final_status = "canceled"
                        final_message = "Run canceled by user request during discovery"
                        self._cancel(run_id)
                        return

                    frame = self.store.load_bars(run_id, symbol, timeframe)
                    outcome = run_indicator_search(frame=frame, symbol=symbol, timeframe=timeframe, config=config)
                    outcomes.append(outcome)

                    bt = run_backtest_from_forecasts(
                        y_true=outcome.combo_score.y_true,
                        y_pred=outcome.combo_score.y_pred,
                        close_ref=outcome.combo_score.close_ref,
                        fee_bps=config.backtest.fee_bps,
                        slippage_bps=config.backtest.slippage_bps,
                        threshold=config.backtest.signal_threshold,
                    )
                    backtests[(symbol, timeframe)] = bt

                    summary_path = self.store.run_dir(run_id) / "debug" / f"search_{symbol}_{timeframe}.json"
                    self.store.save_json(summary_path, search_outcome_to_dict(outcome))

                    done += 1
                    overall_done = 1.0 + total_jobs + done
                    progress = min(0.99, overall_done / overall_total_units)
                    self._update(
                        run_id,
                        RunStatusEnum.running,
                        RunStageEnum.optimization,
                        progress,
                        f"Scored {symbol} {timeframe} ({done}/{total_jobs})",
                    )
                    telemetry.update(
                        stage=RunStageEnum.optimization.value,
                        working_on=f"Scoring and optimizing {symbol} {timeframe}",
                        achieved=f"{done}/{total_jobs} discovery jobs complete",
                        remaining=f"{total_jobs - done} discovery units remaining",
                        overall_done=overall_done,
                        overall_total=overall_total_units,
                        stage_done=float(done),
                        stage_total=float(total_jobs),
                    )

            self._update(run_id, RunStatusEnum.running, RunStageEnum.ranking, 0.83, "Building universal-first ranking")
            telemetry.update(
                stage=RunStageEnum.ranking.value,
                working_on="Aggregating universal-first ranking",
                achieved="Discovery complete; building leaderboard",
                remaining="Ranking and artifact generation pending",
                overall_done=overall_done,
                overall_total=overall_total_units,
                stage_done=0.0,
                stage_total=1.0,
            )
            result_summary = build_result_summary(run_id=run_id, outcomes=outcomes, backtests=backtests)
            result_json = result_summary.model_dump(mode="json")
            self.db.save_result(run_id, result_json)

            result_path = self.store.run_dir(run_id) / "result_summary.json"
            self.store.save_json(result_path, result_json)
            self.db.add_artifact(run_id, "result_summary", str(result_path))
            overall_done = 1.0 + (2 * total_jobs) + 1.0

            self._update(run_id, RunStatusEnum.running, RunStageEnum.artifacts, 0.9, "Generating visualization payloads")
            artifact_stage_total = 3.0
            artifact_stage_done = 0.0
            telemetry.update(
                stage=RunStageEnum.artifacts.value,
                working_on="Generating visualization payloads",
                achieved="Ranking complete",
                remaining="Plots, report, and Pine exports pending",
                overall_done=overall_done,
                overall_total=overall_total_units,
                stage_done=artifact_stage_done,
                stage_total=artifact_stage_total,
            )
            plot_payloads = build_plot_payloads(outcomes)
            for plot_id, payload in plot_payloads.items():
                self.db.save_plot(run_id, plot_id, payload)
                path = self.store.plot_dir(run_id) / f"{plot_id}.json"
                self.store.save_json(path, payload)
                self.db.add_artifact(run_id, "plot", str(path))
            artifact_stage_done = 1.0
            overall_done = overall_done + 1.0
            telemetry.update(
                stage=RunStageEnum.artifacts.value,
                working_on="Visualization payload generation complete",
                achieved="Plots generated",
                remaining="Report and Pine export pending",
                overall_done=overall_done,
                overall_total=overall_total_units,
                stage_done=artifact_stage_done,
                stage_total=artifact_stage_total,
            )

            self._update(run_id, RunStatusEnum.running, RunStageEnum.artifacts, 0.95, "Generating report and Pine exports")
            telemetry.update(
                stage=RunStageEnum.artifacts.value,
                working_on="Generating professional HTML/PDF report",
                achieved="Plots generated",
                remaining="Report and Pine export pending",
                overall_done=overall_done,
                overall_total=overall_total_units,
                stage_done=artifact_stage_done,
                stage_total=artifact_stage_total,
            )
            report = self.report_builder.build(run_id, result_summary)
            self.db.add_artifact(run_id, "report_html", report.html_path)
            self.db.add_artifact(run_id, "report_pdf", report.pdf_path)
            artifact_stage_done = 2.0
            overall_done = overall_done + 1.0
            telemetry.update(
                stage=RunStageEnum.artifacts.value,
                working_on="Generating PineScript exports",
                achieved="Report generated",
                remaining="Pine export pending",
                overall_done=overall_done,
                overall_total=overall_total_units,
                stage_done=artifact_stage_done,
                stage_total=artifact_stage_total,
            )

            expression_map: dict[str, str] = {}
            for outcome in outcomes:
                for cand in outcome.best_combo:
                    expression_map[cand.expression()] = cand.root.to_pine()
                for cand, _ in outcome.best_candidates:
                    expression_map[cand.expression()] = cand.root.to_pine()

            expression_map_path = self.store.export_dir(run_id) / "expression_to_pine.json"
            self.store.save_json(expression_map_path, expression_map)
            self.db.add_artifact(run_id, "pine_expression_map", str(expression_map_path))

            pine_bundle = self.pine_exporter.export(
                run_id,
                result_summary,
                outcomes,
                top_n=3,
                expression_to_pine_override=expression_map,
            )
            for pine in pine_bundle.files:
                self.db.add_artifact(run_id, "pine", pine.path)
            artifact_stage_done = 3.0
            overall_done = overall_total_units
            telemetry.update(
                stage=RunStageEnum.artifacts.value,
                working_on="Artifacts complete",
                achieved="Plots + report + Pine exports complete",
                remaining="0 units remaining",
                overall_done=overall_done,
                overall_total=overall_total_units,
                stage_done=artifact_stage_done,
                stage_total=artifact_stage_total,
            )

            self._update(run_id, RunStatusEnum.completed, RunStageEnum.finished, 1.0, "Run completed")
        except Exception as exc:
            final_status = "failed"
            final_message = f"Run failed: {exc}"
            logger.exception("run failed", exc_info=exc)
            self._update(run_id, RunStatusEnum.failed, RunStageEnum.finished, 1.0, f"Run failed: {exc}", error=str(exc))
        finally:
            telemetry.stop(final_status=final_status, final_message=final_message)

    def _clean_rows(self, rows: list[list]) -> pl.DataFrame:
        if not rows:
            raise ValueError("No OHLCV rows returned from Binance")

        frame = pl.DataFrame(
            {
                "timestamp": [int(r[0]) for r in rows],
                "open": [float(r[1]) for r in rows],
                "high": [float(r[2]) for r in rows],
                "low": [float(r[3]) for r in rows],
                "close": [float(r[4]) for r in rows],
                "volume": [float(r[5]) for r in rows],
            }
        )
        frame = frame.unique(subset=["timestamp"]).sort("timestamp")

        # Fill gaps by forward filling OHLC and zero volume to keep deterministic indexing.
        step = self._infer_step_ms(frame["timestamp"].to_list())
        min_ts = frame["timestamp"].min()
        max_ts = frame["timestamp"].max()
        full = pl.DataFrame({"timestamp": list(range(min_ts, max_ts + step, step))})
        frame = full.join(frame, on="timestamp", how="left").sort("timestamp")
        frame = frame.with_columns(
            [
                pl.col("open").fill_null(strategy="forward").fill_null(strategy="backward"),
                pl.col("high").fill_null(pl.col("open")),
                pl.col("low").fill_null(pl.col("open")),
                pl.col("close").fill_null(pl.col("open")),
                pl.col("volume").fill_null(0.0),
            ]
        )
        return frame

    @staticmethod
    def _infer_step_ms(timestamps: list[int]) -> int:
        if len(timestamps) < 3:
            return 60_000
        diffs = [b - a for a, b in zip(timestamps[:-1], timestamps[1:]) if b > a]
        if not diffs:
            return 60_000
        diffs.sort()
        return diffs[len(diffs) // 2]

    def _update(
        self,
        run_id: str,
        status: RunStatusEnum,
        stage: RunStageEnum,
        progress: float,
        message: str,
        error: str | None = None,
    ) -> None:
        self.db.update_run_status(run_id, status=status, stage=stage, progress=progress, error=error)
        self.db.add_log(run_id, stage=stage, message=message)

    def _cancel(self, run_id: str) -> None:
        self.db.update_run_status(
            run_id,
            status=RunStatusEnum.canceled,
            stage=RunStageEnum.finished,
            progress=1.0,
            error="Canceled by user",
        )
        self.db.add_log(run_id, RunStageEnum.finished, "Run canceled")


def config_hash(config: RunConfig) -> str:
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
