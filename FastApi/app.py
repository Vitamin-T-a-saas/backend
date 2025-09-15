from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import os
import json
import threading
import time
import uuid
from pathlib import Path
import sqlite3
from contextlib import contextmanager

# Import your existing modules
try:
    from models.myinstascrape import InstagramCompetitorAnalyzer
    from models.generate_instagram import run_instagram_agent
    from models.generate_email import run_email_agent
except ImportError as e:
    print(f"Warning: Could not import some modules: {e}")

app = FastAPI(title="Unified Content Management System", version="1.0.0")

# Global variables
scheduler_running = False
scheduler_thread = None
content_folder = "generated_content"
db_path = "content_management.db"
instagram_cache = {}

# Ensure directories exist
os.makedirs(content_folder, exist_ok=True)

# Database setup
def init_database():
    """Initialize SQLite database with required tables."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Create schedule_entries table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schedule_entries (
            id TEXT PRIMARY KEY,
            content_type TEXT NOT NULL,
            scheduled_date TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            content_description TEXT,
            instagram_username TEXT,
            content_path TEXT,
            created_at TEXT NOT NULL,
            notified_at TEXT
        )
    ''')
    
    # Create instagram_cache table
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
    
    # Create content_files table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS content_files (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            content_type TEXT NOT NULL,
            username TEXT,
            created_at TEXT NOT NULL,
            file_size INTEGER DEFAULT 0
        )
    ''')
    
    conn.commit()
    conn.close()
    print("Database initialized successfully")

@contextmanager
def get_db_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # This allows accessing columns by name
    try:
        yield conn
    finally:
        conn.close()

# Initialize database on startup
init_database()

# Pydantic Models (keeping all existing models unchanged)
class AnalyzeRequest(BaseModel):
    instagram_input: str

class InstagramData(BaseModel):
    username: str
    followers: int = 0
    avg_likes: int = 0
    avg_comments: int = 0
    engagement_rate: float = 0.0
    posts_analyzed: int = 0
    profile_url: str = ""
    error: Optional[str] = None

class ContentResponse(BaseModel):
    success: bool
    content_type: str
    file_path: Optional[str] = None
    error: Optional[str] = None

class ScheduleRequest(BaseModel):
    content_type: str
    instagram_username: str
    scheduled_date: datetime
    content_description: str = ""
    content_path: str = ""

class ScheduleEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content_type: str
    scheduled_date: datetime
    status: str = "pending"
    content_description: str = ""
    instagram_username: str = ""
    content_path: str = ""
    created_at: datetime = Field(default_factory=datetime.now)

class ScheduleUpdate(BaseModel):
    status: Optional[str] = None
    scheduled_date: Optional[datetime] = None

class RecommendationRequest(BaseModel):
    content_type: str

class ParseInputRequest(BaseModel):
    input_text: str

# Database utility functions (replacing file-based operations)
def load_schedule_log() -> List[dict]:
    """Load schedule log from database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM schedule_entries 
            ORDER BY scheduled_date ASC
        ''')
        rows = cursor.fetchall()
        
        schedule_log = []
        for row in rows:
            entry = dict(row)
            schedule_log.append(entry)
        
        return schedule_log

def save_schedule_log(schedule_log: List[dict]):
    """Save schedule log to database (for compatibility, but individual saves are preferred)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Clear existing entries
        cursor.execute('DELETE FROM schedule_entries')
        
        # Insert all entries
        for entry in schedule_log:
            cursor.execute('''
                INSERT INTO schedule_entries 
                (id, content_type, scheduled_date, status, content_description, 
                 instagram_username, content_path, created_at, notified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                entry.get('id'),
                entry.get('content_type'),
                entry.get('scheduled_date'),
                entry.get('status', 'pending'),
                entry.get('content_description', ''),
                entry.get('instagram_username', ''),
                entry.get('content_path', ''),
                entry.get('created_at'),
                entry.get('notified_at')
            ))
        
        conn.commit()

def save_schedule_entry(entry: ScheduleEntry):
    """Save a single schedule entry to database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO schedule_entries 
            (id, content_type, scheduled_date, status, content_description, 
             instagram_username, content_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            entry.id,
            entry.content_type,
            entry.scheduled_date.isoformat(),
            entry.status,
            entry.content_description,
            entry.instagram_username,
            entry.content_path,
            entry.created_at.isoformat()
        ))
        
        conn.commit()

def save_instagram_cache(username: str, data: InstagramData):
    """Save Instagram data to database cache."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO instagram_cache 
            (username, followers, avg_likes, avg_comments, engagement_rate, 
             posts_analyzed, profile_url, error, cached_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            username,
            data.followers,
            data.avg_likes,
            data.avg_comments,
            data.engagement_rate,
            data.posts_analyzed,
            data.profile_url,
            data.error,
            datetime.now().isoformat()
        ))
        
        conn.commit()

