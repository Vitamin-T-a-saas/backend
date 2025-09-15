from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Annotated, Optional, Literal
from pydantic import Field, BaseModel
from datetime import datetime, timedelta
import os
import json
import schedule
import time
import threading
from dotenv import load_dotenv


# Import your existing modules
try:
    from myinstascrape import InstagramCompetitorAnalyzer
    from generate_instagram import run_instagram_agent
    from generate_email import run_email_agent
except ImportError as e:
    print(f"Warning: Could not import some modules: {e}")

load_dotenv()

class ScheduleEntry(BaseModel):
    content_type: str
    scheduled_date: datetime
    status: str = "pending"  # pending, ready_to_post, completed, failed
    content_description: str = ""
    instagram_username: str = ""
    content_path: str = ""  # Path to generated content file
    created_at: datetime = Field(default_factory=datetime.now)

class MainState(TypedDict):
    instagram_data: Optional[dict]
    insta_id: str
    user_choice: str
    content_results: Optional[dict]
    schedule_entries: Optional[list]

class UnifiedContentSystem:
    def __init__(self):
        self.schedule_log_path = "schedule_log.json"
        self.content_folder = "generated_content"
        self.scheduler_running = False
        
        # Create content folder if it doesn't exist
        os.makedirs(self.content_folder, exist_ok=True)
        
    def get_instagram_data(self, state: MainState) -> MainState:
        """Fetch Instagram data for the given Instagram ID."""
        try:
            print(f"\nFetching Instagram data for: @{state['insta_id']}")
            
            # Initialize the Instagram analyzer
            analyzer = InstagramCompetitorAnalyzer()
            
            # Format the profile URL
            profile_url = f"https://www.instagram.com/{state['insta_id'].replace('@', '')}/"
            
            # Scrape the profile data
            instagram_data = analyzer.scrape_competitor(profile_url)
            
            # Clean up the driver
            if hasattr(analyzer, 'driver'):
                analyzer.driver.quit()
            
            # Transform data to match your expected format
            if 'error' not in instagram_data:
                transformed_data = {
                    'username': instagram_data.get('username', state['insta_id']),
                    'followers': instagram_data.get('followers', 0),
                    'avg_likes': instagram_data.get('avg_likes', 0),
                    'avg_comments': instagram_data.get('avg_comments', 0),
                    'engagement_rate': instagram_data.get('engagement_rate', 0),
                    'posts_analyzed': instagram_data.get('posts_analyzed', 0),
                    'profile_url': instagram_data.get('profile_url', profile_url)
                }
                state['instagram_data'] = transformed_data
                print("✅ Instagram data fetched successfully!")
            else:
                print(f"⚠️ Error fetching Instagram data: {instagram_data.get('error', 'Unknown error')}")
                state['instagram_data'] = {
                    'username': state['insta_id'],
                    'followers': 0,
                    'avg_likes': 0,
                    'avg_comments': 0,
                    'engagement_rate': 0,
                    'posts_analyzed': 0,
                    'error': instagram_data.get('error', 'Failed to fetch data')
                }
            
            return state
        except Exception as e:
            print(f"❌ Error fetching Instagram data: {e}")
            state['instagram_data'] = {
                'username': state['insta_id'],
                'followers': 0,
                'avg_likes': 0,
                'avg_comments': 0,
                'engagement_rate': 0,
                'posts_analyzed': 0,
                'error': str(e)
            }
            return state

    def display_instagram_data(self, state: MainState) -> MainState:
        """Display Instagram data in a readable format."""
        print("\n" + "="*60)
        print("📊 INSTAGRAM PROFILE ANALYTICS")
        print("="*60)
        
        if state.get('instagram_data'):
            data = state['instagram_data']
            
            if 'error' in data:
                print(f"❌ Error: {data['error']}")
                print(f"Username: @{data.get('username', 'unknown')}")
            else:
                print(f"👤 Username: @{data.get('username', 'unknown')}")
                print(f"👥 Followers: {data.get('followers', 0):,}")
                print(f"❤️  Average Likes: {data.get('avg_likes', 0):,}")
                print(f"💬 Average Comments: {data.get('avg_comments', 0):,}")
                print(f"📈 Engagement Rate: {data.get('engagement_rate', 0):.2f}%")
                print(f"📸 Posts Analyzed: {data.get('posts_analyzed', 0)}")
                
                if data.get('profile_url'):
                    print(f"🔗 Profile URL: {data['profile_url']}")
        else:
            print("❌ No Instagram data available")
        
        print("="*60)
        return state

    def get_user_choice(self, state: MainState) -> MainState:
        """Get user choice for content type."""
        print("\n🎯 What type of content would you like to generate?")
        print("1️⃣  Instagram Content (Posts, Reels, Stories)")
        print("2️⃣  Email Content (Cold emails, Campaigns)")
        
        while True:
            choice = input("\n➡️  Enter your choice (1 for Instagram, 2 for Email): ").strip()
            
            if choice == "1":
                state['user_choice'] = "instagram"
                print("✅ Instagram content generation selected")
                break
            elif choice == "2":
                state['user_choice'] = "email"
                print("✅ Email content generation selected")
                break
            else:
                print("⚠️  Please enter 1 or 2")
        
        return state

    def generate_instagram_content(self, state: MainState) -> MainState:
        """Generate Instagram content."""
        print("\n🎨 Generating Instagram content...")
        try:
            result = run_instagram_agent()
            
            # Save content to file
            content_filename = self._save_content(result, 'instagram', state['insta_id'])
            
            state['content_results'] = {
                'type': 'instagram',
                'result': result,
                'success': True if result else False,
                'file_path': content_filename
            }
            
            if result:
                print("✅ Instagram content generated successfully!")
            else:
                print("⚠️  Instagram content generation completed with warnings")
            
        except Exception as e:
            print(f"❌ Error generating Instagram content: {e}")
            state['content_results'] = {
                'type': 'instagram',
                'result': None,
                'success': False,
                'error': str(e),
                'file_path': None
            }
        
        return state

    def generate_email_content(self, state: MainState) -> MainState:
        """Generate email content."""
        print("\n📧 Generating email content...")
        try:
            result = run_email_agent()
            
            # Save content to file
            content_filename = self._save_content(result, 'email', state['insta_id'])
            
            state['content_results'] = {
                'type': 'email',
                'result': result,
                'success': True if result else False,
                'file_path': content_filename
            }
            
            if result:
                print("✅ Email content generated successfully!")
            else:
                print("⚠️  Email content generation completed with warnings")
            
        except Exception as e:
            print(f"❌ Error generating email content: {e}")
            state['content_results'] = {
                'type': 'email',
                'result': None,
                'success': False,
                'error': str(e),
                'file_path': None
            }
        
        return state

    def _save_content(self, content, content_type, username):
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            date_folder = datetime.now().strftime("%Y-%m")
        
            if content_type == "instagram":
               base_dir = os.path.join(self.content_folder, "instagram", date_folder)
               filename = f"instagram_{username}_{timestamp}.txt"
            elif content_type == "email":
                base_dir = os.path.join(self.content_folder, "email", date_folder)
                filename = f"email_{username}_{timestamp}.txt"
            else:
            
               base_dir = self.content_folder
               filename = f"{content_type}_content_{username}_{timestamp}.txt"
        
        
            os.makedirs(base_dir, exist_ok=True)
        
            filepath = os.path.join(base_dir, filename)
        
            with open(filepath, 'w', encoding='utf-8') as f:
                if isinstance(content, dict):
                   f.write(json.dumps(content, indent=2, ensure_ascii=False))
                else:
                   f.write(str(content))
        
            print(f"💾 Content saved to: {filepath}")
            return filepath
        
        except Exception as e:
            print(f"⚠️  Error saving content: {e}")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{content_type}_content_{username}_{timestamp}.txt"
        filepath = os.path.join(self.content_folder, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            if isinstance(content, dict):
                f.write(json.dumps(content, indent=2, ensure_ascii=False))
            else:
                f.write(str(content))
        
        return filepath

    def setup_scheduler(self, state: MainState) -> MainState:
        """Setup content scheduling - FIXED VERSION"""
        print("\n" + "="*60)
        print("⏰ CONTENT SCHEDULING")
        print("="*60)
        
        print("\n📅 Would you like to schedule this content for future posting?")
        print("1️⃣  Yes, schedule it")
        print("2️⃣  No, just generate now")
        
        choice = input("\n➡️  Enter your choice (1/2): ").strip()
        
        if choice != "1":
            print("✅ Content generated and saved. No scheduling setup.")
            state['schedule_entries'] = []
            return state
        
        # Get scheduling details
        print("\n⏰ Scheduling Options:")
        print("1️⃣  Schedule for a specific date/time")
        print("2️⃣  Use recommended timing")
        
        schedule_choice = input("\n➡️  Enter your choice (1/2): ").strip()
        
        if schedule_choice == "2":
            # Recommend optimal times based on content type
            recommended_times = self._get_recommended_times(state)
            print(f"\n⭐ Recommended posting times:")
            for i, time in enumerate(recommended_times, 1):
                print(f"{i}️⃣  {time.strftime('%Y-%m-%d at %H:%M')}")
            
            try:
                time_choice = input(f"\n➡️  Choose time (1-{len(recommended_times)}): ").strip()
                scheduled_date = recommended_times[int(time_choice) - 1]
            except (ValueError, IndexError):
                print("⚠️  Invalid choice, using first recommendation")
                scheduled_date = recommended_times[0]
        else:
            scheduled_date = self._get_custom_datetime()
        
        # Create schedule entry - FIXED to ensure all fields are strings
        content_desc = f"{state['user_choice'].title()} content for @{state['insta_id']}"
        content_path = ""
        if state.get('content_results') and state['content_results'].get('file_path'):
            content_path = str(state['content_results']['file_path'])  # Ensure string
        
        schedule_entry = ScheduleEntry(
            content_type=state['user_choice'],
            scheduled_date=scheduled_date,
            content_description=content_desc,
            instagram_username=state['insta_id'],
            content_path=content_path
        )
        
        # Save to schedule log
        try:
            self._save_schedule_entry(schedule_entry)
            print(f"\n✅ Content scheduled for: {scheduled_date.strftime('%Y-%m-%d at %H:%M')}")
            print(f"📁 Schedule entry saved to: {self.schedule_log_path}")
            
            # FIXED: Use proper serialization
            try:
                state['schedule_entries'] = [schedule_entry.model_dump()]
            except AttributeError:
                # Fallback for older Pydantic versions
                state['schedule_entries'] = [schedule_entry.dict()]
                
        except Exception as e:
            print(f"❌ Error saving schedule: {e}")
            state['schedule_entries'] = []
        
        return state

    def _get_recommended_times(self, state: MainState) -> list:
        """Get recommended posting times based on content type."""
        base_time = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        if state['user_choice'] == 'instagram':
            # Instagram optimal times: 11 AM, 2 PM, 5 PM
            recommended = [
                base_time + timedelta(days=1, hours=11),  # Tomorrow 11 AM
                base_time + timedelta(days=1, hours=14),  # Tomorrow 2 PM
                base_time + timedelta(days=1, hours=17),  # Tomorrow 5 PM
                base_time + timedelta(days=2, hours=11),  # Day after tomorrow 11 AM
            ]
        else:  # email
            # Email optimal times: 10 AM, 2 PM (weekdays)
            recommended = [
                base_time + timedelta(days=1, hours=10),  # Tomorrow 10 AM
                base_time + timedelta(days=1, hours=14),  # Tomorrow 2 PM
                base_time + timedelta(days=2, hours=10),  # Day after tomorrow 10 AM
            ]
        
        return recommended

    def _get_custom_datetime(self) -> datetime:
        """Get custom date/time from user."""
        while True:
            try:
                print("\n📅 Enter your preferred date and time:")
                date_input = input("Date (YYYY-MM-DD): ").strip()
                time_input = input("Time (HH:MM): ").strip()
                
                datetime_str = f"{date_input} {time_input}"
                scheduled_date = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M")
                
                if scheduled_date <= datetime.now():
                    print("⚠️  Please enter a future date and time.")
                    continue
                
                confirm = input(f"\n✅ Confirm scheduling for {scheduled_date.strftime('%Y-%m-%d at %H:%M')}? (y/n): ").strip().lower()
                if confirm == 'y':
                    return scheduled_date
                
            except ValueError as e:
                print(f"❌ Invalid date/time format. Please use YYYY-MM-DD and HH:MM format.")

    def _save_schedule_entry(self, entry: ScheduleEntry):
        """Save schedule entry to log file - FIXED for Pydantic v2"""
        # Load existing entries
        schedule_log = []
        if os.path.exists(self.schedule_log_path):
            try:
                with open(self.schedule_log_path, "r") as f:
                    schedule_log = json.load(f)
            except:
                schedule_log = []
        
        # Add new entry - FIX: Use model_dump instead of dict
        try:
            entry_dict = entry.model_dump()  # Pydantic v2 method
        except AttributeError:
            # Fallback for older Pydantic versions
            entry_dict = entry.dict()
        
        # Convert datetime objects to strings
        for key, value in entry_dict.items():
            if isinstance(value, datetime):
                entry_dict[key] = value.isoformat()
        
        schedule_log.append(entry_dict)
        
        # Save back to file
        with open(self.schedule_log_path, "w") as f:
            json.dump(schedule_log, f, indent=2, default=str)

    def route_content_choice(self, state: MainState) -> Literal["instagram", "email"]:
        """Route based on user's content choice."""
        return state['user_choice']

    def start_scheduler_daemon(self):
        """Start the scheduler as a background daemon."""
        def run_scheduler():
            self.scheduler_running = True
            print("\n🤖 Background scheduler started...")
            print("⏰ Checking every minute for scheduled content...")
            
            while self.scheduler_running:
                self.check_scheduled_content()
                time.sleep(60)  # Check every minute
        
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()

    def check_scheduled_content(self):
        """Check for scheduled content that needs to be posted - IMPROVED"""
        if not os.path.exists(self.schedule_log_path):
            return
        
        try:
            with open(self.schedule_log_path, "r") as f:
                schedule_log = json.load(f)
            
            current_time = datetime.now()
            updated = False
            
            for entry in schedule_log:
                if entry['status'] == 'pending':
                    # Parse the scheduled date - IMPROVED parsing
                    try:
                        scheduled_date_str = entry['scheduled_date']
                        # Handle ISO format with timezone
                        if 'T' in scheduled_date_str:
                            if scheduled_date_str.endswith('Z'):
                                scheduled_time = datetime.fromisoformat(scheduled_date_str.replace('Z', '+00:00'))
                            elif '+' in scheduled_date_str or scheduled_date_str.endswith(('00:00', ':00')):
                                scheduled_time = datetime.fromisoformat(scheduled_date_str)
                            else:
                                scheduled_time = datetime.fromisoformat(scheduled_date_str)
                        else:
                            # Fallback parsing for simple datetime strings
                            scheduled_time = datetime.strptime(scheduled_date_str[:19], '%Y-%m-%d %H:%M:%S')
                        
                        # Remove timezone info for comparison
                        if scheduled_time.tzinfo:
                            scheduled_time = scheduled_time.replace(tzinfo=None)
                        
                    except Exception as parse_error:
                        print(f"⚠️  Error parsing date for entry: {parse_error}")
                        continue
                    
                    if current_time >= scheduled_time:
                        print(f"\n🔔 TIME TO POST: {entry['content_description']}")
                        print(f"📱 Content type: {entry['content_type']}")
                        print(f"⏰ Scheduled for: {entry['scheduled_date'][:16]}")
                        print(f"👤 Account: @{entry.get('instagram_username', 'unknown')}")
                        
                        content_path = entry.get('content_path', '')
                        if content_path and os.path.exists(content_path):
                            print(f"📁 Content file: {content_path}")
                        
                        # Update status
                        entry['status'] = 'ready_to_post'
                        entry['notified_at'] = current_time.isoformat()
                        updated = True
                        
                        print("📝 READY FOR MANUAL POSTING - Check your content file!")
            
            # Save updated log
            if updated:
                with open(self.schedule_log_path, "w") as f:
                    json.dump(schedule_log, f, indent=2, default=str)
                
        except Exception as e:
            print(f"⚠️  Error checking scheduled content: {e}")

    def show_schedule_log(self):
        """Display current schedule log - IMPROVED formatting"""
        if not os.path.exists(self.schedule_log_path):
            print("\n📋 No schedule log found.")
            return
        
        try:
            with open(self.schedule_log_path, "r") as f:
                schedule_log = json.load(f)
            
            if not schedule_log:
                print("\n📋 Schedule log is empty.")
                return
            
            print("\n" + "="*60)
            print("📋 CURRENT SCHEDULE LOG")
            print("="*60)
            
            # Group by status
            pending = [e for e in schedule_log if e.get('status') == 'pending']
            ready = [e for e in schedule_log if e.get('status') == 'ready_to_post']
            completed = [e for e in schedule_log if e.get('status') in ['completed', 'failed']]
            
            if ready:
                print(f"\n🔔 READY TO POST ({len(ready)} items):")
                for i, entry in enumerate(ready, 1):
                    print(f"  {i}. {entry.get('content_description', 'No description')}")
                    print(f"     📅 Was scheduled for: {entry.get('scheduled_date', 'Unknown')[:16]}")
                    content_path = entry.get('content_path', '')
                    if content_path:
                        print(f"     📁 File: {content_path}")
            
            if pending:
                print(f"\n⏳ PENDING ({len(pending)} items):")
                for i, entry in enumerate(pending, 1):
                    print(f"  {i}. {entry.get('content_description', 'No description')}")
                    print(f"     📅 Scheduled: {entry.get('scheduled_date', 'Unknown')[:16]}")
                    print(f"     📱 Type: {entry.get('content_type', 'Unknown').title()}")
            
            if completed:
                print(f"\n✅ COMPLETED ({len(completed)} items):")
                for i, entry in enumerate(completed[-5:], 1):  # Show last 5
                    status = entry.get('status', 'unknown').upper()
                    print(f"  {i}. {entry.get('content_description', 'No description')[:30]}... - {status}")
            
            print("="*60)
                
        except Exception as e:
            print(f"❌ Error reading schedule log: {e}")

def main():
    """Main function to run the unified content system."""
    system = UnifiedContentSystem()
    
    print("="*70)
    print("🎯 UNIFIED CONTENT MANAGEMENT SYSTEM")
    print("="*70)
    print("📱 Instagram Analytics → Content Generation → Smart Scheduling")
    print("="*70)
    
    # Get Instagram ID
    insta_input = input("\n👤 Enter your Instagram username or URL: ").strip()
    
    if not insta_input:
        print("❌ Instagram username is required. Exiting...")
        return
    
    # Extract username from URL if provided
    if "instagram.com/" in insta_input:
        insta_id = insta_input.split("instagram.com/")[-1].rstrip("/").split('?')[0]
    else:
        insta_id = insta_input.replace("@", "")
    
    print(f"✅ Analyzing Instagram account: @{insta_id}")
    
    # Create workflow
    workflow = StateGraph(MainState)
    
    # Add nodes
    workflow.add_node("get_instagram_data", system.get_instagram_data)
    workflow.add_node("display_instagram_data", system.display_instagram_data)
    workflow.add_node("get_user_choice", system.get_user_choice)
    workflow.add_node("generate_instagram_content", system.generate_instagram_content)
    workflow.add_node("generate_email_content", system.generate_email_content)
    workflow.add_node("setup_scheduler", system.setup_scheduler)
    
    # Add edges
    workflow.add_edge(START, "get_instagram_data")
    workflow.add_edge("get_instagram_data", "display_instagram_data")
    workflow.add_edge("display_instagram_data", "get_user_choice")
    
    # Conditional routing based on content choice
    workflow.add_conditional_edges(
        "get_user_choice",
        system.route_content_choice,
        {
            "instagram": "generate_instagram_content",
            "email": "generate_email_content"
        }
    )
    
    workflow.add_edge("generate_instagram_content", "setup_scheduler")
    workflow.add_edge("generate_email_content", "setup_scheduler")
    workflow.add_edge("setup_scheduler", END)
    
    # Compile and run
    app = workflow.compile()
    
    # Initialize state
    initial_state = MainState(
        instagram_data=None,
        insta_id=insta_id,
        user_choice="",
        content_results=None,
        schedule_entries=None
    )
    
    try:
        # Run the workflow
        print("\n🚀 Starting workflow...")
        result = app.invoke(initial_state)
        
        print("\n" + "="*70)
        print("✅ CONTENT GENERATION COMPLETE!")
        print("="*70)
        
        # Show current schedule log
        system.show_schedule_log()
        
        # Ask if user wants to start scheduler daemon
        if result.get('schedule_entries'):
            print("\n⏰ SCHEDULER OPTIONS:")
            print("1️⃣  Start background scheduler (monitors for posting time)")
            print("2️⃣  Exit (you can run scheduler later)")
            
            start_daemon = input("\n➡️  Enter your choice (1/2): ").strip()
            
            if start_daemon == '1':
                system.start_scheduler_daemon()
                print("\n🤖 Background scheduler is now running!")
                print("📱 The system will notify you when it's time to post.")
                print("⚠️  Press Ctrl+C to stop the scheduler and exit.")
                
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    system.scheduler_running = False
                    print("\n\n🛑 Scheduler stopped. Goodbye!")
            else:
                print("\n📝 You can restart this script anytime to check your schedule!")
        
        print(f"\n📁 All files saved in: {system.content_folder}/")
        print("✨ Thank you for using the Unified Content System!")
        
    except Exception as e:
        print(f"\n❌ Error during workflow execution: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()