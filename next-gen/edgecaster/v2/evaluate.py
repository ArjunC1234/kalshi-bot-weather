"""Training, prediction, strategy replay, and report writing for Edgecaster v2."""

from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from statistics import mean
from typing import Any

import torch
from torch import nn

from edgecaster.v2.dataset import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    CandidateSet,
    EdgecasterV2Candidate,
    load_candidate_sets,
    load_dataset_for_trades,
    market_history,
    split_sets,
)
from edgecaster.v2.model import EdgecasterV2Config, EdgecasterV2Net
from strategy.metrics import daily_pnl_rows, grouped_metric_rows, summary_metrics
from strategy.orders import PaperTrade


@dataclass
class FeatureSpec:
    means: dict[str, float]
    stds: dict[str, float]
    vocabularies: dict[str, dict[str, int]]

    @classmethod
    def fit(cls, sets: list[CandidateSet]) -> FeatureSpec:
        candidates = [candidate for item in sets for candidate in item.candidates]
        values: dict[str, list[float]] = {name: [] for name in NUMERIC_FEATURES}
        vocabularies = {name: {"<unk>": 0} for name in CATEGORICAL_FEATURES}
        for candidate in candidates:
            for name in NUMERIC_FEATURES:
                parsed = _finite_float(candidate.features.get(name))
                if parsed is not None:
                    values[name].append(parsed)
            for name in CATEGORICAL_FEATURES:
                value = str(candidate.features.get(name, ""))
                if value not in vocabularies[name]:
                    vocabularies[name][value] = len(vocabularies[name])
        means = {name: mean(items) if items else 0.0 for name, items in values.items()}
        stds = {}
        for name, items in values.items():
            if len(items) < 2:
                stds[name] = 1.0
                continue
            variance = sum((item - means[name]) ** 2 for item in items) / len(items)
            stds[name] = max(math.sqrt(variance), 1e-6)
        return cls(means=means, stds=stds, vocabularies=vocabularies)

    def tensors(self, candidate_set: CandidateSet) -> tuple[torch.Tensor, torch.Tensor]:
        numeric, categorical, _ = self.batch_tensors([candidate_set])
        return numeric, categorical

    def batch_tensors(
        self,
        candidate_sets: list[CandidateSet],
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        numeric_rows = []
        categorical_rows = []
        group_rows = []
        for group_index, candidate_set in enumerate(candidate_sets):
            for candidate in candidate_set.candidates:
                numeric_rows.append(
                    [
                        self._numeric(name, candidate.features.get(name))
                        for name in NUMERIC_FEATURES
                    ]
                )
                categorical_rows.append(
                    [
                        self.vocabularies[name].get(str(candidate.features.get(name, "")), 0)
                        for name in CATEGORICAL_FEATURES
                    ]
                )
                group_rows.append(group_index)
        return (
            torch.tensor(numeric_rows, dtype=torch.float32),
            torch.tensor(categorical_rows, dtype=torch.long),
            torch.tensor(group_rows, dtype=torch.long),
        )

    def _numeric(self, name: str, value: Any) -> float:
        parsed = _finite_float(value)
        if parsed is None:
            parsed = self.means[name]
        return (parsed - self.means[name]) / self.stds[name]


@dataclass(frozen=True)
class EdgecasterV2Prediction:
    candidate: EdgecasterV2Candidate
    win_probability: float
    predicted_reward: float
    rank_score: float
    trade_probability: float
    model_mode: str
    training_examples: int
    training_days: int
    calibrated_win_probability: float | None = None
    calibrated_ev: float | None = None
    calibrated_ev_lcb: float | None = None
    calibration_segment: str | None = None
    calibration_count: int = 0


@dataclass(frozen=True)
class CalibrationStats:
    key: str
    count: int
    mean_win: float
    mean_model_win: float
    mean_reward: float
    mean_predicted_reward: float
    std_reward: float


@dataclass(frozen=True)
class CalibrationPolicy:
    stats: dict[str, CalibrationStats]
    global_stats: CalibrationStats
    config: EdgecasterV2Config

    def apply(self, prediction: EdgecasterV2Prediction) -> EdgecasterV2Prediction:
        stats = self._best_stats(prediction)
        local_weight = stats.count / (stats.count + self.config.calibration_shrinkage)
        global_weight = 1.0 - local_weight
        global_win_bias = self.global_stats.mean_win - self.global_stats.mean_model_win
        local_win_bias = stats.mean_win - stats.mean_model_win
        global_reward_bias = (
            self.global_stats.mean_reward - self.global_stats.mean_predicted_reward
        )
        local_reward_bias = stats.mean_reward - stats.mean_predicted_reward
        calibrated_win = _clamp(
            prediction.win_probability
            + local_weight * local_win_bias
            + global_weight * global_win_bias,
            0.001,
            0.999,
        )
        calibrated_reward = (
            prediction.predicted_reward
            + local_weight * local_reward_bias
            + global_weight * global_reward_bias
        )
        candidate_ev = calibrated_win - prediction.candidate.entry_ask
        calibrated_ev = min(candidate_ev, calibrated_reward)
        standard_error = math.sqrt(
            max(calibrated_win * (1.0 - calibrated_win), 0.01) / max(1, stats.count)
        )
        calibrated_ev_lcb = calibrated_ev - self.config.calibration_lcb_z * standard_error
        return replace(
            prediction,
            calibrated_win_probability=calibrated_win,
            calibrated_ev=calibrated_ev,
            calibrated_ev_lcb=calibrated_ev_lcb,
            calibration_segment=stats.key,
            calibration_count=stats.count,
        )

    def _best_stats(self, prediction: EdgecasterV2Prediction) -> CalibrationStats:
        for key in _calibration_keys(prediction):
            stats = self.stats.get(key)
            if stats is not None and stats.count >= self.config.calibration_min_count:
                return stats
        return self.global_stats


def run_fixed_window(
    data_path: str | Path,
    model_report: str | Path,
    output_dir: str | Path,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    config: EdgecasterV2Config,
) -> dict[str, Any]:
    sets = load_candidate_sets(data_path, model_report)
    train_sets, test_sets = split_sets(sets, train_start, train_end, test_start, test_end)
    model, feature_spec, training_summary = train_model(train_sets, config)
    calibration_policy = None
    calibration_summary: dict[str, Any] = {}
    if config.selection_policy == "calibrated":
        validation_date = training_summary.get("validation_date")
        if config.calibration_source == "train":
            calibration_sets = train_sets
        else:
            calibration_sets = [
                item
                for item in train_sets
                if validation_date is not None and item.target_date == validation_date
            ]
            if not calibration_sets:
                calibration_sets = train_sets
        calibration_predictions = predict_sets(
            model,
            feature_spec,
            calibration_sets,
            training_examples=training_summary["training_examples"],
            training_days=training_summary["training_days"],
        )
        calibration_policy = fit_calibration_policy(calibration_predictions, config)
        calibration_summary = calibration_policy_summary(calibration_policy)
    predictions = predict_sets(
        model,
        feature_spec,
        test_sets,
        training_examples=training_summary["training_examples"],
        training_days=training_summary["training_days"],
    )
    if calibration_policy is not None:
        predictions = [calibration_policy.apply(row) for row in predictions]
    dataset = load_dataset_for_trades(data_path)
    trades = select_trades(predictions, market_history(dataset), config)
    summary = {
        "mode": "edgecaster_v2_fixed_window",
        "data_path": str(data_path),
        "model_report": str(model_report),
        "config": asdict(config),
        "train_start_date": train_start,
        "train_end_date": train_end,
        "test_start_date": test_start,
        "test_end_date": test_end,
        "candidate_sets": len(sets),
        "train_sets": len(train_sets),
        "test_sets": len(test_sets),
        "training_examples": training_summary["training_examples"],
        "training_days": training_summary["training_days"],
        "prediction_examples": len(predictions),
        "calibration": calibration_summary,
        **training_summary,
        **summary_metrics(trades),
    }
    write_outputs(
        Path(output_dir),
        summary,
        predictions,
        trades,
        calibration_policy=calibration_policy,
    )
    return {**summary, "output_dir": str(output_dir)}


def run_rolling_eval(
    data_path: str | Path,
    model_report: str | Path,
    output_dir: str | Path,
    train_days: int,
    test_start: str | None,
    test_end: str | None,
    config: EdgecasterV2Config,
) -> dict[str, Any]:
    sets = load_candidate_sets(data_path, model_report)
    target_dates = sorted({item.target_date for item in sets})
    all_predictions: list[EdgecasterV2Prediction] = []
    folds = []
    for index, target_date in enumerate(target_dates):
        if test_start is not None and target_date < test_start:
            continue
        if test_end is not None and target_date > test_end:
            continue
        prior_dates = [value for value in target_dates[:index] if value < target_date]
        if len(prior_dates) < train_days:
            continue
        train_date_set = set(prior_dates[-train_days:])
        train_sets = [item for item in sets if item.target_date in train_date_set]
        test_sets = [item for item in sets if item.target_date == target_date]
        model, feature_spec, training_summary = train_model(train_sets, config)
        predictions = predict_sets(
            model,
            feature_spec,
            test_sets,
            training_examples=training_summary["training_examples"],
            training_days=training_summary["training_days"],
        )
        all_predictions.extend(predictions)
        folds.append(
            {
                "train_start_date": min(train_date_set),
                "train_end_date": max(train_date_set),
                "test_date": target_date,
                **training_summary,
            }
        )
    dataset = load_dataset_for_trades(data_path)
    trades = select_trades(all_predictions, market_history(dataset), config)
    summary = {
        "mode": "edgecaster_v2_rolling_eval",
        "data_path": str(data_path),
        "model_report": str(model_report),
        "config": asdict(config),
        "train_days": train_days,
        "test_start_date": test_start,
        "test_end_date": test_end,
        "folds": folds,
        "candidate_sets": len(sets),
        "prediction_examples": len(all_predictions),
        **summary_metrics(trades),
    }
    write_outputs(Path(output_dir), summary, all_predictions, trades)
    return {**summary, "output_dir": str(output_dir)}


def train_model(
    train_sets: list[CandidateSet],
    config: EdgecasterV2Config,
) -> tuple[EdgecasterV2Net, FeatureSpec, dict[str, Any]]:
    _seed_everything(config.seed)
    train_dates = sorted({item.target_date for item in train_sets})
    validation_date = train_dates[-1] if len(train_dates) >= 3 else None
    fit_sets = [item for item in train_sets if item.target_date != validation_date] or train_sets
    validation_sets = (
        [item for item in train_sets if item.target_date == validation_date]
        if validation_date is not None
        else train_sets
    )
    feature_spec = FeatureSpec.fit(fit_sets)
    model = EdgecasterV2Net(
        numeric_dim=len(NUMERIC_FEATURES),
        categorical_cardinalities=[
            len(feature_spec.vocabularies[name]) for name in CATEGORICAL_FEATURES
        ],
        hidden_size=config.hidden_size,
        dropout=config.dropout,
    )
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    pos_weight = _pos_weight(fit_sets)
    bce = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(pos_weight, dtype=torch.float32))
    huber = nn.HuberLoss(delta=0.25)
    best_loss = float("inf")
    best_state = None
    epochs_without_improvement = 0
    history = []
    for epoch in range(1, config.epochs + 1):
        model.train()
        shuffled = list(fit_sets)
        random.shuffle(shuffled)
        train_loss_values = []
        for batch in _batches(shuffled, 64):
            optimizer.zero_grad()
            loss = _batch_loss(model, feature_spec, batch, config, bce, huber)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 2.0)
            optimizer.step()
            train_loss_values.append(float(loss.detach()))
        train_loss = sum(train_loss_values) / max(1, len(train_loss_values))
        model.eval()
        with torch.no_grad():
            validation_losses = [
                _batch_loss(model, feature_spec, batch, config, bce, huber)
                for batch in _batches(validation_sets, 64)
            ]
            validation_loss = sum(float(item) for item in validation_losses) / max(
                1, len(validation_losses)
            )
        history.append(
            {"epoch": epoch, "train_loss": train_loss, "validation_loss": validation_loss}
        )
        if validation_loss + 1e-5 < best_loss:
            best_loss = validation_loss
            best_state = {key: value.detach().clone() for key, value in model.state_dict().items()}
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
        if epochs_without_improvement >= config.patience:
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    return model, feature_spec, {
        "training_examples": sum(len(item.candidates) for item in train_sets),
        "fit_examples": sum(len(item.candidates) for item in fit_sets),
        "validation_examples": sum(len(item.candidates) for item in validation_sets),
        "training_days": len(train_dates),
        "validation_date": validation_date,
        "epochs_run": len(history),
        "best_validation_loss": best_loss,
        "loss_history": history,
    }


