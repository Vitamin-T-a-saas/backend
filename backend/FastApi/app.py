from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime, timedelta
import os
import json
import threading
import time
import uuid
from pathlib import Path
import sqlite3
from contextlib import contextmanager
import pickle
import logging
from functools import wraps
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add parent directory to path to allow importing utils
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from utils.github_storage import upload_image_to_github
    GITHUB_STORAGE_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Could not import github_storage utils: {e}")
    GITHUB_STORAGE_AVAILABLE = False

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import your existing modules
try:
    from models.myinstascrape import InstagramCompetitorAnalyzer
    SCRAPER_AVAILABLE = True
except Exception as e:
    logger.warning(f"InstagramCompetitorAnalyzer not available: {e}")
    SCRAPER_AVAILABLE = False

try:
    from models.generate_instagram import run_instagram_agent
    INSTAGRAM_AGENT_AVAILABLE = True
except Exception as e:
    logger.warning(f"run_instagram_agent not available: {e}")
    INSTAGRAM_AGENT_AVAILABLE = False

try:
    from models.generate_email import run_email_agent
    EMAIL_AGENT_AVAILABLE = True
except Exception as e:
    logger.warning(f"run_email_agent not available: {e}")
    EMAIL_AGENT_AVAILABLE = False

# ============= APP SETUP =============
app = FastAPI(
    title="Unified Content Management System",
    version="4.0.0",
    description="Enhanced 36 endpoints with robust error handling and state management"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",  # Vite default port
        "http://localhost:8080",  # Vite alternative port
        "http://localhost:8081",  # Current frontend port
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:8081",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

# ============= GLOBALS =============
scheduler_running = False
scheduler_thread = None
content_folder = "generated_content"
db_path = "content_management.db"

os.makedirs(content_folder, exist_ok=True)
os.makedirs("campaigns", exist_ok=True)

# ============= DATABASE =============
def init_database():
    """Initialize database with all required tables"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Brands table (Persistent Identity)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS brands (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            brand_values TEXT,
            target_audience TEXT,
            instagram_expectations TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')

    # Campaigns table (Marketing Project)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS campaigns (
            id TEXT PRIMARY KEY,
            brand_id TEXT,
            name TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(brand_id) REFERENCES brands(id)
        )
    ''')

    # Migration: Check if brand_id exists in campaigns
    cursor.execute("PRAGMA table_info(campaigns)")
    camp_columns = [info[1] for info in cursor.fetchall()]
    if 'brand_id' not in camp_columns:
        logger.info("Migrating database: Adding brand_id to campaigns")
        try:
            cursor.execute('ALTER TABLE campaigns ADD COLUMN brand_id TEXT')
        except Exception as e:
            logger.error(f"Migration failed (campaigns): {e}")

    # Workflow sessions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workflow_sessions (
            session_id TEXT PRIMARY KEY,
            campaign_id TEXT,
            workflow_type TEXT,
            channel TEXT,
            current_step TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            campaign_folder TEXT,
            metadata TEXT,
            FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
        )
    ''')

    # Migration: Check if campaign_id exists in workflow_sessions (for existing DBs)
    cursor.execute("PRAGMA table_info(workflow_sessions)")
    columns = [info[1] for info in cursor.fetchall()]
    if 'campaign_id' not in columns:
        logger.info("Migrating database: Adding campaign_id to workflow_sessions")
        try:
            cursor.execute('ALTER TABLE workflow_sessions ADD COLUMN campaign_id TEXT')
        except Exception as e:
            logger.error(f"Migration failed: {e}")
    
    # Workflow states table (stores pickled state)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS workflow_states (
            session_id TEXT PRIMARY KEY,
            state_data BLOB NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    
    # Instagram cache table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS instagram_cache (
            username TEXT PRIMARY KEY,
            followers INTEGER DEFAULT 0,
            avg_likes INTEGER DEFAULT 0,
            avg_comments INTEGER DEFAULT 0,
            engagement_rate REAL DEFAULT 0.0,
            posts_analyzed INTEGER DEFAULT 0,
            profile_url TEXT,
            error TEXT,
            cached_at TEXT NOT NULL
        )
    ''')
    
    # Schedule entries table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schedule_entries (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            content_type TEXT NOT NULL,
            scheduled_date TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            content_description TEXT,
            instagram_username TEXT,
            content_path TEXT,
            created_at TEXT NOT NULL,
            notified_at TEXT,
            error_message TEXT
        )
    ''')
    
 
    
    # Media assets table (GitHub storage - PRIMARY for images)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS media_assets (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            platform TEXT,
            content_type TEXT,
            storage TEXT,
            url TEXT,
            repo_path TEXT,
            sha TEXT,
            created_at TEXT
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully")

@contextmanager
def get_db_connection():
    """Context manager for database connections"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

init_database()

# ============= PYDANTIC MODELS =============
class WorkflowStartResponse(BaseModel):
    success: bool
    message: str
    session_id: str
    next_step: str

class BrandDnaRequest(BaseModel):
    session_id: Optional[str] = None
    brand_name: str
    brand_description: str
    brand_values: List[str]
    target_audience: List[str]
    instagram_expectations: List[str]
    
    @validator('brand_name')
    def validate_brand_name(cls, v):
        if not v or len(v.strip()) < 2:
            raise ValueError('Brand name must be at least 2 characters')
        return v.strip()

class BrandDnaResponse(BaseModel):
    success: bool
    message: str
    session_id: str
    next_step: str

class ChannelRequest(BaseModel):
    session_id: str
    channel: Literal["instagram", "email"]

class ChannelResponse(BaseModel):
    success: bool
    message: str
    channel: str
    next_step: str

class CampaignTypeRequest(BaseModel):
    session_id: str
    campaign_type: Literal["single", "series"]

class CampaignTypeResponse(BaseModel):
    success: bool
    message: str
    next_step: str
    requires_config: bool

class SeriesConfigRequest(BaseModel):
    session_id: str
    days: int = Field(..., ge=2, le=30, description="Number of days (2-30)")
    content_type: Literal["reel", "post"]

class ContentTypeRequest(BaseModel):
    session_id: str
    content_type: Literal["reel", "post"]

class IdeaResponse(BaseModel):
    success: bool
    idea: str
    session_id: str
    day_info: Optional[str] = None

class IdeaActionRequest(BaseModel):
    session_id: str
    action: Literal["accept", "modify", "regenerate"]
    modified_idea: Optional[str] = None
    
    @validator('modified_idea')
    def validate_modified_idea(cls, v, values):
        if values.get('action') == 'modify' and not v:
            raise ValueError('Modified idea required for modify action')
        return v

class ContentResponse(BaseModel):
    success: bool
    content_type: str
    content_data: Dict[str, Any]
    files: List[str] = []

class CaptionResponse(BaseModel):
    success: bool
    caption: str
    session_id: str

class SummaryResponse(BaseModel):
    success: bool
    session_id: str
    summary: Dict[str, Any]

class EmailTypeRequest(BaseModel):
    session_id: str
    email_type: str
    target_audience: str
    key_message: str
    tone: str = "professional"

class ChatMessageRequest(BaseModel):
    session_id: str
    message: str
    channel: Optional[str] = None  # "instagram" or "email" if already selected

class ChatMessageResponse(BaseModel):
    success: bool
    response: str
    action_taken: Optional[str] = None  # "generated_post", "generated_script", "generated_email", etc.
    content: Optional[Dict[str, Any]] = None
    next_step: Optional[str] = None


class AnalyzeRequest(BaseModel):
    instagram_input: str
    force_refresh: bool = False

class InstagramData(BaseModel):
    username: str
    followers: int = 0
    avg_likes: int = 0
    avg_comments: int = 0
    engagement_rate: float = 0.0
    posts_analyzed: int = 0
    profile_url: str = ""
    captions: Optional[list] = None
    posts_data: Optional[list] = None
    error: Optional[str] = None

class ScheduleRequest(BaseModel):
    session_id: Optional[str] = None
    content_type: str
    instagram_username: str
    scheduled_date: datetime
    content_description: str = ""
    content_path: str = ""

class ScheduleUpdate(BaseModel):
    status: Optional[str] = None
    scheduled_date: Optional[datetime] = None

class WorkflowStatusResponse(BaseModel):
    session_id: str
    workflow_type: Optional[str]
    channel: Optional[str]
    current_step: str
    status: str
    progress: Dict[str, Any]
    next_available_steps: List[str]

# ============= UTILITY FUNCTIONS =============

def parse_instagram_input(input_text: str) -> str:
    """Parse Instagram username from various input formats"""
    if not input_text or not input_text.strip():
        raise ValueError("Instagram input required")
    
    input_text = input_text.strip()
    
    # Handle URL format
    if "instagram.com/" in input_text:
        username = input_text.split("instagram.com/")[-1].rstrip("/").split('?')[0].split('/')[0]
    else:
        # Handle @username format
        username = input_text.replace("@", "").strip()
    
    # Validate username
    if not username or len(username) < 1:
        raise ValueError("Invalid Instagram username")
    
    return username

def create_campaign_folder(brand_name: str, channel: str, campaign_id: str = None) -> str:
    """Create organized campaign folder structure"""
    # Use campaign_id for deterministic path if available, else timestamp
    if campaign_id:
        safe_brand = "".join(c if c.isalnum() or c in " -_" else "" for c in brand_name).replace(" ", "_")
        folder_name = f"campaigns/{safe_brand}/{channel}_{campaign_id}"
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_brand = "".join(c if c.isalnum() or c in " -_" else "" for c in brand_name).replace(" ", "_")
        folder_name = f"campaigns/{safe_brand}/{channel}_{timestamp}"
    
    # Create all necessary subfolders
    subfolders = ["storyboards", "posts", "images", "captions", "metadata", "emails", "scripts"]
    for subfolder in subfolders:
        Path(f"{folder_name}/{subfolder}").mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Created campaign folder: {folder_name}")
    return folder_name

def validate_session_step(session_id: str, expected_step: str = None) -> Dict:
    """Validate session exists and optionally check step"""
    session = get_workflow_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    
    if expected_step and session['current_step'] != expected_step:
        raise HTTPException(
            status_code=400, 
            detail=f"Invalid workflow step. Expected '{expected_step}', got '{session['current_step']}'"
        )
    
    return session

def get_initial_state() -> Dict:
    """Get initial workflow state structure"""
    return {
        "brand_dna": None,
        "channel": None,
        "campaign_type": None,
        "content_type": None,
        "email_config": None,
        "days_series": None,
        "current_day": None,
        "ideas": [],
        "current_idea": None,
        "captions": [],
        "emails": [],
        "campaign_folder": None,
        "generated_content": []
    }

def get_media_type(filename: str) -> str:
    """Get media type based on file extension"""
    ext = os.path.splitext(filename)[1].lower()
    media_types = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.svg': 'image/svg+xml',
        '.pdf': 'application/pdf',
        '.json': 'application/json',
        '.txt': 'text/plain',
        '.md': 'text/markdown',
        '.html': 'text/html',
        '.csv': 'text/csv',
    }
    return media_types.get(ext, 'application/octet-stream')
# ============= STATE MANAGEMENT =============

def save_workflow_session(session_id: str, current_step: str, workflow_type: str = None,
                         channel: str = None, status: str = "active", 
                         campaign_folder: str = None, metadata: Dict = None,
                         campaign_id: str = None):
    """Save or update workflow session"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            
            # Check if session exists to preserve creation time and existing campaign_id
            cursor.execute('SELECT created_at, campaign_id FROM workflow_sessions WHERE session_id = ?', (session_id,))
            existing = cursor.fetchone()
            created_at = existing['created_at'] if existing else now
            
            # Use provided campaign_id or preserve existing one, or None
            final_campaign_id = campaign_id if campaign_id else (existing['campaign_id'] if existing else None)
            
            cursor.execute('''
                INSERT OR REPLACE INTO workflow_sessions 
                (session_id, workflow_type, channel, current_step, status, 
                 created_at, updated_at, campaign_folder, metadata, campaign_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (session_id, workflow_type, channel, current_step, status, 
                  created_at, now, campaign_folder, json.dumps(metadata) if metadata else None, final_campaign_id))
            conn.commit()
            
        logger.info(f"Session {session_id} saved: step={current_step}, status={status}")
    except Exception as e:
        logger.error(f"Error saving session {session_id}: {e}")
        raise

def get_workflow_session(session_id: str) -> Optional[Dict]:
    """Get workflow session by ID"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM workflow_sessions WHERE session_id = ?', (session_id,))
            row = cursor.fetchone()
            if row:
                session = dict(row)
                if session.get('metadata'):
                    try:
                        session['metadata'] = json.loads(session['metadata'])
                    except:
                        session['metadata'] = {}
                return session
    except Exception as e:
        logger.error(f"Error getting session {session_id}: {e}")
    return None