def load_instagram_cache(username: str) -> Optional[InstagramData]:
    """Load Instagram data from database cache."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM instagram_cache WHERE username = ?
        ''', (username,))
        row = cursor.fetchone()
        
        if row:
            return InstagramData(
                username=row['username'],
                followers=row['followers'],
                avg_likes=row['avg_likes'],
                avg_comments=row['avg_comments'],
                engagement_rate=row['engagement_rate'],
                posts_analyzed=row['posts_analyzed'],
                profile_url=row['profile_url'],
                error=row['error']
            )
        
        return None

def save_content_file_record(file_path: str, content_type: str, username: str = "generated"):
    """Save content file record to database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        filename = os.path.basename(file_path)
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        
        cursor.execute('''
            INSERT INTO content_files 
            (id, filename, file_path, content_type, username, created_at, file_size)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(uuid.uuid4()),
            filename,
            file_path,
            content_type,
            username,
            datetime.now().isoformat(),
            file_size
        ))
        
        conn.commit()

# Existing utility functions (keeping unchanged)
def parse_instagram_input(input_text: str) -> str:
    """Parse Instagram URL/username input."""
    if not input_text.strip():
        raise ValueError("Instagram input is required")
    
    if "instagram.com/" in input_text:
        username = input_text.split("instagram.com/")[-1].rstrip("/").split('?')[0]
    else:
        username = input_text.replace("@", "")
    
    return username

def save_content(content: Any, content_type: str, username: str) -> str:
    """Save content to file and return filepath."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        date_folder = datetime.now().strftime("%Y-%m")
        
        if content_type == "instagram":
            base_dir = os.path.join(content_folder, "instagram", date_folder)
            filename = f"instagram_{username}_{timestamp}.txt"
        elif content_type == "email":
            base_dir = os.path.join(content_folder, "email", date_folder)
            filename = f"email_{username}_{timestamp}.txt"
        else:
            base_dir = content_folder
            filename = f"{content_type}_content_{username}_{timestamp}.txt"
        
        os.makedirs(base_dir, exist_ok=True)
        filepath = os.path.join(base_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            if isinstance(content, dict):
                f.write(json.dumps(content, indent=2, ensure_ascii=False))
            else:
                f.write(str(content))
        
        # Save file record to database
        save_content_file_record(filepath, content_type, username)
        
        return filepath
    except Exception as e:
        raise Exception(f"Error saving content: {e}")

def get_recommended_times(content_type: str) -> List[datetime]:
    """Get recommended posting times."""
    base_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    if content_type == 'instagram':
        recommended = [
            base_time + timedelta(days=1, hours=11),
            base_time + timedelta(days=1, hours=14),
            base_time + timedelta(days=1, hours=17),
            base_time + timedelta(days=2, hours=11),
        ]
    else:  # email
        recommended = [
            base_time + timedelta(days=1, hours=10),
            base_time + timedelta(days=1, hours=14),
            base_time + timedelta(days=2, hours=10),
        ]
    
    return recommended

def check_scheduled_content():
    """Check for scheduled content that's ready (now uses database)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        current_time = datetime.now()
        
        # Get pending entries
        cursor.execute('''
            SELECT * FROM schedule_entries 
            WHERE status = 'pending'
        ''')
        rows = cursor.fetchall()
        
        for row in rows:
            try:
                scheduled_date_str = row['scheduled_date']
                if 'T' in scheduled_date_str:
                    scheduled_time = datetime.fromisoformat(scheduled_date_str.replace('Z', '+00:00'))
                else:
                    scheduled_time = datetime.strptime(scheduled_date_str[:19], '%Y-%m-%d %H:%M:%S')
                
                if scheduled_time.tzinfo:
                    scheduled_time = scheduled_time.replace(tzinfo=None)
                
                if current_time >= scheduled_time:
                    # Update status to ready
                    cursor.execute('''
                        UPDATE schedule_entries 
                        SET status = 'ready_to_post', notified_at = ?
                        WHERE id = ?
                    ''', (current_time.isoformat(), row['id']))
                    
            except Exception as e:
                continue
        
        conn.commit()

def scheduler_daemon():
    """Background scheduler daemon."""
    global scheduler_running
    while scheduler_running:
        check_scheduled_content()
        time.sleep(60)

# API Endpoints (ALL UNCHANGED - just the underlying storage changed to database)

@app.post("/analyze", response_model=InstagramData)
async def analyze_instagram(request: AnalyzeRequest):
    """Analyze Instagram profile and cache data."""
    try:
        username = parse_instagram_input(request.instagram_input)
        
        # Check database cache first
        cached_data = load_instagram_cache(username)
        if cached_data:
            return cached_data
        
        # Initialize analyzer
        analyzer = InstagramCompetitorAnalyzer()
        profile_url = f"https://www.instagram.com/{username}/"
        
        # Scrape data
        instagram_data = analyzer.scrape_competitor(profile_url)
        
        # Clean up
        if hasattr(analyzer, 'driver'):
            analyzer.driver.quit()
        
        # Transform data
        if 'error' not in instagram_data:
            result = InstagramData(
                username=instagram_data.get('username', username),
                followers=instagram_data.get('followers', 0),
                avg_likes=instagram_data.get('avg_likes', 0),
                avg_comments=instagram_data.get('avg_comments', 0),
                engagement_rate=instagram_data.get('engagement_rate', 0),
                posts_analyzed=instagram_data.get('posts_analyzed', 0),
                profile_url=instagram_data.get('profile_url', profile_url)
            )
        else:
            result = InstagramData(
                username=username,
                error=instagram_data.get('error', 'Failed to fetch data')
            )
        
        # Cache result in database
        save_instagram_cache(username, result)
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing Instagram: {str(e)}")

@app.get("/analyze/{username}", response_model=InstagramData)
async def get_cached_analysis(username: str):
    """Get cached Instagram analysis data."""
    cached_data = load_instagram_cache(username)
    if not cached_data:
        raise HTTPException(status_code=404, detail="No cached data found for this username")
    
    return cached_data

@app.post("/generate/instagram", response_model=ContentResponse)
async def generate_instagram_content(background_tasks: BackgroundTasks):
    """Generate Instagram content."""
    try:
        result = run_instagram_agent()
        
        if result:
            # Save content
            file_path = save_content(result, 'instagram', 'generated')
            
            return ContentResponse(
                success=True,
                content_type='instagram',
                file_path=file_path
            )
        else:
            return ContentResponse(
                success=False,
                content_type='instagram',
                error="Content generation failed"
            )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating Instagram content: {str(e)}")

@app.post("/generate/email", response_model=ContentResponse)
async def generate_email_content(background_tasks: BackgroundTasks):
    """Generate email content."""
    try:
        result = run_email_agent()
        
        if result:
            # Save content
            file_path = save_content(result, 'email', 'generated')
            
            return ContentResponse(
                success=True,
                content_type='email',
                file_path=file_path
            )
        else:
            return ContentResponse(
                success=False,
                content_type='email',
                error="Content generation failed"
            )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating email content: {str(e)}")

@app.post("/schedule", response_model=dict)
async def create_schedule(request: ScheduleRequest):
    """Create new schedule entry."""
    try:
        entry = ScheduleEntry(
            content_type=request.content_type,
            scheduled_date=request.scheduled_date,
            content_description=request.content_description,
            instagram_username=request.instagram_username,
            content_path=request.content_path
        )
        
        save_schedule_entry(entry)
        
        return {
            "success": True,
            "message": f"Content scheduled for {request.scheduled_date.strftime('%Y-%m-%d at %H:%M')}",
            "schedule_id": entry.id
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating schedule: {str(e)}")

@app.get("/schedule")
async def get_schedule():
    """Get all schedule entries."""
    try:
        schedule_log = load_schedule_log()
        return {
            "schedule_entries": schedule_log,
            "total": len(schedule_log)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading schedule: {str(e)}")

@app.put("/schedule/{schedule_id}")
async def update_schedule(schedule_id: str, update: ScheduleUpdate):
    """Update schedule entry."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Check if entry exists
            cursor.execute('SELECT id FROM schedule_entries WHERE id = ?', (schedule_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Schedule entry not found")
            
            # Update entry
            update_fields = []
            update_values = []
            
            if update.status:
                update_fields.append('status = ?')
                update_values.append(update.status)
            
            if update.scheduled_date:
                update_fields.append('scheduled_date = ?')
                update_values.append(update.scheduled_date.isoformat())
            
            if update_fields:
                update_values.append(schedule_id)
                cursor.execute(f'''
                    UPDATE schedule_entries 
                    SET {', '.join(update_fields)}
                    WHERE id = ?
                ''', update_values)
                conn.commit()
            
            return {"success": True, "message": "Schedule updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating schedule: {str(e)}")

@app.delete("/schedule/{schedule_id}")
async def delete_schedule(schedule_id: str):
    """Delete schedule entry."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Check if entry exists and delete
            cursor.execute('DELETE FROM schedule_entries WHERE id = ?', (schedule_id,))
            
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Schedule entry not found")
            
            conn.commit()
            return {"success": True, "message": "Schedule entry deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting schedule: {str(e)}")

@app.post("/scheduler/start")
async def start_scheduler():
    """Start scheduler daemon."""
    global scheduler_running, scheduler_thread
    
    if scheduler_running:
        return {"success": False, "message": "Scheduler is already running"}
    
    scheduler_running = True
    scheduler_thread = threading.Thread(target=scheduler_daemon, daemon=True)
    scheduler_thread.start()
    
    return {"success": True, "message": "Scheduler started successfully"}

@app.post("/scheduler/stop")
async def stop_scheduler():
    """Stop scheduler daemon."""
    global scheduler_running
    
    if not scheduler_running:
        return {"success": False, "message": "Scheduler is not running"}
    
    scheduler_running = False
    return {"success": True, "message": "Scheduler stopped successfully"}

@app.get("/scheduler/status")
async def get_scheduler_status():
    """Get scheduler status."""
    return {
        "running": scheduler_running,
        "last_check": datetime.now().isoformat() if scheduler_running else None
    }

@app.post("/scheduler/check")
async def manual_check():
    """Manually trigger schedule check."""
    try:
        check_scheduled_content()
        return {"success": True, "message": "Schedule check completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking schedule: {str(e)}")

@app.get("/content")
async def list_content():
    """List all generated content files (now includes database records)."""
    try:
        content_files = []
        
        # Get from database first
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT filename, file_path, content_type, username, created_at, file_size
                FROM content_files 
                ORDER BY created_at DESC
            ''')
            db_files = cursor.fetchall()
            
            for row in db_files:
                if os.path.exists(row['file_path']):
                    relative_path = os.path.relpath(row['file_path'], content_folder)
                    content_files.append({
                        "filename": row['filename'],
                        "path": relative_path,
                        "full_path": row['file_path'],
                        "content_type": row['content_type'],
                        "username": row['username'],
                        "created": row['created_at'],
                        "size": row['file_size']
                    })
        
        # Also scan filesystem for any files not in database
        for root, dirs, files in os.walk(content_folder):
            for file in files:
                if file.endswith('.txt'):
                    file_path = os.path.join(root, file)
                    
                    # Check if already in our list from database
                    if not any(f['full_path'] == file_path for f in content_files):
                        relative_path = os.path.relpath(file_path, content_folder)
                        content_files.append({
                            "filename": file,
                            "path": relative_path,
                            "full_path": file_path,
                            "content_type": "unknown",
                            "username": "unknown",
                            "created": datetime.fromtimestamp(os.path.getctime(file_path)).isoformat(),
                            "size": os.path.getsize(file_path)
                        })
        
        return {
            "files": content_files,
            "total": len(content_files)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing content: {str(e)}")

@app.get("/content/{filename}")
async def download_content(filename: str):
    """Download content file."""
    try:
        # Search for file in content folder
        for root, dirs, files in os.walk(content_folder):
            if filename in files:
                file_path = os.path.join(root, filename)
                return FileResponse(
                    file_path, 
                    filename=filename,
                    media_type='text/plain'
                )
        
        raise HTTPException(status_code=404, detail="File not found")
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error downloading file: {str(e)}")

@app.post("/recommendations")
async def get_recommendations(request: RecommendationRequest):
    """Get recommended posting times."""
    try:
        recommended_times = get_recommended_times(request.content_type)
        
        return {
            "content_type": request.content_type,
            "recommended_times": [time.isoformat() for time in recommended_times]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting recommendations: {str(e)}")

@app.post("/parse-input")
async def parse_input(request: ParseInputRequest):
    """Parse Instagram URL/username input."""
    try:
        username = parse_instagram_input(request.input_text)
        return {
            "original_input": request.input_text,
            "parsed_username": username,
            "profile_url": f"https://www.instagram.com/{username}/"
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error parsing input: {str(e)}")

# Additional database management endpoints
@app.get("/database/status")
async def get_database_status():
    """Get database status and statistics."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            # Count entries in each table
            cursor.execute('SELECT COUNT(*) as count FROM schedule_entries')
            schedule_count = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM instagram_cache')
            cache_count = cursor.fetchone()['count']
            
            cursor.execute('SELECT COUNT(*) as count FROM content_files')
            files_count = cursor.fetchone()['count']
            
            return {
                "database_path": db_path,
                "database_exists": os.path.exists(db_path),
                "tables": {
                    "schedule_entries": schedule_count,
                    "instagram_cache": cache_count,
                    "content_files": files_count
                },
                "total_records": schedule_count + cache_count + files_count
            }
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting database status: {str(e)}")