def predict_sets(
    model: EdgecasterV2Net,
    feature_spec: FeatureSpec,
    sets: list[CandidateSet],
    training_examples: int,
    training_days: int,
) -> list[EdgecasterV2Prediction]:
    output = []
    model.eval()
    with torch.no_grad():
        for candidate_set in sets:
            numeric, categorical = feature_spec.tensors(candidate_set)
            scores = model(numeric, categorical)
            win_probabilities = torch.sigmoid(scores["win_logit"]).tolist()
            trade_probabilities = torch.sigmoid(scores["trade_logit"]).tolist()
            for index, candidate in enumerate(candidate_set.candidates):
                output.append(
                    EdgecasterV2Prediction(
                        candidate=candidate,
                        win_probability=float(win_probabilities[index]),
                        predicted_reward=float(scores["reward"][index]),
                        rank_score=float(scores["rank"][index]),
                        trade_probability=float(trade_probabilities[index]),
                        model_mode="trained_edgecaster_v2_set_ranker",
                        training_examples=training_examples,
                        training_days=training_days,
                    )
                )
    return output


def select_trades(
    predictions: list[EdgecasterV2Prediction],
    history: dict[tuple[str, str], list[Any]],
    config: EdgecasterV2Config,
) -> list[PaperTrade]:
    grouped: dict[datetime_key, list[EdgecasterV2Prediction]] = defaultdict(list)
    for prediction in predictions:
        grouped[
            (
                prediction.candidate.city,
                prediction.candidate.event_ticker,
                prediction.candidate.snapshot_hour_utc,
            )
        ].append(prediction)
    open_events: Counter[str] = Counter()
    budget_remaining: dict[str, float] = {}
    trades: list[PaperTrade] = []
    sequence = 1
    for _, snapshot_predictions in sorted(grouped.items(), key=lambda item: item[0][2]):
        candidates = [
            item
            for item in snapshot_predictions
            if _passes_policy(item, config)
            and open_events[item.candidate.event_ticker] < config.max_positions_per_event
        ]
        by_event: dict[str, list[EdgecasterV2Prediction]] = defaultdict(list)
        for candidate in candidates:
            by_event[candidate.candidate.event_ticker].append(candidate)
        selected = [
            sorted(
                event_candidates,
                key=lambda item: _selection_key(item, config),
                reverse=True,
            )[0]
            for event_candidates in by_event.values()
        ]
        for prediction in sorted(
            selected,
            key=lambda item: _selection_key(item, config),
            reverse=True,
        ):
            candidate = prediction.candidate
            budget = budget_remaining.get(candidate.target_date, config.daily_budget)
            contracts = _contracts(candidate, budget, config)
            if contracts <= 0:
                continue
            budget_remaining[candidate.target_date] = budget - contracts * candidate.entry_ask
            open_events[candidate.event_ticker] += 1
            trades.append(_trade(prediction, contracts, history, sequence))
            sequence += 1
    return trades