def save_workflow_state(session_id: str, state_data: Dict):
    """Save workflow state with pickle"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            serialized_state = pickle.dumps(state_data)
            now = datetime.now().isoformat()
            cursor.execute('''
                INSERT OR REPLACE INTO workflow_states 
                (session_id, state_data, updated_at) VALUES (?, ?, ?)
            ''', (session_id, serialized_state, now))
            conn.commit()
        logger.debug(f"State saved for session {session_id}")
    except Exception as e:
        logger.error(f"Error saving state for {session_id}: {e}")
        raise

def get_workflow_state(session_id: str) -> Optional[Dict]:
    """Get workflow state from database"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT state_data FROM workflow_states WHERE session_id = ?', (session_id,))
            row = cursor.fetchone()
            if row and row['state_data']:
                return pickle.loads(row['state_data'])
    except Exception as e:
        logger.error(f"Error getting state for {session_id}: {e}")
    return None

def update_workflow_step(session_id: str, new_step: str, status: str = "active"):
    """Update workflow step"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute('''
                UPDATE workflow_sessions 
                SET current_step = ?, status = ?, updated_at = ?
                WHERE session_id = ?
            ''', (new_step, status, now, session_id))
            conn.commit()
        logger.info(f"Session {session_id} step updated to: {new_step}")
    except Exception as e:
        logger.error(f"Error updating step for {session_id}: {e}")
        raise

# ============= AGENT WRAPPERS =============

def call_instagram_agent_safe(state: Dict, action: str = "generate_idea") -> Dict:
    """Safely call Instagram agent with error handling and retries"""
    max_retries = 2
    
    for attempt in range(max_retries):
        try:
            if INSTAGRAM_AGENT_AVAILABLE:
                logger.info(f"Calling Instagram agent (attempt {attempt + 1})")
                result = run_instagram_agent(state, action=action)
                
                if result and isinstance(result, dict):
                    logger.info("Instagram agent call successful")
                    return result
                else:
                    logger.warning("Instagram agent returned invalid result")
            
        except Exception as e:
            logger.error(f"Instagram agent error (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                break
            time.sleep(1)
    
    # Fallback
    logger.info("Using fallback for Instagram content")
    return generate_instagram_fallback(state, action)

def generate_instagram_fallback(state: Dict, action: str) -> Dict:
    """Generate fallback Instagram content"""
    brand_name = state['brand_dna']['brand_name']
    content_type = state.get('content_type', 'post')
    
    if action == "generate_idea":
        ideas = [
            f"Behind the scenes: How {brand_name} creates magic",
            f"Top 5 tips from {brand_name} experts",
            f"Customer success story featuring {brand_name}",
            f"Quick tutorial: Getting started with {brand_name}",
            f"What makes {brand_name} different from competitors"
        ]
        return {"idea": ideas[len(state.get('ideas', [])) % len(ideas)]}
    
    elif action == "generate_content":
        if content_type == "reel":
            return {
                "storyboard": {
                    "numOfScenes": 3,
                    "scenePrompts": [
                        f"Hook: Attention-grabbing intro for {brand_name}",
                        f"Value: Main content showcasing {brand_name}",
                        f"CTA: Call to action for {brand_name}"
                    ],
                    "dialogue": [
                        "Stop scrolling! Here's something you need to know...",
                        "Let me show you how this works...",
                        "Try it yourself! Link in bio."
                    ]
                }
            }
        else:
            return {
                "post": {
                    "post_type": "single",
                    "image_prompts": [f"Professional image for {brand_name}: {state.get('current_idea', 'Featured content')[:100]}"]
                }
            }
    
    return {}

def call_email_agent_safe(state: Dict, action: str = "generate_idea") -> Dict:
    """Safely call Email agent with error handling"""
    max_retries = 2
    
    for attempt in range(max_retries):
        try:
            if EMAIL_AGENT_AVAILABLE:
                logger.info(f"Calling Email agent (attempt {attempt + 1})")
                result = run_email_agent(state, action=action)
                
                if result and isinstance(result, dict):
                    logger.info("Email agent call successful")
                    return result
                    
        except Exception as e:
            logger.error(f"Email agent error (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                break
            time.sleep(1)
    
    # Fallback
    logger.info("Using fallback for Email content")
    return generate_email_fallback(state, action)

def generate_email_fallback(state: Dict, action: str) -> Dict:
    """Generate fallback email content"""
    brand_name = state['brand_dna']['brand_name']
    config = state.get('email_config', {})
    
    if action == "generate_idea":
        return {
            "idea": f"{config.get('email_type', 'Newsletter')} - {config.get('key_message', 'Important update from ' + brand_name)}"
        }
    
    elif action == "generate_content":
        return {
            "email_content": f"""Subject: {state.get('current_idea', 'Update from ' + brand_name)}

Dear {config.get('target_audience', 'Valued Customer')},

{config.get('key_message', f'We have exciting news to share with you from {brand_name}!')}

Thank you for being part of our community.

Best regards,
The {brand_name} Team"""
        }
    
def get_brand_by_name(name: str) -> Optional[dict]:
    """Get brand by name"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM brands WHERE name = ?", (name,))
        row = cursor.fetchone()
        return dict(row) if row else None

def create_brand(name: str, description: str, values: list, audience: list, expectations: str) -> str:
    """Create a new persistent brand"""
    brand_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    
    # Serialize lists to JSON strings for storage
    values_json = json.dumps(values) if values else "[]"
    audience_json = json.dumps(audience) if audience else "[]"
    expectations_json = json.dumps(expectations) if isinstance(expectations, list) else (expectations or "")
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO brands (id, name, description, brand_values, target_audience, instagram_expectations, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (brand_id, name, description, values_json, audience_json, expectations_json, now, now))
        conn.commit()
    logger.info(f"Created Brand: {name} ({brand_id})")
    return brand_id

