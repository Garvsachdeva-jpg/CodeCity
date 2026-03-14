from flask import Flask, request, jsonify, send_file
import json
import os
import sys
from datetime import datetime

from src import scan_pipeline


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

app = Flask(__name__, static_folder='.', static_url_path='')


@app.route('/health', methods=['GET'])
def health():
    """Simple health check endpoint."""
    return jsonify({"status": "ok"}), 200


@app.route('/')
def index():
    """Serve the Pro UI."""
    return send_file('index.html')



@app.route('/api/analyze', methods=['POST'])
def analyze():
    """
    Analyze a GitHub repository and create a saved snapshot.

    Request JSON:
    {
      "repo_url": "...",
      "label": "optional human-friendly snapshot name",
      "github_token": "optional GitHub personal access token"
    }
    """
    data = request.get_json() or {}
    repo_url = (data.get('repo_url') or '').strip()
    label = (data.get('label') or '').strip()
    
    # Prioritize token from request, fall back to environment variable
    github_token = (data.get('github_token') or os.environ.get('GITHUB_TOKEN') or '').strip()

    if not repo_url:
        return jsonify({'error': 'Repository URL is required'}), 400

    print(f"[PRO] Received repo URL: {repo_url}", file=sys.stderr)

    try:
        # Create a snapshot record (metadata first so it can be reused by downstream pipelines)
        timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        snapshot_id = f"{timestamp}"
        snapshot_meta = {
            "id": snapshot_id,
            "created_at": timestamp,
            "repo_url": repo_url,
            "label": label or repo_url,
            # file_count will be filled after analysis
            "file_count": 0,
        }

        # Run the scanner + feature engineering + SQLite storage pipeline
        city_data = scan_pipeline.analyze_and_store(
            repo_url=repo_url,
            label=label,
            snapshot_meta=snapshot_meta,
            github_token=github_token or None,
        )
        snapshot_meta["file_count"] = len(city_data)

        snapshot_path = os.path.join(SNAPSHOT_DIR, f"{snapshot_id}.json")
        with open(snapshot_path, 'w') as f:
            json.dump({"meta": snapshot_meta, "data": city_data}, f)

        print(f"[PRO] Snapshot saved at {snapshot_path}", file=sys.stderr)

        return jsonify({
            'success': True,
            'data': city_data,
            'snapshot': snapshot_meta,
            'message': "Analysis complete",
        })

    except Exception as e:
        print(f"[PRO] Exception: {str(e)}", file=sys.stderr)
        return jsonify({'error': str(e)}), 500


@app.route('/api/data', methods=['GET'])
def get_current_data():
    """
    Get the latest city data in this pro instance (not tied to a snapshot).
    """
    data_file = os.path.join(BASE_DIR, 'city_data2.json')
    if os.path.exists(data_file):
        try:
            with open(data_file, 'r') as f:
                return jsonify(json.load(f))
        except json.JSONDecodeError:
            return jsonify({'error': 'The city_data2.json file is not valid JSON.'}), 400
    return jsonify([])


@app.route('/api/snapshots', methods=['GET'])
def list_snapshots():
    """Return metadata for all saved snapshots."""
    snapshots = []
    for name in sorted(os.listdir(SNAPSHOT_DIR)):
        if not name.endswith('.json'):
            continue
        path = os.path.join(SNAPSHOT_DIR, name)
        try:
            with open(path, 'r') as f:
                payload = json.load(f)
            meta = payload.get('meta', {})
            snapshots.append(meta)
        except Exception as e:
            print(f"[PRO] Failed to load snapshot {name}: {e}", file=sys.stderr)
    # Sort newest first
    snapshots.sort(key=lambda s: s.get('id', ''), reverse=True)
    return jsonify(snapshots)


@app.route('/api/snapshots/<snapshot_id>', methods=['GET'])
def get_snapshot(snapshot_id):
    """Return data for a single snapshot."""
    path = os.path.join(SNAPSHOT_DIR, f"{snapshot_id}.json")
    if not os.path.exists(path):
        return jsonify({'error': 'Snapshot not found'}), 404
    with open(path, 'r') as f:
        payload = json.load(f)
    return jsonify(payload)


@app.route('/api/snapshots/<snapshot_id>/risk', methods=['GET'])
def get_snapshot_risk(snapshot_id):
    """
    Return files and their risk/anomaly scores for a snapshot,
    sorted by risk descending.
    """
    path = os.path.join(SNAPSHOT_DIR, f"{snapshot_id}.json")
    if not os.path.exists(path):
        return jsonify({'error': 'Snapshot not found'}), 404
    with open(path, 'r') as f:
        payload = json.load(f)
    data = payload.get('data', [])
    enriched = []
    for rec in data:
        enriched.append({
            "name": rec.get("name"),
            "size": rec.get("size"),
            "h": rec.get("h"),
            "risk_score": rec.get("risk_score", 0.0),
            "anomaly_score": rec.get("anomaly_score", 0.0),
        })
    enriched.sort(key=lambda r: r.get("risk_score", 0.0), reverse=True)
    return jsonify(enriched)


@app.route('/api/diff', methods=['GET'])
def diff_snapshots():
    """Compare two snapshots and return the delta."""
    snap1_id = request.args.get('snap1')
    snap2_id = request.args.get('snap2')

    if not snap1_id or not snap2_id:
        return jsonify({'error': 'Two snapshot IDs are required'}), 400

    path1 = os.path.join(SNAPSHOT_DIR, f"{snap1_id}.json")
    path2 = os.path.join(SNAPSHOT_DIR, f"{snap2_id}.json")

    if not os.path.exists(path1) or not os.path.exists(path2):
        return jsonify({'error': 'One or both snapshots not found'}), 404

    with open(path1, 'r') as f:
        data1 = json.load(f)['data']
    with open(path2, 'r') as f:
        data2 = json.load(f)['data']

    files1 = {f['name']: f for f in data1}
    files2 = {f['name']: f for f in data2}

    added = [f for name, f in files2.items() if name not in files1]
    removed = [f for name, f in files1.items() if name not in files2]
    
    modified = []
    for name, f2 in files2.items():
        if name in files1:
            f1 = files1[name]
            if f1['h'] != f2['h'] or f1['size'] != f2['size']:
                modified.append({
                    'name': name,
                    'complexity_change': f2['h'] - f1['h'],
                    'size_change': f2['size'] - f1['size'],
                    'new_data': f2,
                })

    return jsonify({
        'added': added,
        'removed': removed,
        'modified': modified,
    })


if __name__ == '__main__':
    print("Code City Pro starting...")
    print("Visit: http://localhost:5100")
    app.run(debug=True, host='0.0.0.0', port=5100)

