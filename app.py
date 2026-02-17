from flask import Flask, request, jsonify, send_file
import json
import subprocess
import os
import sys
from datetime import datetime


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

app = Flask(__name__, static_folder='.', static_url_path='')


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
        cwd = BASE_DIR
        cmd = [sys.executable, 'scanner2.py', repo_url]
        if github_token:
            cmd.append(github_token)
        print(f"[PRO] Running command: {' '.join(cmd)} in {cwd}", file=sys.stderr)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=cwd,
        )

        print(f"[PRO] Exit code: {result.returncode}", file=sys.stderr)
        print(f"[PRO] STDOUT:\n{result.stdout}", file=sys.stderr)
        print(f"[PRO] STDERR:\n{result.stderr}", file=sys.stderr)

        if result.returncode != 0:
            error_msg = result.stderr if result.stderr else result.stdout
            return jsonify({'error': f'Analysis failed: {error_msg}'}), 400

        data_file = os.path.join(cwd, 'city_data2.json')
        if not os.path.exists(data_file):
            return jsonify({'error': 'No data generated - check if repository has Python files'}), 400

        with open(data_file, 'r') as f:
            city_data = json.load(f)

        # Create a snapshot record
        timestamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        snapshot_id = f"{timestamp}"
        snapshot_meta = {
            "id": snapshot_id,
            "created_at": timestamp,
            "repo_url": repo_url,
            "label": label or repo_url,
            "file_count": len(city_data),
        }

        snapshot_path = os.path.join(SNAPSHOT_DIR, f"{snapshot_id}.json")
        with open(snapshot_path, 'w') as f:
            json.dump({"meta": snapshot_meta, "data": city_data}, f)

        print(f"[PRO] Snapshot saved at {snapshot_path}", file=sys.stderr)

        return jsonify({
            'success': True,
            'data': city_data,
            'snapshot': snapshot_meta,
            'message': result.stdout,
        })

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Analysis timed out (> 300 seconds). Repository may be too large.'}), 408
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


if __name__ == '__main__':
    print("Code City Pro starting...")
    print("Visit: http://localhost:5100")
    app.run(debug=True, host='0.0.0.0', port=5100)

