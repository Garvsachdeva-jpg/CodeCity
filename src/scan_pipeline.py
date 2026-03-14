import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from . import features, storage, model_server


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_scanner(repo_url: str, github_token: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Invoke scanner2.py for the given repo and return the parsed city_data list.

    This preserves the existing behavior of writing city_data2.json to disk
    while also giving us the in-memory list for downstream processing.
    """
    cmd = [sys.executable, "scanner2.py", repo_url]
    if github_token:
        cmd.append(github_token)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=BASE_DIR,
    )

    if result.returncode != 0:
        error_msg = result.stderr if result.stderr else result.stdout
        raise RuntimeError(f"Analysis failed: {error_msg}")

    data_file = os.path.join(BASE_DIR, "city_data2.json")
    if not os.path.exists(data_file):
        raise FileNotFoundError(
            "No data generated - check if repository has supported source files."
        )

    with open(data_file, "r") as f:
        city_data = json.load(f)

    return city_data


def analyze_and_store(
    repo_url: str,
    label: str,
    snapshot_meta: Dict[str, Any],
    github_token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    High-level pipeline:
      1. Run scanner to build city_data2.json and parse it.
      2. Build feature rows for each file.
      3. Persist snapshot and file rows into SQLite.
    """
    city_data = run_scanner(repo_url, github_token=github_token)
    feature_rows = features.build_feature_rows(city_data)

    # Attach ML-based scores if models are available
    risk_scores = model_server.predict_risk(feature_rows)
    anomaly_scores = model_server.score_anomaly(feature_rows)
    for rec, risk, anom in zip(city_data, risk_scores, anomaly_scores):
        rec["risk_score"] = float(risk)
        rec["anomaly_score"] = float(anom)

    storage.upsert_snapshot_and_files(
        repo_url=repo_url,
        snapshot_meta=snapshot_meta,
        files=city_data,
        feature_rows=feature_rows,
    )
    return city_data