def update_brand(brand_id: str, description: str, values: list, audience: list, expectations: str):
    """Update existing brand"""
    now = datetime.now().isoformat()
    values_json = json.dumps(values) if values else "[]"
    audience_json = json.dumps(audience) if audience else "[]"
    expectations_json = json.dumps(expectations) if isinstance(expectations, list) else (expectations or "")
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE brands 
            SET description = ?, brand_values = ?, target_audience = ?, instagram_expectations = ?, updated_at = ?
            WHERE id = ?
        ''', (description, values_json, audience_json, expectations_json, now, brand_id))
        conn.commit()
    logger.info(f"Updated Brand: {brand_id}")

def create_campaign(name: str, description: str = "", brand_id: str = None) -> str:
    """Create a new campaign and return its ID"""
    campaign_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO campaigns (id, brand_id, name, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (campaign_id, brand_id, name, description, now, now))
            conn.commit()
            logger.info(f"Created new campaign: {name} ({campaign_id})")
            return campaign_id
    except Exception as e:
        logger.error(f"Error creating campaign: {e}")
        raise

def update_campaign(campaign_id: str, name: str, description: str = ""):
    """Update an existing campaign"""
    now = datetime.now().isoformat()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE campaigns 
                SET name = ?, description = ?, updated_at = ?
                WHERE id = ?
            ''', (name, description, now, campaign_id))
            conn.commit()
            logger.info(f"Updated campaign: {name} ({campaign_id})")
    except Exception as e:
        logger.error(f"Error updating campaign {campaign_id}: {e}")
        raise
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE campaigns SET name = ?, description = ?, updated_at = ? WHERE id = ?
        ''', (name, description, now, campaign_id))
        conn.commit()

# ============= 1. WORKFLOW INITIATION & BRAND DNA (3 endpoints) =============

@app.post("/workflow/start", response_model=WorkflowStartResponse)
async def start_workflow():
    """Endpoint 1: Initialize new workflow"""
    try:
        session_id = str(uuid.uuid4())
        save_workflow_session(session_id, "collect_brand_dna")
        save_workflow_state(session_id, get_initial_state())
        
        logger.info(f"New workflow started: {session_id}")
        
        return WorkflowStartResponse(
            success=True,
            message="Workflow initialized successfully",
            session_id=session_id,
            next_step="brand_dna"
        )
    except Exception as e:
        logger.error(f"Error starting workflow: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/workflow/brand-dna", response_model=BrandDnaResponse)
async def submit_brand_dna(request: BrandDnaRequest):
    """Endpoint 2: Submit brand DNA (Persisted to Brands Table)"""
    try:
        session_id = request.session_id or str(uuid.uuid4())
        
        # 1. Handle Brand Persistence
        existing_brand = get_brand_by_name(request.brand_name)
        
        if existing_brand:
            brand_id = existing_brand['id']
            # Update existing brand with latest info
            update_brand(brand_id, request.brand_description, request.brand_values, 
                         request.target_audience, request.instagram_expectations)
            message_suffix = "Brand updated."
        else:
            # Create new brand
            brand_id = create_brand(request.brand_name, request.brand_description, 
                                    request.brand_values, request.target_audience, 
                                    request.instagram_expectations)
            message_suffix = "New Brand created."
            
        # 2. Handle Campaign linking
        current_session = get_workflow_session(session_id)
        existing_campaign_id = current_session.get('campaign_id') if current_session else None
        
        campaign_name = f"{request.brand_name} Campaign"
        
        if existing_campaign_id:
            update_campaign(existing_campaign_id, campaign_name, request.brand_description)
            campaign_id = existing_campaign_id
            message_suffix += " Campaign updated."
        else:
            # Link new campaign to the Brand ID
            campaign_id = create_campaign(campaign_name, request.brand_description, brand_id=brand_id)
            message_suffix += " Campaign created."
        
        # 3. Update Session
        # We populate the legacy state["brand_dna"] dict for compatibility with the rest of the app
        # But the Source of Truth is now the DB
        state = get_workflow_state(session_id) or get_initial_state()
        
        state["brand_dna"] = {
            "brand_id": brand_id, # Link in state too
            "brand_name": request.brand_name,
            "brand_description": request.brand_description,
            "brand_values": request.brand_values,
            "target_audience": request.target_audience,
            "instagram_expectations": request.instagram_expectations
        }
        
        save_workflow_session(session_id, "choose_channel", campaign_id=campaign_id)
        save_workflow_state(session_id, state)
        
        logger.info(f"Brand DNA processing complete. Session: {session_id}, Brand: {brand_id}, Campaign: {campaign_id}")
        
        return BrandDnaResponse(
            success=True,
            message=f"Brand DNA processed. {message_suffix}",
            session_id=session_id,
            next_step="channel"
        )
    except Exception as e:
        logger.error(f"Error saving brand DNA: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/workflow/status/{session_id}", response_model=WorkflowStatusResponse)
async def get_workflow_status(session_id: str):
    """Endpoint 3: Get current workflow status"""
    try:
        session = validate_session_step(session_id)
        state = get_workflow_state(session_id)
        
        # Map steps to next available actions
        step_mapping = {
            "collect_brand_dna": ["brand_dna"],
            "choose_channel": ["channel"],
            "choose_campaign_type": ["campaign_type"],
            "configure_series": ["series_config"],
            "choose_content_type": ["content_type"],
            "choose_email_type": ["email_type"],
            "generate_idea": ["idea"],
            "generate_content": ["content"],
            "generate_caption": ["caption"],
            "next_day": ["next"],
            "completed": ["summary"]
        }
        
        # Build progress info
        progress = {}
        if state:
            progress = {
                "has_brand_dna": state.get("brand_dna") is not None,
                "channel_selected": state.get("channel"),
                "campaign_type": state.get("campaign_type"),
                "content_type": state.get("content_type"),
                "ideas_generated": len(state.get("ideas", [])),
                "captions_generated": len(state.get("captions", [])),
                "emails_generated": len(state.get("emails", [])),
                "current_day": state.get("current_day"),
                "total_days": state.get("days_series"),
                "campaign_folder": state.get("campaign_folder")
            }
        
        return WorkflowStatusResponse(
            session_id=session_id,
            workflow_type=session.get("workflow_type"),
            channel=session.get("channel"),
            current_step=session["current_step"],
            status=session["status"],
            progress=progress,
            next_available_steps=step_mapping.get(session["current_step"], [])
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting workflow status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= 2. INSTAGRAM ANALYSIS (2 endpoints) =============

@app.post("/instagram/analyze", response_model=InstagramData)
async def analyze_instagram(request: AnalyzeRequest):
    """Endpoint 4: Analyze Instagram profile"""
    try:
        username = parse_instagram_input(request.instagram_input)
        logger.info(f"Analyzing Instagram profile: {username}")

        # Check cache unless force_refresh is True
        if not request.force_refresh:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM instagram_cache 
                    WHERE username = ? AND 
                    datetime(cached_at) > datetime('now', '-24 hours')
                ''', (username,))
                row = cursor.fetchone()
                if row:
                    logger.info(f"Using cached data for {username}")
                    # Add captions and posts_data as None for cached data (legacy)
                    cached = dict(row)
                    cached.setdefault('captions', None)
                    cached.setdefault('posts_data', None)
                    return InstagramData(**cached)

        # Scrape new data
        result = None
        if SCRAPER_AVAILABLE:
            try:
                analyzer = InstagramCompetitorAnalyzer()
                profile_url = f"https://www.instagram.com/{username}/"
                data = analyzer.scrape_competitor(profile_url)

                # Cleanup
                if hasattr(analyzer, 'driver'):
                    analyzer.driver.quit()

                if 'error' not in data:
                    result = InstagramData(
                        username=data.get('username', username),
                        followers=data.get('followers', 0),
                        avg_likes=data.get('avg_likes', 0),
                        avg_comments=data.get('avg_comments', 0),
                        engagement_rate=data.get('engagement_rate', 0),
                        posts_analyzed=data.get('posts_analyzed', 0),
                        profile_url=data.get('profile_url', profile_url),
                        captions=data.get('captions', []),
                        posts_data=data.get('posts_data', []),
                        error=None
                    )
                    logger.info(f"Successfully scraped {username}")
                else:
                    result = InstagramData(username=username, error=data.get('error'))
                    logger.warning(f"Scraper error for {username}: {data.get('error')}")

            except Exception as e:
                logger.error(f"Scraper exception for {username}: {e}")
                result = InstagramData(username=username, error=str(e))

        # Fallback to mock data
        if not result:
            logger.info(f"Using mock data for {username}")
            result = InstagramData(
                username=username,
                followers=15000,
                avg_likes=450,
                avg_comments=25,
                engagement_rate=3.17,
                posts_analyzed=12,
                profile_url=f"https://www.instagram.com/{username}/",
                captions=[],
                posts_data=[],
                error="Using sample data - scraper unavailable"
            )

        # Cache result (only basic fields, not captions/posts_data for now)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO instagram_cache 
                (username, followers, avg_likes, avg_comments, engagement_rate, 
                 posts_analyzed, profile_url, error, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (result.username, result.followers, result.avg_likes, result.avg_comments,
                  result.engagement_rate, result.posts_analyzed, result.profile_url,
                  result.error, datetime.now().isoformat()))
            conn.commit()

        return result

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error analyzing Instagram: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/instagram/analyze/{username}", response_model=InstagramData)
async def get_cached_analysis(username: str):
    """Endpoint 5: Get cached Instagram analysis"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM instagram_cache WHERE username = ?', (username,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"No cached data for {username}")
            return InstagramData(**dict(row))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting cached analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= 3. CHANNEL SELECTION (1 endpoint) =============

@app.post("/workflow/channel", response_model=ChannelResponse)
async def choose_channel(request: ChannelRequest):
    """Endpoint 6: Choose Instagram or Email channel"""
    try:
        session = validate_session_step(request.session_id, "choose_channel")
        state = get_workflow_state(request.session_id)
        
        if not state or not state.get("brand_dna"):
            raise HTTPException(status_code=400, detail="Brand DNA required before channel selection")
        
        # Update state
        state["channel"] = request.channel
        
        # Get campaign ID from session
        campaign_id = session.get("campaign_id")
        
        # Create campaign folder
        brand_name = state["brand_dna"]["brand_name"]
        campaign_folder = create_campaign_folder(brand_name, request.channel, campaign_id)
        state["campaign_folder"] = campaign_folder
        
        # Determine next step
        if request.channel == "instagram":
            next_step = "choose_campaign_type"
            save_workflow_session(
                request.session_id, 
                next_step, 
                workflow_type="instagram",
                channel="instagram", 
                campaign_folder=campaign_folder
            )
        else:  # email
            next_step = "choose_email_type"
            save_workflow_session(
                request.session_id, 
                next_step,
                workflow_type="email",
                channel="email", 
                campaign_folder=campaign_folder
            )
        
        save_workflow_state(request.session_id, state)
        
        logger.info(f"Channel selected for {request.session_id}: {request.channel}")
        
        return ChannelResponse(
            success=True,
            message=f"{request.channel.capitalize()} channel selected",
            channel=request.channel,
            next_step=next_step
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error selecting channel: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= 4. INSTAGRAM WORKFLOW (10 endpoints) =============

@app.post("/instagram/campaign-type", response_model=CampaignTypeResponse)
async def choose_campaign_type(request: CampaignTypeRequest):
    """Endpoint 7: Choose single or series campaign"""
    try:
        session = validate_session_step(request.session_id, "choose_campaign_type")
        state = get_workflow_state(request.session_id)
        
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        state["campaign_type"] = request.campaign_type
        
        if request.campaign_type == "series":
            update_workflow_step(request.session_id, "configure_series")
            save_workflow_state(request.session_id, state)
            
            logger.info(f"Series campaign selected for {request.session_id}")
            
            return CampaignTypeResponse(
                success=True,
                message="Series campaign selected - configure days and content type",
                next_step="series_config",
                requires_config=True
            )
        else:
            update_workflow_step(request.session_id, "choose_content_type")
            save_workflow_state(request.session_id, state)
            
            logger.info(f"Single campaign selected for {request.session_id}")
            
            return CampaignTypeResponse(
                success=True,
                message="Single post campaign selected",
                next_step="content_type",
                requires_config=False
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error choosing campaign type: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/instagram/series-config")
async def configure_series(request: SeriesConfigRequest):
    """Endpoint 8: Configure series campaign"""
    try:
        session = validate_session_step(request.session_id, "configure_series")
        state = get_workflow_state(request.session_id)
        
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        # Validate days
        if request.days < 2 or request.days > 30:
            raise HTTPException(status_code=400, detail="Days must be between 2 and 30")
        
        # Update state
        state["days_series"] = request.days
        state["content_type"] = request.content_type
        state["current_day"] = 1
        
        update_workflow_step(request.session_id, "generate_idea")
        save_workflow_state(request.session_id, state)
        
        logger.info(f"Series configured for {request.session_id}: {request.days} days of {request.content_type}")
        
        return {
            "success": True,
            "message": f"Series configured: {request.days} days of {request.content_type}s",
            "next_step": "idea",
            "days": request.days,
            "content_type": request.content_type
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error configuring series: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/instagram/content-type")
async def choose_content_type(request: ContentTypeRequest):
    """Endpoint 9: Choose reel or post for single campaign"""
    try:
        session = validate_session_step(request.session_id, "choose_content_type")
        state = get_workflow_state(request.session_id)
        
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        state["content_type"] = request.content_type
        
        update_workflow_step(request.session_id, "generate_idea")
        save_workflow_state(request.session_id, state)
        
        logger.info(f"Content type selected for {request.session_id}: {request.content_type}")
        
        return {
            "success": True,
            "message": f"Content type set to {request.content_type}",
            "next_step": "idea"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error choosing content type: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/instagram/idea/{session_id}", response_model=IdeaResponse)
async def get_instagram_idea(session_id: str):
    """Endpoint 10: Generate Instagram content idea"""
    try:
        session = validate_session_step(session_id, "generate_idea")
        state = get_workflow_state(session_id)
        
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        # Generate new idea if not exists
        if not state.get("current_idea"):
            logger.info(f"Generating idea for {session_id}")
            
            # Call agent
            result = call_instagram_agent_safe(state, action="generate_idea")
            state["current_idea"] = result.get("idea", f"Content idea for {state['brand_dna']['brand_name']}")
            
            save_workflow_state(session_id, state)
        
        # Build day info for series
        day_info = None
        if state.get("campaign_type") == "series":
            day_info = f"Day {state.get('current_day', 1)} of {state.get('days_series', 1)}"
        
        logger.info(f"Idea retrieved for {session_id}: {state['current_idea'][:50]}...")
        
        return IdeaResponse(
            success=True,
            idea=state["current_idea"],
            session_id=session_id,
            day_info=day_info
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting Instagram idea: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/instagram/idea/action")
async def handle_idea_action(request: IdeaActionRequest):
    """Endpoint 11: Accept/modify/regenerate Instagram idea"""
    try:
        session = validate_session_step(request.session_id, "generate_idea")
        state = get_workflow_state(request.session_id)
        
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        # Handle regenerate
        if request.action == "regenerate":
            state["current_idea"] = None
            save_workflow_state(request.session_id, state)
            
            logger.info(f"Regenerating idea for {request.session_id}")
            
            return {
                "success": True,
                "message": "Regenerating idea...",
                "next_step": "idea"
            }
        
        # Handle modify
        elif request.action == "modify":
            if not request.modified_idea:
                raise HTTPException(status_code=400, detail="Modified idea text required")
            
            state["current_idea"] = request.modified_idea
            logger.info(f"Idea modified for {request.session_id}")
        
        # Accept idea (both accept and modify end up here)
        state["ideas"].append(state["current_idea"])
        
        # Save to file
        day_suffix = ""
        if state.get("campaign_type") == "series":
            day_suffix = f"_day{state.get('current_day', 1)}"
        
        idea_path = f"{state['campaign_folder']}/metadata/idea{day_suffix}_{len(state['ideas'])}.txt"
        os.makedirs(os.path.dirname(idea_path), exist_ok=True)
        
        with open(idea_path, "w", encoding="utf-8") as f:
            f.write(state["current_idea"])
        
        # Move to content generation
        update_workflow_step(request.session_id, "generate_content")
        save_workflow_state(request.session_id, state)
        
        logger.info(f"Idea accepted for {request.session_id}, moving to content generation")
        
        return {
            "success": True,
            "message": "Idea accepted, ready for content generation",
            "next_step": "content",
            "idea_saved_to": idea_path
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling idea action: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/instagram/content/{session_id}", response_model=ContentResponse)
async def get_instagram_content(session_id: str):
    """Endpoint 12: Generate Instagram content (storyboard for reel or image prompt for post)"""
    try:
        session = validate_session_step(session_id, "generate_content")
        state = get_workflow_state(session_id)
        
        if not state or not state.get("current_idea"):
            raise HTTPException(status_code=400, detail="Idea required before content generation")
        
        content_type = state.get("content_type")
        current_idea = state["current_idea"]
        brand_name = state["brand_dna"]["brand_name"]
        
        # Build title
        if state.get("campaign_type") == "series":
            title = f"{brand_name} - Day {state.get('current_day', 1)} {content_type.capitalize()}"
        else:
            title = f"{brand_name} - {content_type.capitalize()}"
        
        logger.info(f"Generating {content_type} content for {session_id}")
        
        # Call agent for content generation
        agent_result = call_instagram_agent_safe(state, action="generate_content")
        
        # Build content data
        content_data = {
            "title": title,
            "idea": current_idea,
            "content_type": content_type,
            "generated_at": datetime.now().isoformat(),
            "brand_name": brand_name
        }
        
        files = []
        day_suffix = f"_day{state.get('current_day', 1)}" if state.get("campaign_type") == "series" else ""
        
        # Generate content based on type
        if content_type == "reel":
            storyboard = agent_result.get("storyboard", {
                "numOfScenes": 3,
                "scenePrompts": [
                    f"Hook: {current_idea[:100]}",
                    f"Value delivery: {brand_name} solution",
                    f"CTA: Engagement call"
                ],
                "dialogue": [
                    "Stop scrolling! You need to see this...",
                    "Here's how it works...",
                    "Try it now! Link in bio."
                ]
            })
            
            content_data["storyboard"] = storyboard
            
            # Save storyboard scenes
            for i, scene in enumerate(storyboard.get("scenePrompts", [])):
                scene_path = f"{state['campaign_folder']}/storyboards/scene{day_suffix}_{i+1}.txt"
                with open(scene_path, "w", encoding="utf-8") as f:
                    f.write(f"Scene {i+1}\n")
                    f.write(f"Prompt: {scene}\n")
                    if i < len(storyboard.get("dialogue", [])):
                        f.write(f"Dialogue: {storyboard['dialogue'][i]}\n")
                files.append(scene_path)
            
            # Save complete storyboard JSON
            storyboard_json_path = f"{state['campaign_folder']}/metadata/storyboard{day_suffix}.json"
            with open(storyboard_json_path, "w", encoding="utf-8") as f:
                json.dump(storyboard, f, indent=2)
            files.append(storyboard_json_path)
            
        else:  # post
            post_data = agent_result.get("post", {
                "post_type": "single",
                "image_prompts": [f"Professional {brand_name} image: {current_idea[:150]}"]
            })
            
            content_data["post"] = post_data
            
            # Save post prompts
            for i, prompt in enumerate(post_data.get("image_prompts", [])):
                post_path = f"{state['campaign_folder']}/posts/post{day_suffix}_{i+1}.txt"
                with open(post_path, "w", encoding="utf-8") as f:
                    f.write(f"Image Prompt:\n{prompt}\n\nIdea:\n{current_idea}")
                files.append(post_path)
            
            # Save post JSON
            post_json_path = f"{state['campaign_folder']}/metadata/post{day_suffix}.json"
            with open(post_json_path, "w", encoding="utf-8") as f:
                json.dump(post_data, f, indent=2)
            files.append(post_json_path)
        
        # Save complete content metadata
        meta_path = f"{state['campaign_folder']}/metadata/{content_type}_content{day_suffix}.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(content_data, f, indent=2)
        files.append(meta_path)
        
        # Store in state
        state["generated_content"].append({
            "type": content_type,
            "data": content_data,
            "files": files
        })
        
        # Move to caption generation
        update_workflow_step(session_id, "generate_caption")
        save_workflow_state(session_id, state)
        
        logger.info(f"Content generated for {session_id}: {len(files)} files created")
        
        return ContentResponse(
            success=True,
            content_type=content_type,
            content_data=content_data,
            files=files
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating Instagram content: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/instagram/caption/{session_id}", response_model=CaptionResponse)
async def get_instagram_caption(session_id: str):
    """Endpoint 13: Generate Instagram caption"""
    try:
        session = validate_session_step(session_id, "generate_caption")
        state = get_workflow_state(session_id)
        
        if not state or not state.get("current_idea"):
            raise HTTPException(status_code=400, detail="Content required before caption generation")
        
        logger.info(f"Generating caption for {session_id}")
        
        # Generate caption using agent or fallback
        brand_name = state["brand_dna"]["brand_name"]
        idea = state["current_idea"]
        content_type = state.get("content_type")
        
        # Try agent first
        try:
            if INSTAGRAM_AGENT_AVAILABLE:
                agent_result = call_instagram_agent_safe(state, action="generate_caption")
                caption = agent_result.get("caption")
        except:
            caption = None
        
        # Fallback caption
        if not caption:
            emojis = ["🔥", "✨", "💡", "🚀", "⭐"]
            hashtags = ["#instagram", "#content", "#growth", "#social", brand_name.lower().replace(" ", "")]
            
            caption = f"{emojis[len(state.get('captions', [])) % len(emojis)]} {idea}\n\n"
            caption += f"What do you think? Drop a comment below! 👇\n\n"
            caption += f"Follow @{brand_name.lower().replace(' ', '')} for more!\n\n"
            caption += " ".join([f"#{tag}" for tag in hashtags[:5]])
        
        # Save caption
        state["captions"].append(caption)
        
        day_suffix = f"_day{state.get('current_day', 1)}" if state.get("campaign_type") == "series" else ""
        caption_path = f"{state['campaign_folder']}/captions/caption{day_suffix}_{len(state['captions'])}.txt"
        os.makedirs(os.path.dirname(caption_path), exist_ok=True)
        
        with open(caption_path, "w", encoding="utf-8") as f:
            f.write(caption)
        
        # Determine next step
        if state.get("campaign_type") == "series":
            current_day = state.get("current_day", 1)
            total_days = state.get("days_series", 1)
            
            if current_day < total_days:
                next_step = "next_day"
                update_workflow_step(session_id, next_step)
            else:
                next_step = "completed"
                update_workflow_step(session_id, next_step, status="completed")
        else:
            next_step = "completed"
            update_workflow_step(session_id, next_step, status="completed")
        
        save_workflow_state(session_id, state)
        
        logger.info(f"Caption generated for {session_id}, next_step: {next_step}")
        
        return CaptionResponse(
            success=True,
            caption=caption,
            session_id=session_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating caption: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/instagram/next/{session_id}")
async def move_to_next_day(session_id: str):
    """Endpoint 14: Move to next day in series"""
    try:
        session = validate_session_step(session_id, "next_day")
        state = get_workflow_state(session_id)
        
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        if state.get("campaign_type") != "series":
            raise HTTPException(status_code=400, detail="Not a series campaign")
        
        current_day = state.get("current_day", 1)
        total_days = state.get("days_series", 1)
        
        if current_day >= total_days:
            return {
                "success": False,
                "message": "Series completed - all days done",
                "next_step": "summary"
            }
        
        # Move to next day
        state["current_day"] = current_day + 1
        state["current_idea"] = None  # Clear for new idea generation
        
        update_workflow_step(session_id, "generate_idea")
        save_workflow_state(session_id, state)
        
        logger.info(f"Moved to day {state['current_day']}/{total_days} for {session_id}")
        
        return {
            "success": True,
            "message": f"Moving to Day {state['current_day']} of {total_days}",
            "current_day": state["current_day"],
            "total_days": total_days,
            "next_step": "idea"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error moving to next day: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/instagram/summary/{session_id}", response_model=SummaryResponse)
async def get_instagram_summary(session_id: str):
    """Endpoint 15: Get Instagram campaign summary"""
    try:
        session = get_workflow_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        state = get_workflow_state(session_id)
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        # Build summary
        summary = {
            "brand_name": state["brand_dna"]["brand_name"],
            "campaign_type": state.get("campaign_type"),
            "content_type": state.get("content_type"),
            "ideas_generated": len(state.get("ideas", [])),
            "captions_generated": len(state.get("captions", [])),
            "content_generated": len(state.get("generated_content", [])),
            "campaign_folder": state.get("campaign_folder"),
            "status": session.get("status"),
            "created_at": session.get("created_at"),
            "completed_at": session.get("completed_at") or datetime.now().isoformat(),
            "files": []
        }
        
        # Add series info
        if state.get("campaign_type") == "series":
            summary["days_completed"] = state.get("current_day", 0)
            summary["total_days"] = state.get("days_series", 0)
        
        # List all generated files
        if state.get("campaign_folder") and os.path.exists(state["campaign_folder"]):
            for root, dirs, files in os.walk(state["campaign_folder"]):
                for file in files:
                    file_path = os.path.join(root, file)
                    summary["files"].append({
                        "path": file_path,
                        "relative": os.path.relpath(file_path, state["campaign_folder"]),
                        "size": os.path.getsize(file_path)
                    })
        
        logger.info(f"Summary generated for {session_id}: {len(summary['files'])} files")
        
        return SummaryResponse(
            success=True,
            session_id=session_id,
            summary=summary
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/instagram/progress/{session_id}")
async def get_instagram_progress(session_id: str):
    """Endpoint 16 (Bonus): Get detailed progress for series"""
    try:
        session = get_workflow_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        state = get_workflow_state(session_id)
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        if state.get("campaign_type") != "series":
            return {
                "message": "Not a series campaign",
                "campaign_type": "single"
            }
        
        total_days = state.get("days_series", 0)
        current_day = state.get("current_day", 0)
        
        progress = {
            "total_days": total_days,
            "current_day": current_day,
            "completed_days": current_day - 1 if session.get("current_step") != "completed" else current_day,
            "percentage": round((current_day / total_days) * 100, 2) if total_days > 0 else 0,
            "days_remaining": max(0, total_days - current_day),
            "status": session.get("status"),
            "current_step": session.get("current_step")
        }
        
        return progress
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting progress: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= NEW: ACTUAL IMAGE GENERATION ENDPOINTS (3 endpoints) =============

@app.post("/instagram/generate-images/{session_id}")
async def generate_actual_images(session_id: str):
    """Endpoint 17: Generate actual images using Vertex AI (calls your generate_instagram.py)"""
    try:
        session = validate_session_step(session_id)
        state = get_workflow_state(session_id)
        
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        if not state.get("current_idea"):
            raise HTTPException(status_code=400, detail="Need idea before generating images")
        
        content_type = state.get("content_type")
        if not content_type:
            raise HTTPException(status_code=400, detail="Content type not set")
        
        logger.info(f"Starting actual image generation for {session_id}")
        
        # Import your image generation functions
        try:
            from models.generate_instagram import (
                _generate_storyboard_prompts,
                _generate_storyboard_images,
                _generate_instagram_post_prompts,
                _generate_instagram_post_images,
                get_llm_local,
                get_image_model
            )
        except ImportError as e:
            logger.error(f"Failed to import image generation functions: {e}")
            raise HTTPException(status_code=500, detail="Image generation module not available")
        
        # Get models
        llm = get_llm_local()
        image_model = get_image_model()
        
        if not image_model:
            raise HTTPException(status_code=500, detail="Vertex AI image model not initialized")
        
        # Build title
        brand_name = state["brand_dna"]["brand_name"]
        if state.get("campaign_type") == "series":
            title = f"{brand_name} - Day {state.get('current_day', 1)}"
        else:
            title = f"{brand_name} - {content_type.capitalize()}"
        
        generated_images = []
        
        # Generate based on content type
        if content_type == "reel":
            logger.info("Generating storyboard images with Vertex AI")
            
            # Generate storyboard structure
            storyboard_data = _generate_storyboard_prompts(llm, title, state["current_idea"])
            
            # Generate actual images
            output_folder = f"{state['campaign_folder']}/images/storyboard"
            scene_images = _generate_storyboard_images(image_model, storyboard_data, title, output_folder)
            
            # Upload to GitHub (Mandatory)
            if not GITHUB_STORAGE_AVAILABLE:
                raise HTTPException(status_code=500, detail="GitHub storage not configured")

            final_images = []
            logger.info("Uploading storyboard images to GitHub...")
            with get_db_connection() as conn:
                cursor = conn.cursor()
                for local_path in scene_images:
                    try:
                        # Upload
                        upload_result = upload_image_to_github(local_path, session_id, content_type)
                        public_url = upload_result['url']
                        
                        # Log to DB
                        asset_id = str(uuid.uuid4())
                        cursor.execute('''
                            INSERT INTO media_assets 
                            (id, session_id, platform, content_type, storage, url, repo_path, sha, created_at)
                            VALUES (?, ?, 'instagram', ?, 'github', ?, ?, ?, ?)
                        ''', (asset_id, session_id, content_type, public_url, 
                              upload_result['repo_path'], upload_result['sha'], datetime.now().isoformat()))
                        
                        final_images.append(public_url)
                        logger.info(f"Uploaded {os.path.basename(local_path)} -> {public_url}")
                    except Exception as e:
                        logger.error(f"Failed to upload {local_path}: {e}")
                        raise HTTPException(status_code=500, detail=f"Failed to upload image to GitHub: {str(e)}")
                conn.commit()

            # Update variables
            scene_images = final_images
            generated_images = final_images
            
            # Save metadata
            image_meta = {
                "type": "storyboard",
                "title": title,
                "num_scenes": len(scene_images),
                "scene_prompts": storyboard_data.scenePrompts,
                "dialogue": storyboard_data.dialogue,
                "images": scene_images,
                "generated_at": datetime.now().isoformat()
            }
            
        else:  # post
            logger.info("Generating post images with Vertex AI")
            
            # Generate post structure
            post_data = _generate_instagram_post_prompts(llm, title, state["current_idea"])
            
            # Generate actual images
            output_folder = f"{state['campaign_folder']}/images/posts"
            post_images = _generate_instagram_post_images(image_model, post_data, title, output_folder)
            
            # Upload to GitHub (Mandatory)
            if not GITHUB_STORAGE_AVAILABLE:
                raise HTTPException(status_code=500, detail="GitHub storage not configured")

            final_images = []
            logger.info("Uploading post images to GitHub...")
            with get_db_connection() as conn:
                cursor = conn.cursor()
                for local_path in post_images:
                    try:
                        # Upload
                        upload_result = upload_image_to_github(local_path, session_id, content_type)
                        public_url = upload_result['url']
                        
                        # Log to DB
                        asset_id = str(uuid.uuid4())
                        cursor.execute('''
                            INSERT INTO media_assets 
                            (id, session_id, platform, content_type, storage, url, repo_path, sha, created_at)
                            VALUES (?, ?, 'instagram', ?, 'github', ?, ?, ?, ?)
                        ''', (asset_id, session_id, content_type, public_url, 
                              upload_result['repo_path'], upload_result['sha'], datetime.now().isoformat()))
                        
                        final_images.append(public_url)
                        logger.info(f"Uploaded {os.path.basename(local_path)} -> {public_url}")
                    except Exception as e:
                        logger.error(f"Failed to upload {local_path}: {e}")
                        raise HTTPException(status_code=500, detail=f"Failed to upload image to GitHub: {str(e)}")
                conn.commit()

            # Update variables
            post_images = final_images
            generated_images = final_images
            
            # Save metadata
            image_meta = {
                "type": "post",
                "title": title,
                "post_type": post_data.get("post_type", "single"),
                "num_images": len(post_images),
                "image_prompts": post_data.get("image_prompts", []),
                "images": post_images,
                "generated_at": datetime.now().isoformat()
            }
        
        # Save image metadata
        meta_path = f"{state['campaign_folder']}/metadata/generated_images_{len(generated_images)}.json"
        os.makedirs(os.path.dirname(meta_path), exist_ok=True)
        with open(meta_path, "w") as f:
            json.dump(image_meta, f, indent=2)
        
        # Update state
        if not state.get("generated_content"):
            state["generated_content"] = []
        
        state["generated_content"].append({
            "type": "images",
            "content_type": content_type,
            "images": generated_images,
            "metadata": image_meta
        })
        save_workflow_state(session_id, state)
        
        logger.info(f"Successfully generated {len(generated_images)} images")
        
        return {
            "success": True,
            "message": f"Generated {len(generated_images)} images",
            "content_type": content_type,
            "images": generated_images,
            "metadata": image_meta,
            "metadata_path": meta_path
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating images: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/instagram/images/{session_id}")
async def get_generated_images(session_id: str):
    """Endpoint 18: Get all generated images for a session"""
    try:
        session = validate_session_step(session_id)
        state = get_workflow_state(session_id)
        
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        generated_content = state.get("generated_content", [])
        
        # Filter for image content
        image_content = [
            content for content in generated_content 
            if content.get("type") == "images"
        ]
        
        if not image_content:
            return {
                "message": "No images generated yet",
                "images": [],
                "total": 0
            }
        
        # Collect all image paths/urls
        all_images = []
        for content in image_content:
            images = content.get("images", [])
            for img_ref in images:
                # Check if it's a URL
                if isinstance(img_ref, str) and (img_ref.startswith("http://") or img_ref.startswith("https://")):
                    all_images.append({
                        "path": img_ref,
                        "filename": os.path.basename(img_ref.split('?')[0]), # Simple filename extraction
                        "content_type": content.get("content_type"),
                        "size": 0, # Size unknown for remote URL without HEAD request
                        "created": datetime.now().isoformat(), # Approximate
                        "is_url": True
                    })
                # Check if it's a local path
                elif isinstance(img_ref, str) and os.path.exists(img_ref):
                    all_images.append({
                        "path": img_ref,
                        "filename": os.path.basename(img_ref),
                        "content_type": content.get("content_type"),
                        "size": os.path.getsize(img_ref),
                        "created": datetime.fromtimestamp(os.path.getctime(img_ref)).isoformat(),
                        "is_url": False
                    })
        
        return {
            "session_id": session_id,
            "images": all_images,
            "total": len(all_images),
            "content_types": list(set(img["content_type"] for img in all_images))
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting images: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/instagram/image/download/{session_id}/{filename}")
async def download_generated_image(session_id: str, filename: str):
    """Endpoint 19: Download specific generated image (Redirects to GitHub)"""
    try:
        # Check GitHub storage first (Primary)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Search by filename similarity in repo_path or url
            cursor.execute('''
                SELECT url FROM media_assets 
                WHERE session_id = ? AND (repo_path LIKE ? OR url LIKE ?)
            ''', (session_id, f"%{filename}", f"%{filename}"))
            row = cursor.fetchone()
            
            if row and row['url']:
                logger.info(f"Redirecting download to GitHub: {filename}")
                return RedirectResponse(url=row['url'])

        # Fallback to local (Legacy/Transient)
        session = validate_session_step(session_id)
        state = get_workflow_state(session_id)
        
        if state and state.get("campaign_folder"):
            images_folder = f"{state['campaign_folder']}/images"
            if os.path.exists(images_folder):
                for root, dirs, files in os.walk(images_folder):
                    if filename in files:
                        file_path = os.path.join(root, filename)
                        logger.info(f"Serving local legacy image: {filename}")
                        media_type = get_media_type(filename)
                        return FileResponse(
                            file_path,
                            media_type=media_type,
                            filename=filename,
                            headers={"Content-Disposition": f"attachment; filename={filename}"}
                        )
        
        raise HTTPException(status_code=404, detail=f"Image {filename} not found in GitHub or local storage")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading image: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= CHAT-BASED NATURAL LANGUAGE ENDPOINT =============

# Add this updated chat endpoint to replace the existing one in app.py

# Add this updated chat endpoint to replace the existing one in app.py

@app.post("/chat/message", response_model=ChatMessageResponse)
async def handle_chat_message(request: ChatMessageRequest):
    """Chat endpoint with flexible workflow - no strict step enforcement"""
    try:
        # Get session and state
        session = get_workflow_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        state = get_workflow_state(request.session_id)
        if not state:
            state = get_initial_state()
        
        # Get brand DNA
        brand_dna = state.get("brand_dna", {})
        if not brand_dna:
            return ChatMessageResponse(
                success=False,
                response="Please set up your brand DNA first before creating content.",
                action_taken=None
            )
        
        # Check if channel is selected
        channel = request.channel or state.get("channel")
        if not channel:
            return ChatMessageResponse(
                success=False,
                response="Please select a channel (Instagram or Email) first.",
                action_taken=None,
                next_step="channel_selection"
            )
        
        # Get LLM for intent understanding
        try:
            from models.generate_instagram import get_llm_local
            llm = get_llm_local()
        except:
            llm = None
        
        user_message = request.message.lower().strip()
        
        # Understand intent using LLM
        intent_data = _analyze_user_intent(llm, request.message, brand_dna, channel)
        
        # Now handle the request based on intent
        if channel == "instagram":
            # Check if content type selection is needed
            if intent_data.get("needs_content_type_selection"):
                return ChatMessageResponse(
                    success=True,
                    response="Great! Would you like to create a Reel or a Post?",
                    action_taken="awaiting_content_type_selection",
                    next_step="select_content_type"
                )
            
            return await _handle_instagram_chat(
                request.session_id, 
                state, 
                intent_data, 
                brand_dna,
                llm
            )
        else:  # email
            return await _handle_email_chat(
                request.session_id,
                state,
                intent_data,
                brand_dna,
                llm
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat message error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing chat: {str(e)}")


def _analyze_user_intent(llm, message: str, brand_dna: dict, channel: str) -> dict:
    """Analyze user intent from natural language message"""
    
    # Fallback intent detection (keyword-based)
    intent_data = {
        "intent": "generate_content",
        "content_type": None,
        "wants_images": True,
        "campaign_type": "single",
        "extracted_idea": message,
        "needs_content_type_selection": False
    }
    
    # Simple keyword matching
    msg_lower = message.lower()
    
    # Check for explicit content type
    has_reel = any(word in msg_lower for word in ["reel", "video", "reels", "script"])
    has_post = any(word in msg_lower for word in ["post", "image", "photo", "carousel"])
    
    if has_reel:
        intent_data["content_type"] = "reel"
    elif has_post:
        intent_data["content_type"] = "post"
    else:
        # User didn't specify - frontend should ask
        intent_data["needs_content_type_selection"] = True
    
    if any(word in msg_lower for word in ["series", "multiple", "campaign", "days"]):
        intent_data["campaign_type"] = "series"
    
    # Check if they just want an idea
    if any(word in msg_lower for word in ["idea", "suggest", "what should", "help me think"]):
        intent_data["intent"] = "just_idea"
        intent_data["wants_images"] = False
    
    # LLM-based intent (if available)
    if llm:
        try:
            intent_prompt = f"""Analyze this user message and extract:
