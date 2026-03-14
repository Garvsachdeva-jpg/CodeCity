import json
import os
from functools import lru_cache
from typing import Any, Dict, Iterable, List

import joblib
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(BASE_DIR, "models")


def _load_model_and_meta(model_name: str):
    model_path = os.path.join(MODELS_DIR, f"{model_name}.joblib")
    meta_path = os.path.join(MODELS_DIR, f"{model_name}_meta.json")
    if not os.path.exists(model_path) or not os.path.exists(meta_path):
        return None, None
    model = joblib.load(model_path)
    with open(meta_path, "r") as f:
        meta = json.load(f)
    return model, meta


@lru_cache(maxsize=1)
def get_risk_model():
    return _load_model_and_meta("risk_model")


@lru_cache(maxsize=1)
def get_anomaly_model():
    return _load_model_and_meta("anomaly_model")


def _features_to_matrix(
    feature_rows: Iterable[Dict[str, Any]], feature_cols: List[str]
) -> np.ndarray:
    matrix = []
    for row in feature_rows:
        matrix.append([row.get(col, 0.0) for col in feature_cols])
    return np.asarray(matrix, dtype=float)


def _fallback_risk_scores(feature_rows: List[Dict[str, Any]]) -> List[float]:
    """
    Heuristic risk when no trained model is available:
    normalize complexity into [0, 1] across current snapshot.
    """
    if not feature_rows:
        return []
    complexities = np.asarray(
        [float(r.get("complexity", 0.0)) for r in feature_rows], dtype=float
    )
    c_min = float(complexities.min())
    c_max = float(complexities.max())
    if c_max == c_min:
        return [0.0 for _ in feature_rows]
    scores = (complexities - c_min) / (c_max - c_min)
    return scores.tolist()


def _fallback_anomaly_scores(feature_rows: List[Dict[str, Any]]) -> List[float]:
    """
    Heuristic anomaly when no trained model is available:
    z-score of complexity, shifted to be non-negative.
    """
    if not feature_rows:
        return []
    complexities = np.asarray(
        [float(r.get("complexity", 0.0)) for r in feature_rows], dtype=float
    )
    mean = float(complexities.mean())
    std = float(complexities.std()) or 1.0
    z = (complexities - mean) / std
    # shift to be >= 0 for nicer display
    z_shifted = z - z.min()
    return z_shifted.tolist()


def predict_risk(feature_rows: Iterable[Dict[str, Any]]) -> List[float]:
    feature_rows = list(feature_rows)
    model, meta = get_risk_model()
    if model is None or meta is None:
        return _fallback_risk_scores(feature_rows)
    X = _features_to_matrix(feature_rows, meta["feature_cols"])
    proba = model.predict_proba(X)[:, 1]
    return proba.tolist()


def score_anomaly(feature_rows: Iterable[Dict[str, Any]]) -> List[float]:
    feature_rows = list(feature_rows)
    model, meta = get_anomaly_model()
    if model is None or meta is None:
        return _fallback_anomaly_scores(feature_rows)
    X = _features_to_matrix(feature_rows, meta["feature_cols"])
    # Higher = more anomalous, following notebook convention
    scores = -model.score_samples(X)
    return scores.tolist()


