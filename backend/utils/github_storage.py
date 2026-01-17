import os
import base64
import uuid
import requests
from datetime import datetime
from typing import Dict, Optional

def upload_image_to_github(file_path: str, session_id: str, content_type: str = "instagram") -> Dict:
    """
    Uploads an image to GitHub and returns the public URL and metadata.
    
    Args:
        file_path: Local path to the image file
        session_id: Workflow session ID
        content_type: Type of content (instagram, email, etc.)
        
    Returns:
        Dict containing url, repo_path, sha, size
    """
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_ASSETS_REPO")
    branch = os.getenv("GITHUB_BRANCH", "main")

    if not token or not repo:
        raise ValueError("Missing GITHUB_TOKEN or GITHUB_ASSETS_REPO environment variables")

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    # Generate path: campaigns/{session_id}/{content_type}/{yyyy-mm-dd}/{uuid}_{hash}.png
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = os.path.basename(file_path)
    
    # Create safe unique filename
    unique_id = uuid.uuid4().hex[:8]
    safe_name = f"{unique_id}_{filename}"
    
    # GitHub path
    github_path = f"campaigns/{session_id}/{content_type}/{date_str}/{safe_name}"
    
    # Read file content
    with open(file_path, "rb") as f:
        content = f.read()
    
    content_b64 = base64.b64encode(content).decode("utf-8")

    # Upload to GitHub via API
    url = f"https://api.github.com/repos/{repo}/contents/{github_path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    data = {
        "message": f"Upload asset for session {session_id}",
        "content": content_b64,
        "branch": branch
    }

    # Retrieve existing sha if file exists (unlikely with uuid, but good practice)
    # We are skipping this since we use UUIDs
    
    response = requests.put(url, json=data, headers=headers)
    
    if response.status_code not in [200, 201]:
        raise Exception(f"GitHub upload failed: {response.status_code} {response.text}")

    response_data = response.json()
    content_data = response_data.get("content", {})
    
    # Construct raw public URL (acting as CDN)
    # raw.githubusercontent.com/{org}/{repo}/{branch}/{path}
    raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{github_path}"

    return {
        "url": raw_url,
        "repo_path": github_path,
        "sha": content_data.get("sha"),
        "size": content_data.get("size")
    }