1. What they want to create (post/reel/email)
2. Whether they want images generated or just ideas
3. The core concept/idea they're describing

User message: "{message}"
Brand: {brand_dna.get('brand_name', 'Unknown')}
Channel: {channel}

Return JSON:
{{
  "intent": "generate_content" or "just_idea",
  "content_type": "post" or "reel" or null,
  "wants_images": true or false,
  "extracted_idea": "cleaned, concise version of user's idea"
}}"""

            response = llm.invoke(intent_prompt)
            response_text = getattr(response, "content", str(response)).strip()
            
            # Clean JSON
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
            response_text = response_text.strip()
            
            parsed = json.loads(response_text)
            intent_data.update(parsed)
            
        except Exception as e:
            logger.warning(f"LLM intent parsing failed: {e}")
    
    return intent_data


async def _handle_instagram_chat(session_id: str, state: dict, intent_data: dict, brand_dna: dict, llm) -> ChatMessageResponse:
    """Handle Instagram content generation through chat"""
    
    try:
        # Set campaign folder if not exists
        # Set campaign folder if not exists
        if not state.get("campaign_folder"):
            # Check for linked campaign
            campaign_id = session.get("campaign_id")
            if not campaign_id:
                # Create ad-hoc campaign for chat session
                campaign_name = f"{brand_dna.get('brand_name', 'Chat')} Chat Campaign"
                campaign_id = create_campaign(campaign_name, "Created via Chat")
                # Update session
                update_workflow_step(session_id, session["current_step"]) # No-op to just trigger update if needed, but we need save_workflow_session to update campaign_id keys
                save_workflow_session(session_id, session["current_step"], campaign_id=campaign_id)
            
            campaign_folder = create_campaign_folder(brand_dna.get("brand_name", "campaign"), "instagram", campaign_id)
            state["campaign_folder"] = campaign_folder
        
        # Determine content type
        content_type = intent_data.get("content_type")
        if not content_type:
            # Default to post if not specified
            content_type = "post"
        
        # Update state
        state["channel"] = "instagram"
        state["content_type"] = content_type
        state["campaign_type"] = intent_data.get("campaign_type", "single")
        
        # Generate idea using LLM
        idea = _generate_concise_idea(llm, brand_dna, content_type, intent_data.get("extracted_idea", ""))
        state["current_idea"] = idea
        
        # Save state
        save_workflow_state(session_id, state)
        
        # If user just wants idea, return it
        if intent_data.get("intent") == "just_idea" or not intent_data.get("wants_images"):
            update_workflow_step(session_id, "idea_generated")
            
            return ChatMessageResponse(
                success=True,
                response=f"✨ **Content Idea:**\n\n{idea}\n\n**Type:** {content_type.capitalize()}\n\nWould you like me to generate the full content with images?",
                action_taken="generated_idea",
                content={"idea": idea, "content_type": content_type},
                next_step="awaiting_approval"
            )
        
        # Generate full content with images
        logger.info(f"Generating full {content_type} content with images")
        
        # Force workflow to content generation step (bypass validation)
        update_workflow_step(session_id, "generate_content", status="active")
        
        # Generate content structure
        content_result = await _generate_content_flexible(session_id, state, content_type)
        
        # Generate actual images
        images_result = await _generate_images_flexible(session_id, state, content_type)
        
        logger.info(f"Images generated: {images_result}")
        logger.info(f"Image count: {len(images_result) if images_result else 0}")
        
        # Generate caption
        caption_result = await _generate_caption_flexible(session_id, state)
        
        # Mark as completed
        update_workflow_step(session_id, "completed", status="completed")
        
        # Build response
        response_text = f"🎉 **Your {content_type.capitalize()} is Ready!**\n\n"
        response_text += f"**Idea:** {idea}\n\n"
        
        if caption_result:
            # Show first 100 chars of caption
            caption_preview = caption_result[:100] + "..." if len(caption_result) > 100 else caption_result
            response_text += f"**Caption Preview:**\n{caption_preview}\n\n"
        
        if images_result:
            response_text += f"**{len(images_result)} image(s) generated** ✓\n\n"
        
        response_text += "Check the preview panel for the full content!"
        
        # Build comprehensive content response
        content_response = {
            "idea": idea,
            "content_type": content_type,
            "caption": caption_result,
            "images": [
                {"path": img, "filename": os.path.basename(img)} if isinstance(img, str) else img
                for img in (images_result or [])
            ]
        }
        
        # Add content structure details
        if content_type == "reel" and content_result.get("storyboard"):
            content_response["storyboard"] = content_result["storyboard"]
        elif content_type == "post" and content_result.get("post_data"):
            content_response["post_data"] = content_result["post_data"]
        
        return ChatMessageResponse(
            success=True,
            response=response_text,
            action_taken="generated_complete_content",
            content=content_response,
            next_step="completed"
        )
        
    except Exception as e:
        logger.error(f"Instagram chat handler error: {e}")
        import traceback
        traceback.print_exc()
        
        return ChatMessageResponse(
            success=False,
            response=f"I encountered an error: {str(e)}. Please try rephrasing your request.",
            action_taken=None
        )


def _generate_concise_idea(llm, brand_dna: dict, content_type: str, user_input: str) -> str:
    """Generate a concise, engaging content idea"""
    
    if not llm:
        # Fallback: use user input or generate simple idea
        if user_input and len(user_input) > 10:
            return user_input[:200]
        return f"Create engaging {content_type} content for {brand_dna.get('brand_name', 'your brand')}"
    
    try:
        idea_prompt = f"""Create a concise, engaging Instagram {content_type} idea.