datetime_key = tuple[str, str, Any]


def write_outputs(
    output: Path,
    summary: dict[str, Any],
    predictions: list[EdgecasterV2Prediction],
    trades: list[PaperTrade],
    calibration_policy: CalibrationPolicy | None = None,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _write_dict_rows(output / "predictions.csv", [_prediction_row(row) for row in predictions])
    _write_dict_rows(output / "trades.csv", [asdict(row) for row in trades])
    _write_dict_rows(output / "daily_pnl.csv", daily_pnl_rows(trades))
    _write_dict_rows(output / "city_metrics.csv", grouped_metric_rows(trades, "city"))
    _write_dict_rows(output / "side_metrics.csv", grouped_metric_rows(trades, "side"))
    _write_dict_rows(output / "ranking_diagnostics.csv", _ranking_diagnostics(predictions))
    if calibration_policy is not None:
        _write_dict_rows(
            output / "calibration_segments.csv",
            [
                asdict(stats)
                for stats in sorted(
                    calibration_policy.stats.values(),
                    key=lambda item: (item.key.split(":", 1)[0], -item.count, item.key),
                )
            ],
        )
    _write_loss_history(output, summary.get("loss_history", []))
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )
    _write_report(output / "edgecaster_v2_report.md", summary)


def _batch_loss(
    model: EdgecasterV2Net,
    feature_spec: FeatureSpec,
    candidate_sets: list[CandidateSet],
    config: EdgecasterV2Config,
    bce: nn.BCEWithLogitsLoss,
    huber: nn.HuberLoss,
) -> torch.Tensor:
    numeric, categorical, group_ids = feature_spec.batch_tensors(candidate_sets)
    candidates = [row for candidate_set in candidate_sets for row in candidate_set.candidates]
    output = model(numeric, categorical, group_ids)
    wins = torch.tensor([row.win_label for row in candidates], dtype=torch.float32)
    rewards = torch.tensor([row.reward for row in candidates], dtype=torch.float32)
    should_trade = torch.tensor(
        [
            1.0
            if row.reward > 0.0
            and row.raw_edge >= config.min_raw_edge
            and row.spread <= config.max_spread
            else 0.0
            for row in candidates
        ],
        dtype=torch.float32,
    )
    return (
        bce(output["win_logit"], wins)
        + config.reward_loss_weight * huber(output["reward"], rewards)
        + config.rank_loss_weight * _pairwise_rank_loss(output["rank"], rewards, group_ids)
        + config.trade_loss_weight * bce(output["trade_logit"], should_trade)
    )


