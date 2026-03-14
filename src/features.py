import os
from typing import Any, Dict, Iterable, List


def _infer_extension(name: str) -> str:
    _, ext = os.path.splitext(name)
    return ext.lower()


def _is_test_file(name: str) -> bool:
    lower = name.lower()
    return lower.startswith("test_") or lower.endswith("_test.py") or "test" in lower


def build_feature_row(file_rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a raw city_data entry into a flat feature dict suitable for ML and DB storage.

    Expected keys in file_rec (from scanner2/city_data2.json):
      - name, size, w, d, h, color
    """
    name = str(file_rec.get("name") or "")
    size = int(file_rec.get("size") or 0)
    width = float(file_rec.get("w") or 0.0)
    depth = float(file_rec.get("d") or 0.0)
    height = float(file_rec.get("h") or 0.0)

    complexity = height / 2.0 if height else 0.0
    extension = _infer_extension(name)
    is_test = _is_test_file(name)
    area = width * depth
    aspect_ratio = width / depth if depth not in (0, 0.0) else 0.0

    return {
        "path": name,  # we do not currently track full paths; treat name as path surrogate
        "name": name,
        "size": size,
        "width": width,
        "depth": depth,
        "height": height,
        "complexity": complexity,
        "extension": extension,
        "is_test_file": is_test,
        "area": area,
        "aspect_ratio": aspect_ratio,
    }


def build_feature_rows(files: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Vectorized helper to build feature rows for an iterable of file dicts."""
    return [build_feature_row(f) for f in files]

