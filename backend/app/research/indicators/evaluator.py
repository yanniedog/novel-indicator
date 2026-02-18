from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from app.research.cv import Fold
from app.research.models.forecaster import RidgeForecaster, mae, rmse


@dataclass
class HorizonScore:
    horizon: int
    normalized_rmse: float
    normalized_mae: float
    composite_error: float
    directional_hit_rate: float
    y_true: np.ndarray
    y_pred: np.ndarray
    close_ref: np.ndarray


@dataclass
class CandidateEvaluation:
    best_horizon: int
    best_score: HorizonScore
    all_scores: dict[int, HorizonScore]


class EvalCache:
    def __init__(self) -> None:
        self.feature: dict[str, np.ndarray] = {}
        self.horizon_scores: dict[tuple[str, int], HorizonScore] = {}


def build_context(frame: pl.DataFrame) -> dict[str, np.ndarray]:
    close = frame["close"].to_numpy().astype(np.float64)
    open_ = frame["open"].to_numpy().astype(np.float64)
    high = frame["high"].to_numpy().astype(np.float64)
    low = frame["low"].to_numpy().astype(np.float64)
    volume = frame["volume"].to_numpy().astype(np.float64)

    logret = np.zeros_like(close)
    logret[1:] = np.log((close[1:] + 1e-9) / (close[:-1] + 1e-9))

    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "hlc3": (high + low + close) / 3.0,
        "ohlc4": (open_ + high + low + close) / 4.0,
        "logret": logret,
        "range": high - low,
    }


def evaluate_candidate_horizons(
    indicator_id: str,
    feature: np.ndarray,
    close: np.ndarray,
    folds: list[Fold],
    horizon_min: int,
    horizon_max: int,
    coarse_step: int,
    refine_radius: int,
    cache: EvalCache,
) -> CandidateEvaluation:
    coarse_horizons = sorted(set([horizon_min] + list(range(horizon_min, horizon_max + 1, coarse_step)) + [horizon_max]))
    coarse_scores: dict[int, HorizonScore] = {}
    for h in coarse_horizons:
        coarse_scores[h] = _score_horizon(indicator_id, feature, close, folds, h, cache)

    ranked = sorted(coarse_scores.values(), key=lambda s: s.composite_error)
    seed_horizons = [x.horizon for x in ranked[: min(7, len(ranked))]]

    fine_horizons: set[int] = set(coarse_horizons)
    for h in seed_horizons:
        for delta in range(-refine_radius, refine_radius + 1):
            cand = h + delta
            if horizon_min <= cand <= horizon_max:
                fine_horizons.add(cand)

    all_scores = dict(coarse_scores)
    for h in sorted(fine_horizons):
        if h not in all_scores:
            all_scores[h] = _score_horizon(indicator_id, feature, close, folds, h, cache)

    best = min(all_scores.values(), key=lambda s: s.composite_error)
    return CandidateEvaluation(best_horizon=best.horizon, best_score=best, all_scores=all_scores)


def evaluate_feature_combo(
    combo_id: str,
    features: np.ndarray,
    close: np.ndarray,
    folds: list[Fold],
    horizon: int,
) -> HorizonScore:
    return _score_horizon(combo_id, features, close, folds, horizon, cache=None)


def _score_horizon(
    key: str,
    feature: np.ndarray,
    close: np.ndarray,
    folds: list[Fold],
    horizon: int,
    cache: EvalCache | None,
) -> HorizonScore:
    if cache is not None:
        cache_key = (key, horizon)
        if cache_key in cache.horizon_scores:
            return cache.horizon_scores[cache_key]

    y = make_target(close, horizon)

    fold_true: list[np.ndarray] = []
    fold_pred: list[np.ndarray] = []
    fold_ref: list[np.ndarray] = []

    for fold in folds:
        train_idx = _valid_indices(fold.train_idx, feature, y)
        val_idx = _valid_indices(fold.val_idx, feature, y)
        if len(train_idx) < 30 or len(val_idx) < 20:
            continue

        if feature.ndim == 1:
            x_train = feature[train_idx][:, None]
            x_val = feature[val_idx][:, None]
        else:
            x_train = feature[train_idx]
            x_val = feature[val_idx]

        y_train = y[train_idx]
        y_val = y[val_idx]

        model = RidgeForecaster(alpha=1.0)
        model.fit(x_train, y_train)
        pred = model.predict(x_val)

        fold_true.append(y_val)
        fold_pred.append(pred)
        fold_ref.append(close[val_idx])

    if not fold_true:
        huge = HorizonScore(
            horizon=horizon,
            normalized_rmse=9_999.0,
            normalized_mae=9_999.0,
            composite_error=9_999.0,
            directional_hit_rate=0.0,
            y_true=np.array([]),
            y_pred=np.array([]),
            close_ref=np.array([]),
        )
        if cache is not None:
            cache.horizon_scores[(key, horizon)] = huge
        return huge

    y_true = np.concatenate(fold_true)
    y_pred = np.concatenate(fold_pred)
    close_ref = np.concatenate(fold_ref)

    nrmse = rmse(y_true, y_pred) / (np.std(y_true) + 1e-9)
    nmae = mae(y_true, y_pred) / (np.mean(np.abs(y_true)) + 1e-9)
    composite = 0.5 * (nrmse + nmae)

    direction_true = np.sign(y_true - close_ref)
    direction_pred = np.sign(y_pred - close_ref)
    hit_rate = float(np.mean(direction_true == direction_pred))

    score = HorizonScore(
        horizon=horizon,
        normalized_rmse=float(nrmse),
        normalized_mae=float(nmae),
        composite_error=float(composite),
        directional_hit_rate=hit_rate,
        y_true=y_true,
        y_pred=y_pred,
        close_ref=close_ref,
    )
    if cache is not None:
        cache.horizon_scores[(key, horizon)] = score
    return score


def make_target(close: np.ndarray, horizon: int) -> np.ndarray:
    out = np.full_like(close, np.nan, dtype=np.float64)
    if horizon >= len(close):
        return out
    out[:-horizon] = close[horizon:]
    return out


def _valid_indices(indices: np.ndarray, feature: np.ndarray, y: np.ndarray) -> np.ndarray:
    if feature.ndim == 1:
        mask_feature = np.isfinite(feature)
    else:
        mask_feature = np.all(np.isfinite(feature), axis=1)
    valid = mask_feature & np.isfinite(y)
    return indices[valid[indices]]