Brand: {brand_dna.get('brand_name')}
Values: {', '.join(brand_dna.get('brand_values', []))}
Audience: {', '.join(brand_dna.get('target_audience', []))}
User Request: {user_input}

Generate ONE specific, actionable content idea in 1-2 sentences. Be creative and on-brand.
Return ONLY the idea text, no JSON, no formatting."""

        response = llm.invoke(idea_prompt)
        idea = getattr(response, "content", str(response)).strip()
        
        # Clean up any markdown or formatting
        idea = idea.replace("**", "").replace("*", "").strip()
        
        # Limit length
        if len(idea) > 250:
            idea = idea[:247] + "..."
        
        return idea
        
    except Exception as e:
        logger.error(f"Idea generation error: {e}")
        return user_input[:200] if user_input else f"Create {content_type} content for {brand_dna.get('brand_name')}"


async def _generate_content_flexible(session_id: str, state: dict, content_type: str) -> dict:
    """Generate content structure without strict workflow validation"""
    
    try:
        from models.generate_instagram import (
            _generate_storyboard_prompts,
            _generate_instagram_post_prompts,
            get_llm_local
        )
        
        llm = get_llm_local()
        brand_name = state["brand_dna"]["brand_name"]
        idea = state["current_idea"]
        
        if content_type == "reel":
            storyboard = _generate_storyboard_prompts(llm, brand_name, idea)
            content_data = {
                "type": "reel",
                "content_type": "reel",
                "storyboard": {
                    "numOfScenes": storyboard.numOfScenes,
                    "scenePrompts": storyboard.scenePrompts,
                    "dialogue": storyboard.dialogue,
                    "sceneDescription": storyboard.sceneDescription
                }
            }
        else:  # post
            post_data = _generate_instagram_post_prompts(llm, brand_name, idea)
            content_data = {
                "type": "post",
                "content_type": "post",
                "post_data": post_data
            }
        
        # Save to state
        if not state.get("generated_content"):
            state["generated_content"] = []
        state["generated_content"].append(content_data)
        save_workflow_state(session_id, state)
        
        return content_data
        
    except Exception as e:
        logger.error(f"Content generation error: {e}")
        return {"type": content_type, "content_type": content_type, "error": str(e)}


async def _generate_images_flexible(session_id: str, state: dict, content_type: str) -> list:
    """Generate images without strict workflow validation"""
    
    try:
        from models.generate_instagram import (
            _generate_storyboard_images,
            _generate_instagram_post_images,
            get_image_model
        )
        
        image_model = get_image_model()
        if not image_model:
            logger.warning("Image model not available")
            return []
        
        brand_name = state["brand_dna"]["brand_name"]
        campaign_folder = state.get("campaign_folder", "campaigns/temp")
        
        if content_type == "reel":
            # Get storyboard from last generated content
            storyboard_data = None
            for content in reversed(state.get("generated_content", [])):
                if content.get("type") == "reel" and "storyboard" in content:
                    from models.generate_instagram import StoryBoardPrompt
                    storyboard_data = StoryBoardPrompt(**content["storyboard"])
                    break
            
            if not storyboard_data:
                return []
            
            output_folder = f"{campaign_folder}/images/storyboard"
            images = _generate_storyboard_images(image_model, storyboard_data, brand_name, output_folder)
            
        else:  # post
            # Get post data from last generated content
            post_data = None
            for content in reversed(state.get("generated_content", [])):
                if content.get("type") == "post" and "post_data" in content:
                    post_data = content["post_data"]
                    break
            
            if not post_data:
                return []
            
            output_folder = f"{campaign_folder}/images/posts"
            images = _generate_instagram_post_images(image_model, post_data, brand_name, output_folder)
        
        # Upload to GitHub (Mandatory)
        if not GITHUB_STORAGE_AVAILABLE:
             logger.error("GitHub storage not configured, cannot upload images")
             return []

        final_images = []
        if images:
            logger.info("Uploading images to GitHub (Flexible)...")
            with get_db_connection() as conn:
                cursor = conn.cursor()
                for local_path in images:
                    try:
                        # Upload
                        upload_result = upload_image_to_github(local_path, session_id, content_type)
                        public_url = upload_result['url']
                        
                        # Log to DB
                        asset_id = str(uuid.uuid4())
                        cursor.execute('''
                            INSERT INTO media_assets 
                            (id, session_id, platform, content_type, storage, url, repo_path, sha, created_at)
                            VALUES (?, ?, 'instagram', ?, 'github', ?, ?, ?, ?)
                        ''', (asset_id, session_id, content_type, public_url, 
                              upload_result['repo_path'], upload_result['sha'], datetime.now().isoformat()))
                        
                        final_images.append(public_url)
                        logger.info(f"Uploaded {os.path.basename(local_path)} -> {public_url}")
                    except Exception as e:
                        logger.error(f"Failed to upload {local_path}: {e}")
                        # In flexible chat mode, we just log error and don't return partial results or crash
                        pass
                conn.commit()
            
            # If upload failed for all images, return empty to signal failure
            if images and not final_images:
                 logger.error("All image uploads failed")
                 return []
                 
            return final_images

        return []
        
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        import traceback
        traceback.print_exc()
        return []


async def _generate_caption_flexible(session_id: str, state: dict) -> str:
    """Generate caption without strict workflow validation"""
    
    try:
        from models.generate_instagram import get_llm_local
        
        llm = get_llm_local()
        brand_name = state["brand_dna"]["brand_name"]
        idea = state["current_idea"]
        content_type = state.get("content_type", "post")
        
        if not llm:
            # Fallback caption
            return f"✨ {idea}\n\n{brand_name} | Follow for more! 🚀\n\n#instagram #{content_type} #content"
        
        caption_prompt = f"""Create an engaging Instagram caption.

