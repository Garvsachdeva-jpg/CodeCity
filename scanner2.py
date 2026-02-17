import os
import json
import sys
import squarify
import requests
import lizard
import subprocess
import tempfile
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

# All file extensions supported by lizard
SUPPORTED_EXTENSIONS = [
    ".c", ".cc", ".cpp", ".cxx", ".c++", ".h", ".hh", ".hpp", ".hxx", ".h++",
    ".java", ".js", ".m", ".mm",
    ".py", ".rb", ".swift", ".go", ".php", ".pl", ".pm", ".t",
    ".cs", ".d", ".erl", ".ex", ".exs", ".f90", ".f", ".for", ".f95",
    ".groovy", ".hs", ".kt", ".kts", ".lua", ".nb", ".pas", ".pp",
    ".R", ".rs", ".scala", ".sc", ".sh", ".bash", ".sql", ".ts", ".tsx",
    ".vb", ".vbs", ".gd"
]

# GitHub token - set via environment variable or create one at https://github.com/settings/tokens
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', None)
MAX_FILES = 2000
MAX_WORKERS = 20

def get_github_repo_info(url):
    """Parse GitHub URL to owner/repo."""
    url = url.rstrip('/')
    if url.startswith('https://github.com/'):
        url = url.replace('https://github.com/', '')
    if url.startswith('git@github.com:'):
        url = url.replace('git@github.com:', '')
    if url.endswith('.git'):
        url = url[:-4]
    parts = url.split('/')
    return parts[0], parts[1]


def clone_repository(repo_url, token):
    """Clone a repository into a temporary directory."""
    temp_dir = tempfile.mkdtemp()
    
    # Construct the clone URL with the token for authentication
    if token:
        clone_url = f"https://{token}@github.com/{get_github_repo_info(repo_url)[0]}/{get_github_repo_info(repo_url)[1]}.git"
    else:
        clone_url = repo_url

    print(f"[PRO] Cloning repository {repo_url} into {temp_dir}...", flush=True)
    
    try:
        subprocess.run(
            ['git', 'clone', '--depth', '1', clone_url, temp_dir],
            check=True,
            capture_output=True,
            text=True
        )
        print("[PRO] Repository cloned successfully.", flush=True)
        return temp_dir
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to clone repository: {e.stderr}", flush=True)
        shutil.rmtree(temp_dir) # Clean up the temp directory
        raise

def get_source_files_from_local(local_repo_path, max_files=MAX_FILES):
    """Fetch all supported source files from a local directory."""
    source_files = []
    for root, _, files in os.walk(local_repo_path):
        # Exclude .git directory
        if '.git' in root:
            continue
        
        for file in files:
            if any(file.endswith(ext) for ext in SUPPORTED_EXTENSIONS):
                full_path = os.path.join(root, file)
                source_files.append({
                    'name': file,
                    'path': os.path.relpath(full_path, local_repo_path),
                    'local_path': full_path
                })
            if len(source_files) >= max_files:
                print(f"[WARN] Reached file limit of {max_files}. Some files may be excluded.", flush=True)
                return source_files
    return source_files

def analyze_file(file_info):
    """Read file content from local path and analyze it with lizard."""
    try:
        with open(file_info['local_path'], 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        if not content:
            return None

        lines = content.splitlines()
        if not lines:
            return None

        analysis = lizard.analyze_file(file_info['local_path'])
        complexity = sum(f.cyclomatic_complexity for f in analysis.function_list) or 1

        return {
            "name": file_info['name'],
            "size": max(1, len(lines)),
            "complexity": complexity,
            "churn": 1,  # Placeholder
        }
    except Exception as e:
        print(f"[DEBUG] Error analyzing {file_info['name']}: {e}", flush=True)
        return None

def build_city_from_github(repo_url):
    """Build city from GitHub repository."""
    if not GITHUB_TOKEN:
        print("[WARN] GITHUB_TOKEN environment variable not set. You may experience rate-limiting.", flush=True)

    print(f"[PRO] Fetching repository: {repo_url}", flush=True)
    owner, repo = get_github_repo_info(repo_url)
    print(f"[PRO] Owner: {owner}, Repo: {repo}", flush=True)
    
    local_repo_path = None
    try:
        local_repo_path = clone_repository(repo_url, GITHUB_TOKEN)
        print("[PRO] Fetching source files from local repository...", flush=True)
        files = get_source_files_from_local(local_repo_path)
        print(f"[PRO] Found {len(files)} source files to analyze.", flush=True)

        if not files:
            raise Exception("No analyzable source files found in repository.")

        files_to_render = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_file = {executor.submit(analyze_file, f): f for f in files}
            for i, future in enumerate(as_completed(future_to_file)):
                file_info = future_to_file[future]
                print(f"[PRO] Analyzing [{i+1}/{len(files)}]: {file_info['path']}", flush=True)
                try:
                    result = future.result()
                    if result:
                        files_to_render.append(result)
                except Exception as e:
                    print(f"[ERROR] Exception analyzing {file_info['name']}: {e}", flush=True)

        if not files_to_render:
            raise Exception("No analyzable files could be processed.")

        num_files = len(files_to_render)
        dynamic_area = max(150, int((num_files ** 0.5) * 45))

        sizes = [f['size'] for f in files_to_render]
        if sum(sizes) == 0:
            sizes = [1] * len(files_to_render)

        values = squarify.normalize_sizes(sizes, dynamic_area, dynamic_area)
        rects = squarify.squarify(values, 0, 0, dynamic_area, dynamic_area)

        city_data = []
        for i, file_data in enumerate(files_to_render):
            complexity = file_data['complexity']
            if complexity <= 5:
                color = "#00ffcc"
            elif complexity <= 15:
                color = "#00ff88"
            elif complexity <= 30:
                color = "#FFC300"
            elif complexity <= 50:
                color = "#ff9900"
            else:
                color = "#ff4444"

            height = max(1, complexity * 2)

            city_data.append({
                "name": file_data['name'],
                "x": rects[i]['x'], "y": rects[i]['y'],
                "w": rects[i]['dx'], "d": rects[i]['dy'],
                "h": height,
                "color": color,
                "size": file_data['size'],
            })

        with open('city_data2.json', 'w') as f:
            json.dump(city_data, f)
        print(f"[PRO] City generated from {owner}/{repo}!", flush=True)
    finally:
        if local_repo_path and os.path.exists(local_repo_path):
            print(f"[PRO] Cleaning up temporary directory: {local_repo_path}", flush=True)
            shutil.rmtree(local_repo_path, onerror=on_rm_error)


def on_rm_error(func, path, exc_info):
    """
    Error handler for shutil.rmtree.
    If the error is due to an access error (read only file)
    it attempts to add write permission and then retries.
    If the error is for another reason it re-raises the error.
    """
    import stat
    if not os.access(path, os.W_OK):
        # Is the error an access error ?
        os.chmod(path, stat.S_IWUSR)
        func(path)
    else:
        raise


def set_github_token(token):
    """Set the GitHub token."""
    global GITHUB_TOKEN
    GITHUB_TOKEN = token

if __name__ == '__main__':
    try:
        if len(sys.argv) > 2:
            set_github_token(sys.argv[2])
        if len(sys.argv) > 1:
            repo_input = sys.argv[1]
            build_city_from_github(repo_input)
        else:
            raise Exception("Repository URL is required")
    except Exception as e:
        print(f"[FATAL ERROR] {str(e)}", flush=True)
        sys.exit(1)
