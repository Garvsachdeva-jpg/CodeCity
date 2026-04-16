from flask import Flask, request, jsonify, send_file, redirect, session, url_for
import json
import os
import sys
from datetime import datetime
import secrets
import urllib.parse

import requests
from dotenv import load_dotenv

from src import scan_pipeline


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

# Load local .env file if present
load_dotenv(os.path.join(BASE_DIR, ".env"))

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

GITHUB_CLIENT_ID = (os.environ.get("GITHUB_CLIENT_ID") or "").strip()
GITHUB_CLIENT_SECRET = (os.environ.get("GITHUB_CLIENT_SECRET") or "").strip()
GITHUB_OAUTH_SCOPES = (os.environ.get("GITHUB_OAUTH_SCOPES") or "read:user repo").strip()
REDIRECT_URI = (os.environ.get("REDIRECT_URI") or "").strip()


def _is_logged_in() -> bool:
    return bool(session.get("github_user") and session.get("github_access_token"))


def _login_required():
    if not _is_logged_in():
        return jsonify({"error": "GitHub login required"}), 401
    return None


@app.route('/health', methods=['GET'])
def health():
    """Simple health check endpoint."""
    return jsonify({"status": "ok"}), 200


@app.route('/')
def index():
    """Serve the Pro UI."""
    return send_file('index.html')


@app.route("/login")
def login():
    """
    Start GitHub OAuth flow.

    Requires env vars:
      - GITHUB_CLIENT_ID
      - GITHUB_CLIENT_SECRET
    """
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        return (
            "GitHub OAuth is not configured. Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET.",
            500,
        )

    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state

    # Prefer explicit REDIRECT_URI from environment (must match GitHub app)
    redirect_uri = REDIRECT_URI or url_for("oauth_callback", _external=True)
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": GITHUB_OAUTH_SCOPES,
        "state": state,
        "allow_signup": "true",
    }
    authorize_url = "https://github.com/login/oauth/authorize?" + urllib.parse.urlencode(
        params
    )
    return redirect(authorize_url)


@app.route("/oauth/callback")
def oauth_callback():
    """GitHub OAuth callback handler."""
    error = request.args.get("error")
    if error:
        desc = request.args.get("error_description") or error
        return f"GitHub OAuth error: {desc}", 400

    code = request.args.get("code")
    state = request.args.get("state")
    expected_state = session.get("oauth_state")
    session.pop("oauth_state", None)

    if not code or not state or not expected_state or state != expected_state:
        return "Invalid OAuth state. Please try logging in again.", 400

    # Compute redirect_uri used in the flow (must match GitHub app)
    redirect_uri = REDIRECT_URI or url_for("oauth_callback", _external=True)

    token_res = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": GITHUB_CLIENT_ID,
            "client_secret":GITHUB_CLIENT_SECRET,
            "code": code,
            # Include redirect_uri if we supplied one when starting the flow
            **({"redirect_uri": redirect_uri} if redirect_uri else {}),
        },
        timeout=30,
    )
    token_res.raise_for_status()
    token_payload = token_res.json()
    access_token = token_payload.get("access_token")
    if not access_token:
        return "Failed to get GitHub access token.", 400

    user_res = requests.get(
        "https://api.github.com/user",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
        },
        timeout=30,
    )
    user_res.raise_for_status()
    user = user_res.json()

    session["github_access_token"] = access_token
    session["github_user"] = {
        "id": user.get("id"),
        "login": user.get("login"),
        "name": user.get("name"),
        "avatar_url": user.get("avatar_url"),
    }

    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


@app.route("/api/me", methods=["GET"])
def me():
    """Return current login status for the UI."""
    if not _is_logged_in():
        return jsonify({"authenticated": False}), 200
    return jsonify({"authenticated": True, "user": session.get("github_user")}), 200



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
    
    # Prioritize token from request, fall back to session token, then environment variable
    session_token = session.get("github_access_token") if _is_logged_in() else ""
    github_token = (
        (data.get("github_token") or "")
        or (session_token or "")
        or (os.environ.get("GITHUB_TOKEN") or "")
    ).strip()

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
    guard = _login_required()
    if guard:
        return guard
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
    guard = _login_required()
    if guard:
        return guard
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
    guard = _login_required()
    if guard:
        return guard
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


@app.route('/api/my_repos', methods=['GET'])
def my_repos():
    """Return the authenticated user's repositories (public + private).

    Requires the user to be logged in and that the OAuth token has the
    `repo` scope to access private repositories. Results are paginated
    transparently and returned as a simplified list.
    """
    guard = _login_required()
    if guard:
        return guard

    access_token = session.get('github_access_token')
    if not access_token:
        return jsonify({'error': 'GitHub access token missing'}), 401

    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {access_token}',
    }

    url = 'https://api.github.com/user/repos'
    params = {'per_page': 100, 'type': 'all', 'sort': 'updated'}
    repos = []

    try:
        while True:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            page = resp.json() or []
            repos.extend(page)

            link = resp.headers.get('Link', '')
            if 'rel="next"' in link:
                import re
                m = re.search(r'<([^>]+)>;\s*rel="next"', link)
                if m:
                    url = m.group(1)
                    params = None
                    continue
            break
    except requests.HTTPError as e:
        return jsonify({'error': 'Failed to fetch repos', 'detail': str(e)}), 502
    except Exception as e:
        return jsonify({'error': 'Unexpected error', 'detail': str(e)}), 500

    # Simplify the payload we return to the UI
    simplified = []
    for r in repos:
        simplified.append({
            'id': r.get('id'),
            'name': r.get('name'),
            'full_name': r.get('full_name'),
            'private': r.get('private'),
            'html_url': r.get('html_url'),
            'description': r.get('description'),
            'language': r.get('language'),
            'updated_at': r.get('updated_at'),
            'stargazers_count': r.get('stargazers_count'),
            'forks_count': r.get('forks_count'),
            'owner': {
                'login': r.get('owner', {}).get('login'),
                'id': r.get('owner', {}).get('id'),
            },
        })

    # Sort by updated_at desc to show most recent first
    simplified.sort(key=lambda x: x.get('updated_at') or '', reverse=True)
    return jsonify(simplified)


@app.route('/api/diff', methods=['GET'])
def diff_snapshots():
    """Compare two snapshots and return the delta."""
    guard = _login_required()
    if guard:
        return guard
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

