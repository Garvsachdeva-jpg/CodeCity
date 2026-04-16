## CodeCity Pro – ML-Powered Code Health Map

CodeCity Pro scans a GitHub repository, turns it into a 3D city, and now also adds
data-driven risk and anomaly scores per file. It is designed to showcase end-to-end
data engineering, data science, and ML engineering skills for DS/ML roles.

### What it does

- Scans a GitHub repository and computes per-file metrics (size, complexity, layout).
- Stores all snapshot and file metrics in a SQLite database.
- Provides notebooks to explore metrics and train:
  - A supervised **risk model** (high-risk file classifier).
  - An **anomaly detection model** (IsolationForest).
- Serves models in the Flask app to attach `risk_score` and `anomaly_score`
  to each file in every new snapshot.
- Visualizes overall project stats and ML scores in a 3D dashboard.

### GitHub login (OAuth)

This project supports optional **GitHub OAuth login**:

- **Public (no login)**: You can still analyze **public repositories** via the Analyze button.
- **Login required**: Access to the **Saved Analyses / snapshot dashboard** (snapshots list, timeline slider, snapshot diffs) is gated behind GitHub login.

To enable OAuth, set:

- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `SECRET_KEY` (recommended for stable sessions; otherwise a random key is used per run)

Optional:

- `GITHUB_OAUTH_SCOPES` (default: `read:user`)

### Running the app

```bash
pip install -r CodeCity/requirements.txt
python CodeCity/app.py
```

Then visit `http://localhost:5100` in your browser.