Brand: {brand_name}
Content Type: {content_type}
Idea: {idea}
Brand Values: {', '.join(state['brand_dna'].get('brand_values', []))}

Create a caption with:
- Hook (first line to grab attention)
- Main content (2-3 lines)
- Call to action
- 5-8 relevant hashtags

Keep it concise, engaging, and on-brand. Return ONLY the caption text."""

        response = llm.invoke(caption_prompt)
        caption = getattr(response, "content", str(response)).strip()
        
        # Save caption
        if not state.get("captions"):
            state["captions"] = []
        state["captions"].append(caption)
        
        # Save to file
        campaign_folder = state.get("campaign_folder", "campaigns/temp")
        caption_path = f"{campaign_folder}/captions/caption_{len(state['captions'])}.txt"
        os.makedirs(os.path.dirname(caption_path), exist_ok=True)
        with open(caption_path, "w", encoding="utf-8") as f:
            f.write(caption)
        
        save_workflow_state(session_id, state)
        
        return caption
        
    except Exception as e:
        logger.error(f"Caption generation error: {e}")
        return f"✨ {state['current_idea']}\n\n#instagram #content"


async def _handle_email_chat(session_id: str, state: dict, intent_data: dict, brand_dna: dict, llm) -> ChatMessageResponse:
    """Handle email generation through chat"""
    
    try:
        # Set campaign folder if not exists
        # Set campaign folder if not exists
        if not state.get("campaign_folder"):
            # Check for linked campaign
            campaign_id = session.get("campaign_id")
            if not campaign_id:
                # Create ad-hoc campaign for chat session
                campaign_name = f"{brand_dna.get('brand_name', 'Chat')} Email Campaign"
                campaign_id = create_campaign(campaign_name, "Created via Chat")
                save_workflow_session(session_id, session["current_step"], campaign_id=campaign_id)

            campaign_folder = create_campaign_folder(brand_dna.get("brand_name", "campaign"), "email", campaign_id)
            state["campaign_folder"] = campaign_folder
        
        # Update state
        state["channel"] = "email"
        
        # Extract email config from intent
        user_message = intent_data.get("extracted_idea", "")
        state["email_config"] = {
            "email_type": "promotional",
            "target_audience": ", ".join(brand_dna.get("target_audience", [])),
            "key_message": user_message,
            "tone": "professional"
        }
        
        # Generate email idea
        idea = _generate_email_idea(llm, brand_dna, user_message)
        state["current_idea"] = idea
        
        # Generate email content
        email_content = _generate_email_content(llm, brand_dna, state["email_config"], idea)
        
        # Save email
        if not state.get("emails"):
            state["emails"] = []
        state["emails"].append(email_content)
        
        campaign_folder = state.get("campaign_folder", "campaigns/temp")
        email_path = f"{campaign_folder}/emails/email_{len(state['emails'])}.txt"
        os.makedirs(os.path.dirname(email_path), exist_ok=True)
        with open(email_path, "w", encoding="utf-8") as f:
            f.write(email_content)
        
        # Mark as completed
        update_workflow_step(session_id, "completed", status="completed")
        save_workflow_state(session_id, state)
        
        # Show preview of email (first 300 chars)
        email_preview = email_content[:300] + "..." if len(email_content) > 300 else email_content
        
        response_text = f"📧 **Your Email is Ready!**\n\n{email_preview}\n\nCheck the preview panel for the complete email!"
        
        return ChatMessageResponse(
            success=True,
            response=response_text,
            action_taken="generated_email",
            content={
                "idea": idea,
                "email_content": email_content,
                "type": "email"
            },
            next_step="completed"
        )
        
    except Exception as e:
        logger.error(f"Email chat handler error: {e}")
        return ChatMessageResponse(
            success=False,
            response=f"Error generating email: {str(e)}",
            action_taken=None
        )


def _generate_email_idea(llm, brand_dna: dict, user_input: str) -> str:
    """Generate concise email idea"""
    
    if not llm:
        return f"Email: {user_input[:100]}"
    
    try:
        idea_prompt = f"""Create a concise email subject/idea.

Brand: {brand_dna.get('brand_name')}
User Request: {user_input}

Generate ONE compelling email subject line or idea in 1 sentence.
Return ONLY the text, no formatting."""

        response = llm.invoke(idea_prompt)
        idea = getattr(response, "content", str(response)).strip()
        return idea[:150]
        
    except:
        return f"Email: {user_input[:100]}"


def _generate_email_content(llm, brand_dna: dict, email_config: dict, idea: str) -> str:
    """Generate email content"""
    
    brand_name = brand_dna.get("brand_name", "Brand")
    
    if not llm:
        return f"""Subject: {idea}

Dear {email_config.get('target_audience', 'Valued Customer')},

{email_config.get('key_message', 'We have an exciting update to share with you.')}

Thank you for being part of our community.

Best regards,
The {brand_name} Team"""
    
    try:
        email_prompt = f"""Create a professional email.

Brand: {brand_name}
Subject: {idea}
Target Audience: {email_config.get('target_audience')}
Key Message: {email_config.get('key_message')}
Tone: {email_config.get('tone', 'professional')}

Create a complete email with:
- Compelling subject line
- Professional greeting
- Clear message body (3-4 paragraphs)
- Call to action
- Professional signature

Return ONLY the email text."""

        response = llm.invoke(email_prompt)
        email_content = getattr(response, "content", str(response)).strip()
        return email_content
        
    except Exception as e:
        logger.error(f"Email content generation error: {e}")
        return f"""Subject: {idea}

Dear {email_config.get('target_audience')},

{email_config.get('key_message')}