def _pairwise_rank_loss(
    scores: torch.Tensor,
    rewards: torch.Tensor,
    group_ids: torch.Tensor,
) -> torch.Tensor:
    if len(scores) < 2:
        return scores.sum() * 0.0
    losses = []
    for group_id in torch.unique(group_ids):
        mask = group_ids == group_id
        group_scores = scores[mask]
        group_rewards = rewards[mask]
        if len(group_scores) < 2:
            continue
        diffs = group_rewards[:, None] - group_rewards[None, :]
        score_diffs = group_scores[:, None] - group_scores[None, :]
        pair_mask = diffs > 1e-6
        if torch.any(pair_mask):
            losses.append(torch.nn.functional.softplus(-score_diffs[pair_mask]).mean())
    if not losses:
        return scores.sum() * 0.0
    return torch.stack(losses).mean()


def _pos_weight(sets: list[CandidateSet]) -> float:
    positives = sum(row.win_label for item in sets for row in item.candidates)
    total = sum(len(item.candidates) for item in sets)
    negatives = total - positives
    return float(negatives / max(1.0, positives))


def _batches(items: list[CandidateSet], size: int) -> list[list[CandidateSet]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _passes_policy(prediction: EdgecasterV2Prediction, config: EdgecasterV2Config) -> bool:
    candidate = prediction.candidate
    standard_pass = (
        prediction.predicted_reward >= config.min_predicted_reward
        and prediction.trade_probability >= config.min_trade_probability
        and candidate.raw_edge >= config.min_raw_edge
        and candidate.spread <= config.max_spread
        and config.min_entry_price <= candidate.entry_ask <= config.max_entry_price
    )
    if config.selection_policy != "calibrated":
        return standard_pass
    return (
        standard_pass
        and prediction.calibrated_ev is not None
        and prediction.calibrated_ev_lcb is not None
        and prediction.calibrated_ev >= config.min_calibrated_ev
        and prediction.calibrated_ev_lcb >= config.min_calibrated_ev_lcb
    )


def _selection_key(
    prediction: EdgecasterV2Prediction,
    config: EdgecasterV2Config,
) -> tuple[float, float, float, float]:
    if config.selection_policy == "calibrated":
        return (
            prediction.calibrated_ev_lcb if prediction.calibrated_ev_lcb is not None else -999.0,
            prediction.calibrated_ev if prediction.calibrated_ev is not None else -999.0,
            prediction.predicted_reward,
            prediction.trade_probability,
        )
    return (
        prediction.trade_probability,
        prediction.predicted_reward,
        prediction.rank_score,
        prediction.candidate.raw_edge,
    )


def _contracts(
    candidate: EdgecasterV2Candidate,
    budget_remaining: float,
    config: EdgecasterV2Config,
) -> int:
    per_order_budget = min(
        config.max_order_cost,
        budget_remaining,
        config.daily_budget * config.budget_fraction,
    )
    cap = (
        config.max_no_contracts_per_order
        if candidate.side == "no"
        else config.max_contracts_per_order
    )
    return max(0, min(cap, int(per_order_budget // max(0.01, candidate.entry_ask))))


def _trade(
    prediction: EdgecasterV2Prediction,
    contracts: int,
    history: dict[tuple[str, str], list[Any]],
    sequence: int,
) -> PaperTrade:
    candidate = prediction.candidate
    settlement_value = 1.0 if candidate.reward > 0 else 0.0
    pnl = candidate.reward * contracts
    closing_mid = _closing_mid(candidate, history)
    return PaperTrade(
        order_id=f"edgecaster-v2-{sequence:06d}",
        city=candidate.city,
        event_ticker=candidate.event_ticker,
        market_ticker=candidate.market_ticker,
        target_date=candidate.target_date,
        entry_time_utc=candidate.snapshot_hour_utc,
        model_probability=(
            prediction.calibrated_win_probability
            if prediction.calibrated_win_probability is not None
            else prediction.trade_probability
        ),
        entry_price=candidate.entry_ask,
        edge=(
            prediction.calibrated_ev
            if prediction.calibrated_ev is not None
            else prediction.predicted_reward
        ),
        contracts=float(contracts),
        winner_ticker=candidate.winner_ticker,
        settlement_value=settlement_value,
        pnl=pnl,
        roi=pnl / max(1e-9, candidate.entry_ask * contracts),
        hit=1.0 if candidate.reward > 0 else 0.0,
        closing_mid=closing_mid,
        clv=(closing_mid - candidate.entry_ask) if closing_mid is not None else None,
        checkpoint=candidate.features["checkpoint"],
        side=candidate.side,
        bracket_type=candidate.bracket_type,
    )


def fit_calibration_policy(
    predictions: list[EdgecasterV2Prediction],
    config: EdgecasterV2Config,
) -> CalibrationPolicy:
    eligible = [
        row
        for row in predictions
        if row.candidate.spread <= config.max_spread
        and config.min_entry_price <= row.candidate.entry_ask <= config.max_entry_price
    ]
    if not eligible:
        eligible = predictions
    accumulators: dict[str, list[EdgecasterV2Prediction]] = defaultdict(list)
    for prediction in eligible:
        for key in _calibration_keys(prediction):
            accumulators[key].append(prediction)
    stats = {key: _calibration_stats(key, rows) for key, rows in accumulators.items() if rows}
    global_stats = stats.get("global:all")
    if global_stats is None:
        global_stats = _calibration_stats("global:all", eligible)
        stats[global_stats.key] = global_stats
    return CalibrationPolicy(stats=stats, global_stats=global_stats, config=config)


def calibration_policy_summary(policy: CalibrationPolicy) -> dict[str, Any]:
    usable_segments = [
        row for row in policy.stats.values() if row.count >= policy.config.calibration_min_count
    ]
    return {
        "policy": "hierarchical_segment_calibration",
        "source": policy.config.calibration_source,
        "segments": len(policy.stats),
        "usable_segments": len(usable_segments),
        "global_count": policy.global_stats.count,
        "global_mean_win": policy.global_stats.mean_win,
        "global_mean_model_win": policy.global_stats.mean_model_win,
        "global_mean_reward": policy.global_stats.mean_reward,
        "global_mean_predicted_reward": policy.global_stats.mean_predicted_reward,
        "shrinkage": policy.config.calibration_shrinkage,
        "min_count": policy.config.calibration_min_count,
        "lcb_z": policy.config.calibration_lcb_z,
    }


def _calibration_stats(key: str, rows: list[EdgecasterV2Prediction]) -> CalibrationStats:
    count = len(rows)
    rewards = [row.candidate.reward for row in rows]
    mean_reward = sum(rewards) / max(1, count)
    if count > 1:
        variance = sum((reward - mean_reward) ** 2 for reward in rewards) / count
        std_reward = math.sqrt(max(variance, 0.0))
    else:
        std_reward = 0.0
    return CalibrationStats(
        key=key,
        count=count,
        mean_win=sum(row.candidate.win_label for row in rows) / max(1, count),
        mean_model_win=sum(row.win_probability for row in rows) / max(1, count),
        mean_reward=mean_reward,
        mean_predicted_reward=sum(row.predicted_reward for row in rows) / max(1, count),
        std_reward=std_reward,
    )


def _calibration_keys(prediction: EdgecasterV2Prediction) -> list[str]:
    features = prediction.candidate.features
    city = prediction.candidate.city
    side = prediction.candidate.side
    checkpoint_bucket = str(features.get("checkpoint_bucket", "unknown"))
    bracket_type = prediction.candidate.bracket_type
    return [
        f"city_checkpoint_bucket_side:{city}:{checkpoint_bucket}:{side}",
        f"city_side:{city}:{side}",
        f"checkpoint_bucket_side:{checkpoint_bucket}:{side}",
        f"city_checkpoint_bucket:{city}:{checkpoint_bucket}",
        f"city_bracket_type:{city}:{bracket_type}",
        f"bracket_type_side:{bracket_type}:{side}",
        f"price_bucket:{_price_bucket(prediction.candidate.entry_ask)}",
        f"raw_edge_bucket:{_edge_bucket(prediction.candidate.raw_edge)}",
        f"predicted_reward_bucket:{_edge_bucket(prediction.predicted_reward)}",
        f"trade_probability_bucket:{_probability_bucket(prediction.trade_probability)}",
        f"city:{city}",
        f"checkpoint_bucket:{checkpoint_bucket}",
        f"side:{side}",
        "global:all",
    ]


def _price_bucket(value: float) -> str:
    if value < 0.35:
        return "lt_35"
    if value < 0.50:
        return "35_50"
    if value < 0.65:
        return "50_65"
    if value < 0.80:
        return "65_80"
    return "gte_80"


def _edge_bucket(value: float) -> str:
    if value < -0.02:
        return "lt_neg_02"
    if value < 0.0:
        return "neg_02_0"
    if value < 0.02:
        return "0_02"
    if value < 0.05:
        return "02_05"
    if value < 0.08:
        return "05_08"
    return "gte_08"


def _probability_bucket(value: float) -> str:
    if value < 0.35:
        return "lt_35"
    if value < 0.45:
        return "35_45"
    if value < 0.55:
        return "45_55"
    if value < 0.65:
        return "55_65"
    return "gte_65"


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _closing_mid(
    candidate: EdgecasterV2Candidate,
    history: dict[tuple[str, str], list[Any]],
) -> float | None:
    rows = history.get((candidate.event_ticker, candidate.market_ticker), [])
    later = [row for row in rows if row.snapshot_hour_utc >= candidate.snapshot_hour_utc]
    if not later:
        return None
    row = later[-1]
    if candidate.side == "yes":
        if row.yes_bid is None or row.yes_ask is None:
            return None
        return (float(row.yes_bid) + float(row.yes_ask)) / 2.0
    no_bid = row.no_bid if row.no_bid is not None else 1.0 - float(row.yes_ask)
    no_ask = row.no_ask if row.no_ask is not None else 1.0 - float(row.yes_bid)
    return (float(no_bid) + float(no_ask)) / 2.0


def _prediction_row(prediction: EdgecasterV2Prediction) -> dict[str, Any]:
    candidate = prediction.candidate
    return {
        "target_date": candidate.target_date,
        "snapshot_hour_utc": candidate.snapshot_hour_utc.isoformat(),
        "city": candidate.city,
        "event_ticker": candidate.event_ticker,
        "market_ticker": candidate.market_ticker,
        "side": candidate.side,
        "entry_bid": candidate.entry_bid,
        "entry_ask": candidate.entry_ask,
        "spread": candidate.spread,
        "raw_edge": candidate.raw_edge,
        "outcome_probability": candidate.outcome_probability,
        "win_label": candidate.win_label,
        "reward": candidate.reward,
        "winner_ticker": candidate.winner_ticker,
        "win_probability": prediction.win_probability,
        "predicted_reward": prediction.predicted_reward,
        "rank_score": prediction.rank_score,
        "trade_probability": prediction.trade_probability,
        "calibrated_win_probability": prediction.calibrated_win_probability,
        "calibrated_ev": prediction.calibrated_ev,
        "calibrated_ev_lcb": prediction.calibrated_ev_lcb,
        "calibration_segment": prediction.calibration_segment,
        "calibration_count": prediction.calibration_count,
        "model_mode": prediction.model_mode,
        "training_examples": prediction.training_examples,
        "training_days": prediction.training_days,
        **candidate.features,
    }


def _ranking_diagnostics(predictions: list[EdgecasterV2Prediction]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, Any], list[EdgecasterV2Prediction]] = defaultdict(list)
    for prediction in predictions:
        grouped[
            (
                prediction.candidate.city,
                prediction.candidate.event_ticker,
                prediction.candidate.snapshot_hour_utc,
            )
        ].append(prediction)
    rows = []
    for key, items in sorted(grouped.items(), key=lambda item: item[0][2]):
        best_reward = max(items, key=lambda row: row.candidate.reward)
        best_rank = max(items, key=lambda row: row.rank_score)
        best_trade = max(items, key=lambda row: row.trade_probability)
        rows.append(
            {
                "city": key[0],
                "event_ticker": key[1],
                "snapshot_hour_utc": key[2].isoformat(),
                "target_date": items[0].candidate.target_date,
                "best_reward_market_ticker": best_reward.candidate.market_ticker,
                "best_reward_side": best_reward.candidate.side,
                "best_reward": best_reward.candidate.reward,
                "best_rank_market_ticker": best_rank.candidate.market_ticker,
                "best_rank_side": best_rank.candidate.side,
                "best_rank_reward": best_rank.candidate.reward,
                "best_trade_market_ticker": best_trade.candidate.market_ticker,
                "best_trade_side": best_trade.candidate.side,
                "best_trade_reward": best_trade.candidate.reward,
                "rank_hit": best_rank.candidate is best_reward.candidate,
                "trade_hit": best_trade.candidate is best_reward.candidate,
            }
        )
    return rows


def _write_dict_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_loss_history(output: Path, history: list[dict[str, Any]]) -> None:
    _write_dict_rows(output / "loss_history.csv", history)
    if not history:
        return
    try:
        import matplotlib
    except ImportError:
        return
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    charts = output / "charts"
    charts.mkdir(exist_ok=True)
    epochs = [int(row["epoch"]) for row in history]
    plt.figure(figsize=(9, 5))
    plt.plot(epochs, [float(row["train_loss"]) for row in history], label="train")
    plt.plot(epochs, [float(row["validation_loss"]) for row in history], label="validation")
    plt.xlabel("Epoch")
    plt.ylabel("Loss")
    plt.title("Edgecaster v2 Training Loss")
    plt.legend()
    plt.tight_layout()
    plt.savefig(charts / "training_loss.png")
    plt.close()


def _write_report(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(
        "\n".join(
            [
                "# Edgecaster v2 Report",
                "",
                f"- Mode: {summary['mode']}",
                f"- Prediction examples: {summary['prediction_examples']}",
                f"- Trades: {summary['trades']}",
                f"- Total PnL: {summary['total_pnl']:.4f}",
                f"- ROI: {summary['roi']:.4f}",
                f"- Hit rate: {summary['hit_rate']:.4f}",
                f"- Max drawdown: {summary['max_drawdown']:.4f}",
                "",
                "Research-only PyTorch candidate-set ranker. This does not authorize deployment.",
            ]
        ),
        encoding="utf-8",
    )


def _finite_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
