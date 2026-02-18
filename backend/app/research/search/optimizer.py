from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl

from app.core.schemas import RunConfig
from app.research.cv import Fold, assert_no_lookahead, build_purged_walk_forward_folds
from app.research.indicators.dsl import sanitize_series
from app.research.indicators.evaluator import (
    CandidateEvaluation,
    EvalCache,
    HorizonScore,
    build_context,
    evaluate_candidate_horizons,
    evaluate_feature_combo,
)
from app.research.indicators.generator import IndicatorGenerator
from app.research.indicators.novelty import NoveltyFilter
from app.research.search.candidate import CandidateIndicator


@dataclass
class SearchOutcome:
    symbol: str
    timeframe: str
    best_candidates: list[tuple[CandidateIndicator, CandidateEvaluation]]
    best_combo: list[CandidateIndicator]
    combo_score: HorizonScore
    folds: list[Fold]


def run_indicator_search(
    frame: pl.DataFrame,
    symbol: str,
    timeframe: str,
    config: RunConfig,
) -> SearchOutcome:
    ctx = build_context(frame)
    close = ctx["close"]
    timestamps = frame["timestamp"].to_numpy().astype(np.int64)

    assert_no_lookahead(
        feature_timestamps=timestamps[:- config.horizon.max_bar],
        target_timestamps=timestamps[config.horizon.max_bar :],
    )

    folds = build_purged_walk_forward_folds(
        n_rows=len(close),
        folds=config.cv.folds,
        max_horizon=config.horizon.max_bar,
        purge_bars=config.cv.purge_bars,
        embargo_bars=config.cv.embargo_bars,
    )

    generator = IndicatorGenerator(seed=config.random_seed + abs(hash((symbol, timeframe))) % 10_000)
    novelty = NoveltyFilter(
        similarity_threshold=config.search.novelty_similarity_threshold,
        collinearity_threshold=config.search.collinearity_threshold,
    )
    cache = EvalCache()

    pool = generator.generate_pool(size=config.search.candidate_pool_size)

    # Stage A: broad screening with novelty filter.
    stage_a: list[tuple[CandidateIndicator, CandidateEvaluation]] = []
    for cand in pool:
        feature = _feature_for_candidate(cand, ctx, cache)
        if not novelty.is_novel_signature(cand):
            continue
        if novelty.is_collinear(feature):
            continue

        eval_result = evaluate_candidate_horizons(
            indicator_id=cand.indicator_id,
            feature=feature,
            close=close,
            folds=folds[:2],
            horizon_min=config.horizon.min_bar,
            horizon_max=config.horizon.max_bar,
            coarse_step=max(config.horizon.coarse_step * 2, 16),
            refine_radius=max(1, config.horizon.refine_radius // 2),
            cache=cache,
        )
        stage_a.append((cand, eval_result))
        novelty.accept(cand, feature)

    stage_a.sort(key=lambda item: item[1].best_score.composite_error)
    stage_a = stage_a[: config.search.stage_a_keep]

    # Stage B: richer evaluation for survivors.
    stage_b: list[tuple[CandidateIndicator, CandidateEvaluation]] = []
    for cand, _ in stage_a:
        feature = _feature_for_candidate(cand, ctx, cache)
        eval_result = evaluate_candidate_horizons(
            indicator_id=cand.indicator_id,
            feature=feature,
            close=close,
            folds=folds,
            horizon_min=config.horizon.min_bar,
            horizon_max=config.horizon.max_bar,
            coarse_step=config.horizon.coarse_step,
            refine_radius=config.horizon.refine_radius,
            cache=cache,
        )
        stage_b.append((cand, eval_result))

    stage_b.sort(key=lambda item: item[1].best_score.composite_error)
    stage_b = stage_b[: config.search.stage_b_keep]

    # Stage C: parameter mutation tuning.
    tuned: list[tuple[CandidateIndicator, CandidateEvaluation]] = []
    for cand, base_eval in stage_b:
        best_pair = (cand, base_eval)
        for trial in range(config.search.tuning_trials):
            mutated = generator.mutate(cand, trial_id=trial)
            if mutated.complexity > 22:
                continue
            feature = _feature_for_candidate(mutated, ctx, cache)
            eval_result = evaluate_candidate_horizons(
                indicator_id=mutated.indicator_id,
                feature=feature,
                close=close,
                folds=folds,
                horizon_min=config.horizon.min_bar,
                horizon_max=config.horizon.max_bar,
                coarse_step=config.horizon.coarse_step,
                refine_radius=config.horizon.refine_radius,
                cache=cache,
            )
            if eval_result.best_score.composite_error < best_pair[1].best_score.composite_error:
                best_pair = (mutated, eval_result)
        tuned.append(best_pair)

    tuned.sort(key=lambda item: item[1].best_score.composite_error)
    tuned = tuned[: config.search.stage_b_keep]

    # Stage D: sparse combo search.
    best_combo, combo_score = _greedy_combo(
        candidates=tuned,
        close=close,
        folds=folds,
        cache=cache,
        context=ctx,
        max_size=config.search.max_combo_size,
    )

    return SearchOutcome(
        symbol=symbol,
        timeframe=timeframe,
        best_candidates=tuned[:10],
        best_combo=best_combo,
        combo_score=combo_score,
        folds=folds,
    )


def _feature_for_candidate(cand: CandidateIndicator, ctx: dict[str, np.ndarray], cache: EvalCache) -> np.ndarray:
    key = cand.expression()
    if key in cache.feature:
        return cache.feature[key]
    feature = sanitize_series(cand.root.eval(ctx))
    cache.feature[key] = feature
    return feature


def _greedy_combo(
    candidates: list[tuple[CandidateIndicator, CandidateEvaluation]],
    close: np.ndarray,
    folds: list[Fold],
    cache: EvalCache,
    context: dict[str, np.ndarray],
    max_size: int,
) -> tuple[list[CandidateIndicator], HorizonScore]:
    if not candidates:
        raise ValueError("No candidates available for combo search")

    sorted_candidates = sorted(candidates, key=lambda x: x[1].best_score.composite_error)
    selected: list[CandidateIndicator] = [sorted_candidates[0][0]]
    best_horizon = sorted_candidates[0][1].best_horizon

    best_matrix = _build_matrix(selected, context, cache)
    best_score = evaluate_feature_combo(
        combo_id="combo_0",
        features=best_matrix,
        close=close,
        folds=folds,
        horizon=best_horizon,
    )

    for _ in range(1, max_size):
        improved = False
        best_candidate: CandidateIndicator | None = None
        best_candidate_score: HorizonScore | None = None

        for cand, cand_eval in sorted_candidates:
            if cand in selected:
                continue
            trial_selected = selected + [cand]
            matrix = _build_matrix(trial_selected, context, cache)
            score = evaluate_feature_combo(
                combo_id="combo_trial",
                features=matrix,
                close=close,
                folds=folds,
                horizon=cand_eval.best_horizon,
            )
            if score.composite_error + 1e-9 < best_score.composite_error:
                if best_candidate_score is None or score.composite_error < best_candidate_score.composite_error:
                    best_candidate = cand
                    best_candidate_score = score

        if best_candidate is not None and best_candidate_score is not None:
            selected.append(best_candidate)
            best_score = best_candidate_score
            improved = True

        if not improved:
            break

    return selected, best_score


def _build_matrix(selected: list[CandidateIndicator], context: dict[str, np.ndarray], cache: EvalCache) -> np.ndarray:
    cols: list[np.ndarray] = []
    for cand in selected:
        key = cand.expression()
        if key not in cache.feature:
            cache.feature[key] = sanitize_series(cand.root.eval(context))
        cols.append(cache.feature[key])
    matrix = np.column_stack(cols)
    return matrix


def search_outcome_to_dict(outcome: SearchOutcome) -> dict[str, Any]:
    return {
        "symbol": outcome.symbol,
        "timeframe": outcome.timeframe,
        "best_combo_ids": [cand.indicator_id for cand in outcome.best_combo],
        "best_combo_expr": [cand.expression() for cand in outcome.best_combo],
        "combo_score": {
            "horizon": outcome.combo_score.horizon,
            "normalized_rmse": outcome.combo_score.normalized_rmse,
            "normalized_mae": outcome.combo_score.normalized_mae,
            "composite_error": outcome.combo_score.composite_error,
            "directional_hit_rate": outcome.combo_score.directional_hit_rate,
        },
        "best_candidates": [
            {
                "indicator_id": cand.indicator_id,
                "expression": cand.expression(),
                "best_horizon": evaluation.best_horizon,
                "composite_error": evaluation.best_score.composite_error,
            }
            for cand, evaluation in outcome.best_candidates
        ],
    }