Best regards,
{brand_name}"""
# ============= 5. EMAIL WORKFLOW (5 endpoints) =============

@app.post("/email/type")
async def choose_email_type(request: EmailTypeRequest):
    """Endpoint 17: Choose email type and configure"""
    try:
        session = validate_session_step(request.session_id, "choose_email_type")
        state = get_workflow_state(request.session_id)
        
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        # Save email configuration
        state["email_config"] = {
            "email_type": request.email_type,
            "target_audience": request.target_audience,
            "key_message": request.key_message,
            "tone": request.tone
        }
        
        update_workflow_step(request.session_id, "generate_idea")
        save_workflow_state(request.session_id, state)
        
        logger.info(f"Email type configured for {request.session_id}: {request.email_type}")
        
        return {
            "success": True,
            "message": f"Email type set to {request.email_type}",
            "session_id": request.session_id,
            "next_step": "idea",
            "config": state["email_config"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error choosing email type: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/email/idea/{session_id}", response_model=IdeaResponse)
async def get_email_idea(session_id: str):
    """Endpoint 18: Generate email idea"""
    try:
        session = validate_session_step(session_id, "generate_idea")
        state = get_workflow_state(session_id)
        
        if not state or not state.get("email_config"):
            raise HTTPException(status_code=400, detail="Email configuration required")
        
        # Generate idea if not exists
        if not state.get("current_idea"):
            logger.info(f"Generating email idea for {session_id}")
            
            result = call_email_agent_safe(state, action="generate_idea")
            state["current_idea"] = result.get("idea", f"Email: {state['email_config']['key_message']}")
            
            save_workflow_state(session_id, state)
        
        logger.info(f"Email idea retrieved for {session_id}")
        
        return IdeaResponse(
            success=True,
            idea=state["current_idea"],
            session_id=session_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting email idea: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/email/idea/action")
async def handle_email_idea_action(request: IdeaActionRequest):
    """Endpoint 19: Accept/modify/regenerate email idea"""
    try:
        session = validate_session_step(request.session_id, "generate_idea")
        state = get_workflow_state(request.session_id)
        
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        # Handle regenerate
        if request.action == "regenerate":
            state["current_idea"] = None
            save_workflow_state(request.session_id, state)
            
            logger.info(f"Regenerating email idea for {request.session_id}")
            
            return {
                "success": True,
                "message": "Regenerating email idea...",
                "next_step": "idea"
            }
        
        # Handle modify
        elif request.action == "modify":
            if not request.modified_idea:
                raise HTTPException(status_code=400, detail="Modified idea required")
            state["current_idea"] = request.modified_idea
            logger.info(f"Email idea modified for {request.session_id}")
        
        # Accept idea
        state["ideas"].append(state["current_idea"])
        
        # Save to file
        idea_path = f"{state['campaign_folder']}/metadata/email_idea_{len(state['ideas'])}.txt"
        os.makedirs(os.path.dirname(idea_path), exist_ok=True)
        with open(idea_path, "w", encoding="utf-8") as f:
            f.write(state["current_idea"])
        
        update_workflow_step(request.session_id, "generate_content")
        save_workflow_state(request.session_id, state)
        
        logger.info(f"Email idea accepted for {request.session_id}")
        
        return {
            "success": True,
            "message": "Email idea accepted",
            "next_step": "content"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling email idea action: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/email/content/{session_id}", response_model=ContentResponse)
async def get_email_content(session_id: str):
    """Endpoint 20: Generate email content"""
    try:
        session = validate_session_step(session_id, "generate_content")
        state = get_workflow_state(session_id)
        
        if not state or not state.get("current_idea"):
            raise HTTPException(status_code=400, detail="Idea required before email generation")
        
        logger.info(f"Generating email content for {session_id}")
        
        # Call email agent
        result = call_email_agent_safe(state, action="generate_content")
        
        config = state["email_config"]
        brand_name = state["brand_dna"]["brand_name"]
        
        # Generate email content
        email_content = result.get("email_content")
        
        if not email_content:
            # Fallback email
            email_content = f"""Subject: {state['current_idea']}

Dear {config['target_audience']},

{config['key_message']}

We're excited to share this update with you and hope it brings value to your day.

Best regards,
The {brand_name} Team

