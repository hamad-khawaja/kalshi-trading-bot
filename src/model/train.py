"""LightGBM model training pipeline."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()


class ModelTrainer:
    """Trains LightGBM model on historical feature/outcome data.

    Uses walk-forward validation to avoid look-ahead bias:
    train on data up to time T, validate on T to T+delta, then
    slide forward.
    """

    DEFAULT_PARAMS = {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_depth": 6,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "verbose": -1,
    }

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def load_training_data(self) -> tuple[pd.DataFrame, pd.Series]:
        """Load features and outcomes from SQLite.

        Joins predictions table (features_json) with outcomes table
        to create labeled training data.
        """
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("""
                SELECT p.features_json, p.model_probability, p.implied_probability,
                       o.result
                FROM predictions p
                JOIN outcomes o ON p.market_ticker = o.market_ticker
                WHERE o.result IS NOT NULL AND p.features_json IS NOT NULL
                ORDER BY p.timestamp
            """)
            rows = await cursor.fetchall()

        if not rows:
            raise ValueError("No training data available. Run the bot to collect data first.")

        features_list = []
        labels = []

        for row in rows:
            features_json, model_prob, implied_prob, result = row
            try:
                features = json.loads(features_json)
                features_list.append(features)
                labels.append(1 if result == "yes" else 0)
            except (json.JSONDecodeError, TypeError):
                continue

        X = pd.DataFrame(features_list)
        y = pd.Series(labels, name="target")

        logger.info("training_data_loaded", samples=len(X), positive_rate=y.mean())
        return X, y

    def train(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        params: dict | None = None,
        num_boost_round: int = 500,
        early_stopping_rounds: int = 50,
        val_fraction: float = 0.2,
    ) -> object:
        """Train LightGBM binary classifier with walk-forward validation.

        Returns trained LightGBM Booster.
        """
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError(
                "lightgbm is required for training. "
                "Install with: pip install kalshi-btc-bot[ml]"
            )

        model_params = {**self.DEFAULT_PARAMS, **(params or {})}

        # Walk-forward split: last val_fraction of data for validation
        split_idx = int(len(X) * (1 - val_fraction))
        X_train, X_val = X.iloc[:split_idx], X.iloc[split_idx:]
        y_train, y_val = y.iloc[:split_idx], y.iloc[split_idx:]

        train_data = lgb.Dataset(X_train, label=y_train)
        val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)

        model = lgb.train(
            model_params,
            train_data,
            valid_sets=[val_data],
            num_boost_round=num_boost_round,
            callbacks=[lgb.early_stopping(early_stopping_rounds)],
        )

        # Evaluate
        metrics = self.evaluate(model, X_val, y_val)
        logger.info("model_trained", **metrics)

        return model

    def evaluate(
        self, model: object, X_test: pd.DataFrame, y_test: pd.Series
    ) -> dict:
        """Compute evaluation metrics."""
        from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

        predictions = model.predict(X_test)  # type: ignore
        predictions = np.clip(predictions, 0.01, 0.99)

        return {
            "auc": round(roc_auc_score(y_test, predictions), 4),
            "log_loss": round(log_loss(y_test, predictions), 4),
            "brier_score": round(brier_score_loss(y_test, predictions), 4),
            "samples": len(y_test),
            "positive_rate": round(y_test.mean(), 4),
            "mean_prediction": round(predictions.mean(), 4),
        }

    @staticmethod
    def save_model(model: object, path: str) -> None:
        """Save trained model to file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        model.save_model(path)  # type: ignore
        logger.info("model_saved", path=path)