---
Tone: {config['tone']}
Type: {config['email_type']}"""
        
        # Save email
        state["emails"].append(email_content)
        
        email_path = f"{state['campaign_folder']}/emails/email_{len(state['emails'])}.txt"
        os.makedirs(os.path.dirname(email_path), exist_ok=True)
        with open(email_path, "w", encoding="utf-8") as f:
            f.write(email_content)
        
        # Save email metadata
        email_data = {
            "email_content": email_content,
            "config": config,
            "idea": state["current_idea"],
            "generated_at": datetime.now().isoformat(),
            "brand_name": brand_name
        }
        
        meta_path = f"{state['campaign_folder']}/metadata/email_content_{len(state['emails'])}.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(email_data, f, indent=2)
        
        # Complete workflow
        update_workflow_step(session_id, "completed", status="completed")
        save_workflow_state(session_id, state)
        
        logger.info(f"Email generated for {session_id}")
        
        return ContentResponse(
            success=True,
            content_type="email",
            content_data=email_data,
            files=[email_path, meta_path]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating email content: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/email/summary/{session_id}", response_model=SummaryResponse)
async def get_email_summary(session_id: str):
    """Endpoint 21: Get email campaign summary"""
    try:
        session = get_workflow_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        state = get_workflow_state(session_id)
        if not state:
            raise HTTPException(status_code=400, detail="State not found")
        
        summary = {
            "brand_name": state["brand_dna"]["brand_name"],
            "email_type": state["email_config"]["email_type"],
            "target_audience": state["email_config"]["target_audience"],
            "tone": state["email_config"]["tone"],
            "emails_generated": len(state.get("emails", [])),
            "campaign_folder": state.get("campaign_folder"),
            "status": session.get("status"),
            "created_at": session.get("created_at"),
            "completed_at": session.get("completed_at") or datetime.now().isoformat(),
            "files": []
        }
        
        # List all files
        if state.get("campaign_folder") and os.path.exists(state["campaign_folder"]):
            for root, dirs, files in os.walk(state["campaign_folder"]):
                for file in files:
                    file_path = os.path.join(root, file)
                    summary["files"].append({
                        "path": file_path,
                        "relative": os.path.relpath(file_path, state["campaign_folder"]),
                        "size": os.path.getsize(file_path)
                    })
        
        logger.info(f"Email summary generated for {session_id}")
        
        return SummaryResponse(
            success=True,
            session_id=session_id,
            summary=summary
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting email summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= 6. SCHEDULING (5 endpoints) =============

@app.post("/schedule")
async def create_schedule(request: ScheduleRequest):
    """Endpoint 22: Create new schedule entry (only after workflow is completed)"""
    try:
        schedule_id = str(uuid.uuid4())
        # Validate session if provided
        if request.session_id:
            session = get_workflow_session(request.session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Session not found")
            if session.get("status") != "completed":
                raise HTTPException(status_code=400, detail="Workflow must be completed before scheduling.")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO schedule_entries 
                (id, session_id, content_type, scheduled_date, status, 
                 content_description, instagram_username, content_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (schedule_id, request.session_id, request.content_type, 
                  request.scheduled_date.isoformat(), "pending",
                  request.content_description, request.instagram_username,
                  request.content_path, datetime.now().isoformat()))
            conn.commit()
        logger.info(f"Schedule created: {schedule_id} for {request.scheduled_date}")
        return {
            "success": True,
            "message": f"Scheduled for {request.scheduled_date.strftime('%Y-%m-%d %H:%M')}",
            "schedule_id": schedule_id,
            "scheduled_date": request.scheduled_date.isoformat(),
            "content_type": request.content_type
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating schedule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/schedule")
async def get_all_schedules(status: Optional[str] = None, content_type: Optional[str] = None):
    """Endpoint 23: Get all schedule entries with optional filters"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            query = 'SELECT * FROM schedule_entries WHERE 1=1'
            params = []
            
            if status:
                query += ' AND status = ?'
                params.append(status)
            
            if content_type:
                query += ' AND content_type = ?'
                params.append(content_type)
            
            query += ' ORDER BY scheduled_date ASC'
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            schedules = [dict(row) for row in rows]
        
        logger.info(f"Retrieved {len(schedules)} schedule entries")
        
        return {
            "schedule_entries": schedules,
            "total": len(schedules),
            "filters": {
                "status": status,
                "content_type": content_type
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting schedules: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/schedule/{schedule_id}")
async def get_schedule(schedule_id: str):
    """Endpoint 24: Get specific schedule entry"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM schedule_entries WHERE id = ?', (schedule_id,))
            row = cursor.fetchone()
            
            if not row:
                raise HTTPException(status_code=404, detail="Schedule not found")
            
            return dict(row)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting schedule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/schedule/{schedule_id}")
async def update_schedule(schedule_id: str, update: ScheduleUpdate):
    """Endpoint 25: Update schedule entry"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Check if exists
            cursor.execute('SELECT id FROM schedule_entries WHERE id = ?', (schedule_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Schedule not found")
            
            # Build update query
            update_fields = []
            update_values = []
            
            if update.status:
                update_fields.append('status = ?')
                update_values.append(update.status)
            
            if update.scheduled_date:
                update_fields.append('scheduled_date = ?')
                update_values.append(update.scheduled_date.isoformat())
            
            if not update_fields:
                return {"success": False, "message": "No fields to update"}
            
            # Execute update
            update_values.append(schedule_id)
            query = f"UPDATE schedule_entries SET {', '.join(update_fields)} WHERE id = ?"
            cursor.execute(query, update_values)
            conn.commit()
        
        logger.info(f"Schedule updated: {schedule_id}")
        
        return {
            "success": True,
            "message": "Schedule updated successfully",
            "schedule_id": schedule_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating schedule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/schedule/{schedule_id}")
async def delete_schedule(schedule_id: str):
    """Endpoint 26: Delete schedule entry"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM schedule_entries WHERE id = ?', (schedule_id,))
            
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Schedule not found")
            
            conn.commit()
        
        logger.info(f"Schedule deleted: {schedule_id}")
        
        return {
            "success": True,
            "message": "Schedule deleted successfully",
            "schedule_id": schedule_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting schedule: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= 7. SCHEDULER DAEMON (4 endpoints) =============

def check_scheduled_content():
    """Check for scheduled content that's ready to post"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            current_time = datetime.now()
            
            cursor.execute('SELECT * FROM schedule_entries WHERE status = "pending"')
            rows = cursor.fetchall()
            
            for row in rows:
                try:
                    scheduled_date_str = row['scheduled_date']
                    
                    # Parse datetime
                    if 'T' in scheduled_date_str:
                        scheduled_time = datetime.fromisoformat(scheduled_date_str.replace('Z', '+00:00'))
                    else:
                        scheduled_time = datetime.strptime(scheduled_date_str[:19], '%Y-%m-%d %H:%M:%S')
                    
                    # Remove timezone for comparison
                    if scheduled_time.tzinfo:
                        scheduled_time = scheduled_time.replace(tzinfo=None)
                    
                    # Check if ready
                    if current_time >= scheduled_time:
                        cursor.execute('''
                            UPDATE schedule_entries 
                            SET status = 'ready_to_post', notified_at = ?
                            WHERE id = ?
                        ''', (current_time.isoformat(), row['id']))
                        
                        logger.info(f"Schedule {row['id']} is ready to post")
                        
                except Exception as e:
                    logger.error(f"Error processing schedule {row.get('id')}: {e}")
                    cursor.execute('''
                        UPDATE schedule_entries 
                        SET error_message = ?
                        WHERE id = ?
                    ''', (str(e), row['id']))
                    continue
            
            conn.commit()
            
    except Exception as e:
        logger.error(f"Error in check_scheduled_content: {e}")

def scheduler_daemon():
    """Background scheduler daemon"""
    global scheduler_running
    logger.info("Scheduler daemon started")
    
    while scheduler_running:
        try:
            check_scheduled_content()
        except Exception as e:
            logger.error(f"Scheduler daemon error: {e}")
        
        time.sleep(60)  # Check every minute
    
    logger.info("Scheduler daemon stopped")

@app.post("/scheduler/start")
async def start_scheduler():
    """Endpoint 27: Start scheduler daemon"""
    global scheduler_running, scheduler_thread
    
    if scheduler_running:
        return {
            "success": False,
            "message": "Scheduler is already running"
        }
    
    scheduler_running = True
    scheduler_thread = threading.Thread(target=scheduler_daemon, daemon=True)
    scheduler_thread.start()
    
    logger.info("Scheduler daemon started")
    
    return {
        "success": True,
        "message": "Scheduler started successfully",
        "running": True
    }

@app.post("/scheduler/stop")
async def stop_scheduler():
    """Endpoint 28: Stop scheduler daemon"""
    global scheduler_running
    
    if not scheduler_running:
        return {
            "success": False,
            "message": "Scheduler is not running"
        }
    
    scheduler_running = False
    logger.info("Scheduler daemon stopping...")
    
    return {
        "success": True,
        "message": "Scheduler stopped successfully",
        "running": False
    }

@app.get("/scheduler/status")
async def get_scheduler_status():
    """Endpoint 29: Get scheduler status"""
    return {
        "running": scheduler_running,
        "status": "active" if scheduler_running else "stopped",
        "last_check": datetime.now().isoformat() if scheduler_running else None,
        "check_interval": "60 seconds"
    }

@app.post("/scheduler/check")
async def manual_scheduler_check():
    """Endpoint 30: Manually trigger scheduler check"""
    try:
        check_scheduled_content()
        
        # Get updated counts
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) as count FROM schedule_entries WHERE status = "ready_to_post"')
            ready_count = cursor.fetchone()['count']
            cursor.execute('SELECT COUNT(*) as count FROM schedule_entries WHERE status = "pending"')
            pending_count = cursor.fetchone()['count']
        
        logger.info("Manual scheduler check completed")
        
        return {
            "success": True,
            "message": "Scheduler check completed",
            "ready_to_post": ready_count,
            "pending": pending_count,
            "checked_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error in manual check: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= 8. CONTENT MANAGEMENT (3 endpoints) =============

@app.get("/content")
async def list_all_content(session_id: Optional[str] = None, content_type: Optional[str] = None):
    """Endpoint 31: List all generated content with filters"""
    try:
        content_files = []
        
        # Get files from database - NEW LOGIC (Media Assets)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            query = '''
                SELECT url, repo_path, content_type, platform, created_at, session_id
                FROM media_assets WHERE 1=1
            '''
            params = []
            
            if session_id:
                query += ' AND session_id = ?'
                params.append(session_id)
            
            if content_type:
                query += ' AND content_type = ?'
                params.append(content_type)
            
            query += ' ORDER BY created_at DESC'
            
            cursor.execute(query, params)
            db_files = cursor.fetchall()
            
            for row in db_files:
                content_files.append({
                    "filename": os.path.basename(row['repo_path']),
                    "path": row['url'],
                    "content_type": row['content_type'],
                    "username": row['platform'], # Mapping platform to username field for compatibility
                    "created": row['created_at'],
                    "size": 0, # Unknown size for remote assets
                    "session_id": row['session_id'],
                    "is_url": True,
                    "storage": "github"
                })
        
        logger.info(f"Retrieved {len(content_files)} media assets")
        
        return {
            "files": content_files,
            "total": len(content_files),
            "filters": {
                "session_id": session_id,
                "content_type": content_type
            }
        }
        
    except Exception as e:
        logger.error(f"Error listing content: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/content/download/{filename}")
async def download_content_file(filename: str):
    """Endpoint 32: Download specific file"""
    try:
        # Search in campaigns folder
        for root, dirs, files in os.walk("campaigns"):
            if filename in files:
                file_path = os.path.join(root, filename)
                if os.path.exists(file_path):
                    logger.info(f"Downloading file: {filename}")
                    
                    # Get correct media type
                    media_type = get_media_type(filename)
                    
                    return FileResponse(
                        file_path, 
                        media_type=media_type,
                        filename=filename,
                        headers={
                            "Content-Disposition": f"attachment; filename={filename}",
                            "Access-Control-Expose-Headers": "Content-Disposition"
                        }
                    )
        
        # Search in content folder
        for root, dirs, files in os.walk(content_folder):
            if filename in files:
                file_path = os.path.join(root, filename)
                if os.path.exists(file_path):
                    logger.info(f"Downloading file: {filename}")
                    
                    # Get correct media type
                    media_type = get_media_type(filename)
                    
                    return FileResponse(
                        file_path, 
                        media_type=media_type,
                        filename=filename,
                        headers={
                            "Content-Disposition": f"attachment; filename={filename}",
                            "Access-Control-Expose-Headers": "Content-Disposition"
                        }
                    )
        
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/content/campaign/{session_id}")
async def get_campaign_content(session_id: str):
    """Endpoint 33: Get all files for a specific campaign (Local + GitHub)"""
    try:
        session = validate_session_step(session_id)
        state = get_workflow_state(session_id)
        
        files = []
        
        # 1. Get GitHub Media Assets (Primary for images)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT url, repo_path, content_type, created_at 
                FROM media_assets WHERE session_id = ?
            ''', (session_id,))
            assets = cursor.fetchall()
            
            for asset in assets:
                filename = os.path.basename(asset['repo_path'])
                files.append({
                    "filename": filename,
                    "path": asset['url'],
                    "relative_path": asset['repo_path'], # Use repo path as relative
                    "size": 0,
                    "created": asset['created_at'],
                    "type": os.path.splitext(filename)[1],
                    "storage": "github",
                    "is_url": True
                })

        # 2. Get Local Files (Text/Metadata/Legacy)
        campaign_folder = state.get("campaign_folder") if state else None
        
        if campaign_folder and os.path.exists(campaign_folder):
            for root, dirs, filenames in os.walk(campaign_folder):
                for filename in filenames:
                    file_path = os.path.join(root, filename)
                    
                    # Add all local files (frontend can filter if needed)
                    files.append({
                        "filename": filename,
                        "path": file_path,
                        "relative_path": os.path.relpath(file_path, campaign_folder),
                        "size": os.path.getsize(file_path),
                        "created": datetime.fromtimestamp(os.path.getctime(file_path)).isoformat(),
                        "type": os.path.splitext(filename)[1],
                        "storage": "local",
                        "is_url": False
                    })
        
        logger.info(f"Retrieved {len(files)} files for campaign {session_id}")
        
        return {
            "files": files,
            "total": len(files),
            "campaign_folder": campaign_folder,
            "session_id": session_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting campaign content: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= 9. UTILITIES (3 endpoints) =============

@app.post("/utility/parse-instagram")
async def parse_instagram_endpoint(request: dict):
    """Endpoint 34: Parse Instagram username from various formats"""
    try:
        input_text = request.get("input_text", "")
        username = parse_instagram_input(input_text)
        
        return {
            "success": True,
            "original_input": input_text,
            "parsed_username": username,
            "profile_url": f"https://www.instagram.com/{username}/"
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error parsing Instagram input: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/utility/recommendations/{content_type}")
async def get_posting_recommendations(content_type: str):
    """Endpoint 35: Get recommended posting times"""
    try:
        base_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        if content_type == 'instagram':
            # Best times for Instagram
            times = [
                base_time + timedelta(days=1, hours=11),  # 11 AM tomorrow
                base_time + timedelta(days=1, hours=14),  # 2 PM tomorrow
                base_time + timedelta(days=1, hours=17),  # 5 PM tomorrow
                base_time + timedelta(days=2, hours=11),  # 11 AM day after
                base_time + timedelta(days=2, hours=19),  # 7 PM day after
            ]
            tips = [
                "Post during lunch hours (11 AM - 1 PM)",
                "Evening posts (5 PM - 7 PM) get high engagement",
                "Wednesday and Thursday are best days",
                "Avoid early mornings and late nights"
            ]
        elif content_type == 'email':
            # Best times for Email
            times = [
                base_time + timedelta(days=1, hours=10),  # 10 AM tomorrow
                base_time + timedelta(days=1, hours=14),  # 2 PM tomorrow
                base_time + timedelta(days=2, hours=10),  # 10 AM day after
                base_time + timedelta(days=3, hours=10),  # 10 AM 3 days later
            ]
            tips = [
                "Tuesday through Thursday mornings work best",
                "Avoid Monday mornings and Friday afternoons",
                "10 AM - 2 PM sees highest open rates",
                "Test different times for your audience"
            ]
        else:
            times = [base_time + timedelta(days=1, hours=12)]
            tips = ["Standard recommendation: noon tomorrow"]
        
        return {
            "content_type": content_type,
            "recommended_times": [t.isoformat() for t in times],
            "tips": tips,
            "timezone": "Local server time"
        }
        
    except Exception as e:
        logger.error(f"Error getting recommendations: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/utility/trends")
async def get_current_trends():
    """Endpoint 36: Get trending content suggestions (placeholder)"""
    try:
        # This is a placeholder - in production, connect to real trending APIs
        trends = {
            "hashtags": [
                "#trending", "#viral", "#fyp", "#explore", 
                "#contentcreator", "#socialmedia", "#marketing"
            ],
            "topics": [
                "AI and Technology",
                "Sustainability",
                "Mental Health",
                "Remote Work",
                "Personal Development"
            ],
            "content_ideas": [
                "Behind the scenes content",
                "Quick tips and tutorials",
                "User-generated content",
                "Interactive polls and quizzes",
                "Educational carousels"
            ],
            "best_formats": [
                {"format": "Reels", "engagement": "high", "reach": "excellent"},
                {"format": "Carousels", "engagement": "medium", "reach": "good"},
                {"format": "Stories", "engagement": "high", "reach": "followers only"}
            ],
            "note": "Connect to real trending APIs (TikTok, Instagram, Twitter) for live data",
            "updated_at": datetime.now().isoformat()
        }
        
        return {
            "success": True,
            "trends": trends
        }
        
    except Exception as e:
        logger.error(f"Error getting trends: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.post("/workflow/validate-step")
async def validate_workflow_step_endpoint(session_id: str, expected_step: str):
    """Validate that session is at expected step"""
    try:
        session = validate_session_step(session_id, expected_step)
        return {
            "valid": True,
            "current_step": session["current_step"],
            "status": session["status"],
            "message": f"Session is at expected step: {expected_step}"
        }
    except HTTPException as e:
        return {
            "valid": False,
            "error": str(e.detail),
            "message": "Step validation failed"
        }
    except Exception as e:
        logger.error(f"Error validating step: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============= ROOT & HEALTH =============

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Check database connectivity
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) as count FROM workflow_sessions')
            session_count = cursor.fetchone()['count']
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "database": "connected",
            "sessions": session_count,
            "scheduler": "running" if scheduler_running else "stopped",
            "modules": {
                "scraper": SCRAPER_AVAILABLE,
                "instagram_agent": INSTAGRAM_AGENT_AVAILABLE,
                "email_agent": EMAIL_AGENT_AVAILABLE
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

@app.get("/")
async def root():
    """Root endpoint with complete API documentation"""
    return {
        "service": "Unified Content Management System",
        "version": "4.0.1",  # Updated version
        "status": "operational",
        "total_endpoints": 41,  # Updated count (39 + 2 new optional endpoints)
        "endpoints": {
            "workflow_initiation": {
                "description": "Start and manage workflows",
                "endpoints": [
                    "POST /workflow/start - Initialize new workflow",
                    "POST /workflow/brand-dna - Submit brand DNA",
                    "GET /workflow/status/{session_id} - Get workflow status",
                    "POST /workflow/validate-step - Validate workflow step (NEW)",
                    "POST /workflow/batch-status - Get batch status (NEW)"
                ]
            },
            "instagram_analysis": {
                "description": "Analyze Instagram profiles",
                "endpoints": [
                    "POST /instagram/analyze - Analyze profile",
                    "GET /instagram/analyze/{username} - Get cached analysis"
                ]
            },
            "channel_selection": {
                "description": "Choose content channel",
                "endpoints": [
                    "POST /workflow/channel - Choose Instagram or Email"
                ]
            },
            "instagram_workflow": {
                "description": "Complete Instagram content workflow",
                "endpoints": [
                    "POST /instagram/campaign-type - Choose single/series",
                    "POST /instagram/series-config - Configure series",
                    "POST /instagram/content-type - Choose reel/post",
                    "GET /instagram/idea/{session_id} - Get idea",
                    "POST /instagram/idea/action - Accept/modify/regenerate",
                    "GET /instagram/content/{session_id} - Generate content",
                    "GET /instagram/caption/{session_id} - Generate caption",
                    "POST /instagram/next/{session_id} - Next day (series)",
                    "GET /instagram/summary/{session_id} - Get summary",
                    "GET /instagram/progress/{session_id} - Get progress"
                ]
            },
            "image_generation": {
                "description": "Generate actual images with Vertex AI",
                "endpoints": [
                    "POST /instagram/generate-images/{session_id} - Generate images",
                    "GET /instagram/images/{session_id} - Get all images",
                    "GET /instagram/image/download/{session_id}/{filename} - Download image (UPDATED)"
                ]
            },
            "email_workflow": {
                "description": "Complete Email content workflow",
                "endpoints": [
                    "POST /email/type - Choose email type",
                    "GET /email/idea/{session_id} - Get email idea",
                    "POST /email/idea/action - Accept/modify/regenerate",
                    "GET /email/content/{session_id} - Generate email",
                    "GET /email/summary/{session_id} - Get summary"
                ]
            },
            "scheduling": {
                "description": "Schedule content for posting",
                "endpoints": [
                    "POST /schedule - Create schedule",
                    "GET /schedule - List all schedules",
                    "GET /schedule/{id} - Get specific schedule",
                    "PUT /schedule/{id} - Update schedule",
                    "DELETE /schedule/{id} - Delete schedule"
                ]
            },
            "scheduler_daemon": {
                "description": "Background scheduler management",
                "endpoints": [
                    "POST /scheduler/start - Start scheduler",
                    "POST /scheduler/stop - Stop scheduler",
                    "GET /scheduler/status - Get scheduler status",
                    "POST /scheduler/check - Manual check"
                ]
            },
            "content_management": {
                "description": "Manage generated content",
                "endpoints": [
                    "GET /content - List all content",
                    "GET /content/download/{filename} - Download file (UPDATED)",
                    "GET /content/campaign/{session_id} - Get campaign files"
                ]
            },
            "utilities": {
                "description": "Utility functions",
                "endpoints": [
                    "POST /utility/parse-instagram - Parse Instagram input",
                    "GET /utility/recommendations/{type} - Get posting times",
                    "GET /utility/trends - Get trending topics"
                ]
            }
        },
        "documentation": "/docs",
        "health": "/health",
        "interactive_docs": "/redoc",
        "changelog": {
            "v4.0.1": [
                "Updated CORS configuration for multiple origins",
                "Enhanced file download with proper media types",
                "Added workflow validation endpoint",
                "Added batch status checking",
                "Improved error handling and logging"
            ]
        }
    }
