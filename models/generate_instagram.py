from typing import TypedDict, List, Optional, Literal
import os, json
from datetime import datetime
from pathlib import Path

def run_instagram_agent():
    """Main function to run the Instagram agent with proper flow implementation"""
    
    try:
        from langgraph.graph import StateGraph, END
        from langgraph.checkpoint.memory import InMemorySaver
        from langchain_core.prompts import PromptTemplate
        from pydantic import BaseModel, Field
        
        memory_saver = InMemorySaver()
        
        def get_llm_local():
            """Get LLM instance locally to avoid circular import"""
            from langchain_google_genai import ChatGoogleGenerativeAI
            api_key = 'AIzaSyCzpzTqKGTd5SVzmpCdZmaj2eFMiUR-nsI'
            return ChatGoogleGenerativeAI(
                model="gemini-1.5-flash",
                temperature=0.7,
                convert_system_message_to_human=True,
                api_key=api_key
            )
        
        def get_image_model():
            """Lazy load image model to avoid circular imports"""
            try:
                import vertexai
                from vertexai.preview.vision_models import ImageGenerationModel
                
                # Check if already initialized
                if not hasattr(get_image_model, '_model'):
                    vertexai.init(project="gen-lang-client-0090000620", location="us-central1")
                    get_image_model._model = ImageGenerationModel.from_pretrained("imagen-3.0-generate-001")
                return get_image_model._model
            except Exception as e:
                print(f"Warning: Could not initialize image model: {e}")
                return None
    
    except ImportError as e:
        print(f"Error importing required packages: {e}")
        return
    
    # ------------------ INTEGRATED STORYBOARD CLASSES ------------------
    class StoryBoardPrompt(BaseModel):
        numOfScenes: int = Field(description="Number of scenes in the storyboard")
        scenePrompts: List[str] = Field(description="List of image prompts for each scene")
        dialogue: List[str] = Field(description="List of dialogues for each scene")
        sceneDescription: List[str] = Field(description="List of scene descriptions for each scene")

    class StoryboardResult(BaseModel):
        title: str = Field(description="Title of the reel")
        script: str = Field(description="Script of the reel")
        sceneimage: List[str] = Field(default=[], description="List of file paths for generated scene images")
        storyBoard: Optional[StoryBoardPrompt] = Field(default=None, description="Storyboard details")
        success: bool = Field(default=True, description="Whether generation was successful")
        error_message: Optional[str] = Field(default=None, description="Error message if failed")
    
    # NEW: Instagram Post Result Class
    class InstagramPostResult(BaseModel):
        title: str = Field(description="Title of the post")
        concept: str = Field(description="Post concept/idea")
        post_images: List[str] = Field(default=[], description="List of file paths for generated post images")
        post_type: str = Field(description="single or carousel")
        success: bool = Field(default=True, description="Whether generation was successful")
        error_message: Optional[str] = Field(default=None, description="Error message if failed")
    
    # ------------------ STATE ------------------
    class BrandDna(BaseModel):
        brand_name: str
        brand_description: str
        brand_values: List[str]
        target_audience: List[str]
        instagram_expectations: List[str]
    
    class InstagramState(TypedDict):
        brand_dna: Optional[BrandDna]
        user_choice: Optional[str]       # 'series' or 'single'
        content_type: Optional[str]      # 'reel' or 'post'
        days_series: Optional[int]       # Number of days for series
        current_day: Optional[int]       # Current day in series
        ideas: List[str]                 # List of ideas (for series or single) - FIXED: Always list
        current_idea: Optional[str]      # Current idea being processed
        storyboards: List[StoryboardResult]  # List of storyboards for reels - FIXED: Always list
        instagram_posts: List[InstagramPostResult]  # List of posts for static content - FIXED: Always list
        captions: List[str]              # List of captions - FIXED: Always list
        campaign_folder: Optional[str]   # Folder path for saving outputs
        saved_files: List[str]           # List of saved file paths - FIXED: Always list
    
    # ------------------ INTEGRATED STORYBOARD FUNCTIONS ------------------
    def _generate_storyboard_prompts(llm, title: str, script: str) -> StoryBoardPrompt:
        """Generate storyboard prompts using LLM with enhanced prompt engineering"""
        if llm is None:
            print("Warning: no llm provided to _generate_storyboard_prompts, using fallback")
            return _create_fallback_storyboard(title, script)

        system_msg = (
            "You are an expert Instagram reel director and storyboard artist. "
            "Create detailed, engaging storyboards that will capture attention and drive engagement. "
            "Focus on visual storytelling, smooth transitions, and Instagram-optimized content. "
            "Maximum 6 scenes for optimal reel length. Return ONLY valid JSON with no markdown formatting."
        )
        
        human_msg = f"""Title: "{title}"
Script/Concept: "{script}"

Create a detailed storyboard JSON with:
- 3-6 scenes optimized for vertical video (9:16 ratio)
- Compelling visual prompts that tell a story
- Engaging dialogue/text overlays
- Scene descriptions that flow naturally

Return this exact JSON format:
{{
  "numOfScenes": <number>,
  "scenePrompts": ["detailed visual prompt 1", "detailed visual prompt 2", ...],
  "dialogue": ["hook dialogue/text", "main content text", ...],
  "sceneDescription": ["opening scene description", "main content description", ...]
}}"""

        try:
            if hasattr(llm, "invoke"):
                raw = llm.invoke([{"role": "system", "content": system_msg}, {"role": "user", "content": human_msg}])
                response_content = getattr(raw, "content", str(raw)).strip()
            else:
                response_content = ""

            # Clean up response - improved JSON parsing
            if response_content.startswith("```"):
                lines = response_content.split('\n')
                json_start = -1
                json_end = -1
                for i, line in enumerate(lines):
                    if line.strip().startswith('{'):
                        json_start = i
                        break
                for i in range(len(lines) - 1, -1, -1):
                    if lines[i].strip().endswith('}'):
                        json_end = i + 1
                        break
                if json_start >= 0 and json_end > json_start:
                    response_content = '\n'.join(lines[json_start:json_end])

            if response_content.startswith("json"):
                response_content = response_content[4:].strip()

            # Parse JSON
            data = json.loads(response_content)
            
            # Validate required fields
            required_fields = ['numOfScenes', 'scenePrompts', 'dialogue', 'sceneDescription']
            for field in required_fields:
                if field not in data:
                    raise ValueError(f"Missing required field: {field}")
            
            # Ensure all lists have same length as numOfScenes
            num_scenes = data['numOfScenes']
            for field in ['scenePrompts', 'dialogue', 'sceneDescription']:
                if len(data[field]) != num_scenes:
                    # Pad or truncate to match numOfScenes
                    if len(data[field]) < num_scenes:
                        last_item = data[field][-1] if data[field] else f"Scene {num_scenes} content"
                        data[field].extend([last_item] * (num_scenes - len(data[field])))
                    else:
                        data[field] = data[field][:num_scenes]
            
            return StoryBoardPrompt(**data)

        except Exception as e:
            print(f"Warning: failed to parse LLM response into JSON: {e}")
            print(f"Raw response: {response_content}")
            return _create_fallback_storyboard(title, script)

    def _create_fallback_storyboard(title: str, script: str) -> StoryBoardPrompt:
        """Create fallback storyboard when LLM fails"""
        return StoryBoardPrompt(
            numOfScenes=3,
            scenePrompts=[
                f"Opening hook: dynamic intro visual for {title}, attention-grabbing, vertical format",
                f"Main content: core message delivery for {title}, engaging visual storytelling",
                f"Call to action: compelling ending with clear next steps, encourage engagement"
            ],
            dialogue=[
                "STOP SCROLLING! Here's what you need to know...",
                f"Main message from script: {script[:100]}..." if len(script) > 100 else script,
                "What do you think? Comment below!"
            ],
            sceneDescription=[
                "Dynamic opening scene with strong visual hook to stop scrolling",
                "Main content delivery with clear, engaging visual elements",
                "Strong CTA scene encouraging likes, comments, and follows"
            ]
        )

    def _generate_storyboard_images(image_model, storyboard_data: StoryBoardPrompt, title: str, output_folder: str) -> List[str]:
        """Generate images for storyboard scenes"""
        os.makedirs(output_folder, exist_ok=True)
        scene_images = []
        
        for i, prompt in enumerate(storyboard_data.scenePrompts, start=1):
            try:
                # Enhanced prompt for better image generation
                enhanced_prompt = (
                    f"Instagram reel storyboard frame {i}: {prompt}. "
                    "Vertical 9:16 aspect ratio, modern clean aesthetic, "
                    "professional lighting, engaging composition, "
                    "storyboard sketch style with clear visual elements."
                )
                
                print(f"  Generating image for scene {i}...")
                response = image_model.generate_images(
                    prompt=enhanced_prompt, 
                    number_of_images=1, 
                    aspect_ratio="9:16"
                )
                
                if not getattr(response, "images", None):
                    raise Exception("No images returned from model")
                    
                image = response.images[0]
                safe_title = "".join(c if c.isalnum() or c in (" ", "-", "_") else "" for c in title)
                file_name = f"{safe_title.replace(' ', '_')}_scene_{i}.png"
                file_path = os.path.join(output_folder, file_name)
                image.save(file_path)
                scene_images.append(file_path)
                print(f"  Scene {i} image saved: {file_name}")
                
            except Exception as e:
                print(f"  Image generation failed for scene {i}: {e}")
                dialogue = storyboard_data.dialogue[i-1] if i-1 < len(storyboard_data.dialogue) else ""
                placeholder = _create_text_placeholder(prompt, i, title, output_folder, dialogue)
                scene_images.append(placeholder)
                
        return scene_images
    
    # NEW: Instagram Post Generation Functions
    def _generate_instagram_post_prompts(llm, title: str, concept: str) -> dict:
        """Generate Instagram post image prompts using LLM"""
        if llm is None:
            print("Warning: no llm provided to _generate_instagram_post_prompts, using fallback")
            return _create_fallback_post_prompts(title, concept)

        system_msg = (
            "You are an expert Instagram post designer specializing in engaging static content. "
            "Create detailed image prompts for Instagram posts that capture attention and drive engagement. "
            "Focus on visual impact, brand consistency, and Instagram-optimized aesthetics. "
            "Return ONLY valid JSON with no markdown formatting."
        )
        
        human_msg = f"""Title: "{title}"
Concept: "{concept}"

Analyze the concept and create Instagram post specifications:
- Determine if this should be a single post or carousel (2-5 slides)
- Create detailed image prompts for each slide
- Focus on square format (1:1 ratio) for Instagram posts
- Ensure visual coherence and brand consistency

Return this exact JSON format:
{{
  "post_type": "single" or "carousel",
  "num_images": <number>,
  "image_prompts": ["detailed image prompt 1", "detailed image prompt 2", ...],
  "image_descriptions": ["description 1", "description 2", ...]
}}"""

        try:
            if hasattr(llm, "invoke"):
                raw = llm.invoke([{"role": "system", "content": system_msg}, {"role": "user", "content": human_msg}])
                response_content = getattr(raw, "content", str(raw)).strip()
            else:
                response_content = ""

            # Clean up response
            if response_content.startswith("```"):
                lines = response_content.split('\n')
                json_start = -1
                json_end = -1
                for i, line in enumerate(lines):
                    if line.strip().startswith('{'):
                        json_start = i
                        break
                for i in range(len(lines) - 1, -1, -1):
                    if lines[i].strip().endswith('}'):
                        json_end = i + 1
                        break
                if json_start >= 0 and json_end > json_start:
                    response_content = '\n'.join(lines[json_start:json_end])

            if response_content.startswith("json"):
                response_content = response_content[4:].strip()

            # Parse JSON
            data = json.loads(response_content)
            
            # Validate and clean data
            required_fields = ['post_type', 'num_images', 'image_prompts', 'image_descriptions']
            for field in required_fields:
                if field not in data:
                    raise ValueError(f"Missing required field: {field}")
            
            # Ensure consistency
            num_images = data['num_images']
            for field in ['image_prompts', 'image_descriptions']:
                if len(data[field]) != num_images:
                    if len(data[field]) < num_images:
                        last_item = data[field][-1] if data[field] else f"Image {num_images} content"
                        data[field].extend([last_item] * (num_images - len(data[field])))
                    else:
                        data[field] = data[field][:num_images]
            
            return data

        except Exception as e:
            print(f"Warning: failed to parse LLM response for post prompts: {e}")
            return _create_fallback_post_prompts(title, concept)

    def _create_fallback_post_prompts(title: str, concept: str) -> dict:
        """Create fallback post prompts when LLM fails"""
        return {
            "post_type": "single",
            "num_images": 1,
            "image_prompts": [
                f"Instagram post for {title}: {concept[:200]}. Square format, professional design, engaging visual elements, brand-consistent aesthetic."
            ],
            "image_descriptions": [
                f"Main Instagram post visual for {title} concept"
            ]
        }

    def _generate_instagram_post_images(image_model, post_data: dict, title: str, output_folder: str) -> List[str]:
        """Generate images for Instagram posts"""
        os.makedirs(output_folder, exist_ok=True)
        post_images = []
        
        for i, prompt in enumerate(post_data['image_prompts'], start=1):
            try:
                # Enhanced prompt for Instagram posts
                enhanced_prompt = (
                    f"Instagram post image {i}: {prompt}. "
                    "Square 1:1 aspect ratio, professional Instagram post design, "
                    "modern clean aesthetic, high engagement visual style, "
                    "brand-consistent colors and typography."
                )
                
                print(f"  Generating post image {i}...")
                response = image_model.generate_images(
                    prompt=enhanced_prompt, 
                    number_of_images=1, 
                    aspect_ratio="1:1"
                )
                
                if not getattr(response, "images", None):
                    raise Exception("No images returned from model")
                    
                image = response.images[0]
                safe_title = "".join(c if c.isalnum() or c in (" ", "-", "_") else "" for c in title)
                
                if post_data['post_type'] == 'carousel':
                    file_name = f"{safe_title.replace(' ', '_')}_slide_{i}.png"
                else:
                    file_name = f"{safe_title.replace(' ', '_')}_post.png"
                    
                file_path = os.path.join(output_folder, file_name)
                image.save(file_path)
                post_images.append(file_path)
                print(f"  Post image {i} saved: {file_name}")
                
            except Exception as e:
                print(f"  Post image generation failed for image {i}: {e}")
                description = post_data['image_descriptions'][i-1] if i-1 < len(post_data['image_descriptions']) else ""
                placeholder = _create_post_text_placeholder(prompt, i, title, output_folder, description)
                post_images.append(placeholder)
                
        return post_images

    def _create_post_text_placeholder(prompt: str, image_num: int, title: str, output_folder: str, description: str = "") -> str:
        """Create text placeholder for post when image generation fails"""
        os.makedirs(output_folder, exist_ok=True)
        safe_title = "".join(c if c.isalnum() or c in (" ", "-", "_") else "" for c in title)
        file_name = f"{safe_title.replace(' ', '_')}_image_{image_num}_placeholder.txt"
        file_path = os.path.join(output_folder, file_name)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"INSTAGRAM POST IMAGE {image_num}\n")
            f.write("=" * 50 + "\n")
            f.write(f"Title: {title}\n")
            f.write(f"Visual Description: {prompt}\n")
            if description:
                f.write(f"Image Description: {description}\n")
            f.write(f"\nNote: Image generation failed - using text placeholder.\n")
            f.write("This image should be created based on the description above.\n")
            
        return file_path

    def _create_text_placeholder(prompt: str, scene_num: int, title: str, output_folder: str, dialogue: str = "") -> str:
        """Create text placeholder when image generation fails"""
        os.makedirs(output_folder, exist_ok=True)
        safe_title = "".join(c if c.isalnum() or c in (" ", "-", "_") else "" for c in title)
        file_name = f"{safe_title.replace(' ', '_')}_scene_{scene_num}_placeholder.txt"
        file_path = os.path.join(output_folder, file_name)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"STORYBOARD SCENE {scene_num}\n")
            f.write("=" * 50 + "\n")
            f.write(f"Title: {title}\n")
            f.write(f"Visual Description: {prompt}\n")
            if dialogue:
                f.write(f"Dialogue/Text: {dialogue}\n")
            f.write(f"Scene Description: Scene {scene_num} placeholder\n")
            f.write("\nNote: Image generation failed - using text placeholder.\n")
            f.write("This scene should be visualized based on the description above.\n")
            
        return file_path
    
    # ------------------ HELPER FUNCTIONS ------------------
    def create_campaign_folder(brand_name: str, campaign_type: str) -> str:
        """Create organized folder structure for campaign"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_brand = "".join(c if c.isalnum() or c in " -_" else "" for c in brand_name).replace(" ", "_")
        folder_name = f"campaigns/{safe_brand}/{campaign_type}_{timestamp}"
        
        # Create subfolders
        Path(f"{folder_name}/storyboards").mkdir(parents=True, exist_ok=True)
        Path(f"{folder_name}/posts").mkdir(parents=True, exist_ok=True)
        Path(f"{folder_name}/images").mkdir(parents=True, exist_ok=True)
        Path(f"{folder_name}/captions").mkdir(parents=True, exist_ok=True)
        Path(f"{folder_name}/metadata").mkdir(parents=True, exist_ok=True)
        
        return folder_name
    
    def save_campaign_metadata(state: InstagramState):
        """Save all campaign metadata to JSON file"""
        if not state.get("campaign_folder"):
            return
        
        metadata = {
        "brand_dna": state["brand_dna"].dict() if state.get("brand_dna") else {},
        "campaign_type": state.get("user_choice", "unknown"),
        "content_type": state.get("content_type", "unknown"),
        "days_series": state.get("days_series"),
        "ideas": state.get("ideas", []),
        "captions": state.get("captions", []),
        "generated_at": datetime.now().isoformat(),
        "saved_files": state.get("saved_files", [])
    }
        
        metadata_path = f"{state['campaign_folder']}/metadata/campaign_info.json"
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        
        print(f"Campaign metadata saved to: {metadata_path}")
    
    def save_caption(caption: str, folder: str, index: int = 1) -> str:
        """Save caption to file"""
        caption_path = f"{folder}/captions/caption_{index}.txt"
        with open(caption_path, "w", encoding="utf-8") as f:
            f.write(caption)
        return caption_path
    
    # ------------------ INITIALIZE STATE HELPER ------------------
    def initialize_state() -> InstagramState:
        """Initialize state with proper empty lists to avoid NoneType errors"""
        return InstagramState(
            brand_dna=None,
            user_choice=None,
            content_type=None,
            days_series=None,
            current_day=None,
            ideas=[],                    # Always empty list, never None
            current_idea=None,
            storyboards=[],             # Always empty list, never None
            instagram_posts=[],         # Always empty list, never None
            captions=[],                # Always empty list, never None
            campaign_folder=None,
            saved_files=[]              # Always empty list, never None
        )
    
    # ------------------ NODE FUNCTIONS ------------------
    def collect_brand_dna(state: InstagramState) -> InstagramState:
        """Collect brand DNA from user"""
        print("\nLet's define your brand DNA:")
        
        brand_name = input("Brand Name: ").strip()
        brand_description = input("Brand Description: ").strip()
        brand_values = input("Brand Values (comma-separated): ").strip().split(",")
        target_audience = input("Target Audience (comma-separated): ").strip().split(",")
        instagram_expectations = input("Instagram Expectations (comma-separated): ").strip().split(",")
        
        state["brand_dna"] = BrandDna(
            brand_name=brand_name,
            brand_description=brand_description,
            brand_values=[v.strip() for v in brand_values],
            target_audience=[a.strip() for a in target_audience],
            instagram_expectations=[e.strip() for e in instagram_expectations]
        )
        
        # Create initial campaign folder
        state["campaign_folder"] = create_campaign_folder(brand_name, "campaign")
        
        return state
    
    def choose_series_or_single(state: InstagramState) -> InstagramState:
        """User chooses between series or single content"""
        print("\nWhat type of content do you want to create?")
        print("1. Series (multiple related posts)")
        print("2. Single post")
        
        choice = input("Enter your choice (1/2): ").strip()
        state["user_choice"] = "series" if choice == "1" else "single"
        
        # Update folder name based on choice
        if state.get("campaign_folder"):
            old_folder = state["campaign_folder"]
            new_folder = old_folder.replace("/campaign_", f"/{state['user_choice']}_")
            if old_folder != new_folder:
                try:
                    os.rename(old_folder, new_folder)
                    state["campaign_folder"] = new_folder
                except OSError:
                    # If rename fails, keep original folder
                    pass
        
        return state
    
    def get_series_days(state: InstagramState) -> InstagramState:
        """Get number of days for series"""
        days = input("\nHow many days for the series? (e.g., 3, 5, 7): ").strip()
        try:
            state["days_series"] = int(days)
        except ValueError:
            state["days_series"] = 3  # Default to 3 days
        
        state["current_day"] = 1
        return state
    
    def choose_content_type(state: InstagramState) -> InstagramState:
        """Choose between reel or post"""
        print("\nWhat type of content?")
        print("1. Reel (with storyboard)")
        print("2. Post (static image)")
        
        choice = input("Enter your choice (1/2): ").strip()
        state["content_type"] = "reel" if choice == "1" else "post"
        return state
    
    def generate_series_idea(state: InstagramState) -> InstagramState:
        """Generate idea for current day in series"""
        llm = get_llm_local()
        current_day = state.get("current_day", 1)
        total_days = state.get("days_series", 3)
        
        prompt = PromptTemplate(
            template="""You are an expert Instagram content strategist creating a cohesive series.
            
            Brand DNA: {brand_dna}
            Series Progress: Day {current_day} of {total_days}
            Content Type: {content_type}
            Previous Ideas: {previous_ideas}
            
            Generate a creative, engaging {content_type} idea for Day {current_day} that:
            1. Builds on the series theme and maintains continuity
            2. Provides unique value while connecting to previous days
            3. Includes a strong hook for the first 3 seconds
            4. Has clear main content that delivers value
            5. Ends with a compelling CTA
            6. Is optimized for Instagram engagement
            
            Make it actionable, specific, and ready for production.""",
            input_variables=["brand_dna", "content_type", "current_day", "total_days", "previous_ideas"]
        )
        
        previous_ideas = "\n".join(state["ideas"]) if state["ideas"] else "None (this is the first in the series)"
        message = prompt.format(
            brand_dna=json.dumps(state["brand_dna"].dict()),
            content_type=state.get("content_type", "reel"),
            current_day=current_day,
            total_days=total_days,
            previous_ideas=previous_ideas
        )
        
        response = llm.invoke(message)
        idea = response.content.strip()
        state["current_idea"] = idea
        
        print(f"\nDay {current_day} Idea Generated:")
        print("-" * 50)
        print(idea)
        print("-" * 50)
        
        return state
    
    def generate_single_idea(state: InstagramState) -> InstagramState:
        """Generate single content idea"""
        llm = get_llm_local()
        content_type = state.get("content_type", "reel")
        
        if content_type == "reel":
            prompt_template = """You are an expert Instagram reel strategist specializing in viral content.
            
            Brand DNA: {brand_dna}
            
            Create a high-engagement Instagram reel concept that:
            1. HOOK (First 3 seconds): Stops scrolling with compelling opening
            2. MAIN CONTENT: Delivers clear value/entertainment 
            3. CTA: Strong call-to-action for engagement
            
            Focus on:
            - Trending formats and styles
            - Visual storytelling opportunities
            - Shareable, save-worthy content
            - Brand alignment and authenticity
            - Clear production guidelines
            
            Make it specific, actionable, and optimized for the algorithm."""
        else:
            prompt_template = """You are an expert Instagram post strategist creating engaging static content.
            
            Brand DNA: {brand_dna}
            
            Create a compelling Instagram post concept with:
            1. VISUAL CONCEPT: Detailed description of the image/carousel
            2. KEY MESSAGE: Core value proposition
            3. ENGAGEMENT STRATEGY: How to encourage interaction
            
            Focus on:
            - Eye-catching visual elements
            - Value-driven content
            - Community engagement
            - Brand consistency
            - Clear production requirements
            
            Make it specific and ready for creation."""
        
        prompt = PromptTemplate(
            template=prompt_template,
            input_variables=["brand_dna"]
        )
        
        response = llm.invoke(prompt.format(brand_dna=json.dumps(state["brand_dna"].dict())))
        idea = response.content.strip()
        state["current_idea"] = idea
        
        print(f"\n{content_type.capitalize()} Idea Generated:")
        print("-" * 50)
        print(idea)
        print("-" * 50)
        
        return state
    
    def accept_or_modify_idea(state: InstagramState) -> InstagramState:
        """Let user accept, reject, or regenerate the idea"""
        print("\nWhat would you like to do with this idea?")
        print("1. Accept and continue")
        print("2. Regenerate new idea")
        print("3. Modify manually")
        
        choice = input("Enter your choice (1/2/3): ").strip()
        
        if choice == "1":
            # Accept the idea
            state["ideas"].append(state["current_idea"])
            
            # Save idea to file
            idea_index = len(state["ideas"])
            idea_path = f"{state['campaign_folder']}/metadata/idea_{idea_index}.txt"
            with open(idea_path, "w", encoding="utf-8") as f:
                f.write(state["current_idea"])
            print(f"Idea saved to: {idea_path}")
            
            return state
            
        elif choice == "3":
            # Manual modification
            print("\nEnter your modified idea (or press Enter to keep current):")
            modified = input().strip()
            if modified:
                state["current_idea"] = modified
                print("Idea modified successfully!")
            
            state["ideas"].append(state["current_idea"])
            
            # Save modified idea
            idea_index = len(state["ideas"])
            idea_path = f"{state['campaign_folder']}/metadata/idea_{idea_index}.txt"
            with open(idea_path, "w", encoding="utf-8") as f:
                f.write(state["current_idea"])
            print(f"Modified idea saved to: {idea_path}")
            
            return state
        
        # Choice 2: Regenerate - the graph will handle re-routing
        print("Regenerating new idea...")
        return state
    
    def generate_instagram_post_node(state: InstagramState) -> InstagramState:
        """Generate Instagram post for static content"""
        if state.get("content_type") != "post":
            return state
        
        print("\nGenerating Instagram post...")
        
        # Get models
        llm = get_llm_local()
        image_model = get_image_model()
        
        # Determine title
        if state.get("user_choice") == "series":
            current_day = state.get("current_day", 1)
            brand_name = state['brand_dna'].brand_name if state.get('brand_dna') else "Brand"
            title = f"{brand_name} - Day {current_day}"
        else:
            brand_name = state['brand_dna'].brand_name if state.get('brand_dna') else "Brand"
            title = f"{brand_name} - Post"
        
        # Set output folder
        posts_folder = f"{state['campaign_folder']}/posts"
        
        try:
            # Step 1: Generate post structure
            print("  Creating post structure...")
            post_data = _generate_instagram_post_prompts(llm, title, state["current_idea"])
            
            # Step 2: Generate images or placeholders
            if image_model:
                print("  Generating post images...")
                post_images = _generate_instagram_post_images(image_model, post_data, title, posts_folder)
            else:
                print("  Creating text placeholders (image model unavailable)...")
                post_images = []
                for i, prompt in enumerate(post_data['image_prompts'], start=1):
                    description = post_data['image_descriptions'][i-1] if i-1 < len(post_data['image_descriptions']) else ""
                    placeholder = _create_post_text_placeholder(prompt, i, title, posts_folder, description)
                    post_images.append(placeholder)
            
            # Create result
            post_result = InstagramPostResult(
                title=title,
                concept=state["current_idea"],
                post_images=post_images,
                post_type=post_data['post_type'],
                success=True,
                error_message=None
            )
            
        except Exception as e:
            print(f"Error during post generation: {e}")
            post_result = InstagramPostResult(
                title=title,
                concept=state["current_idea"],
                post_images=[],
                post_type="single",
                success=False,
                error_message=str(e)
            )
        
        # Add to state
        state["instagram_posts"].append(post_result)
        
        # Save post metadata
        post_index = len(state["instagram_posts"])
        post_meta_path = f"{state['campaign_folder']}/metadata/post_{post_index}.json"
        with open(post_meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "title": post_result.title,
                "concept": post_result.concept,
                "post_type": post_result.post_type,
                "images": post_result.post_images,
                "success": post_result.success,
                "error": post_result.error_message
            }, f, indent=2)
        
        if post_result.success:
            print(f"Instagram post generated successfully!")
            print(f"Created {len(post_result.post_images)} images ({post_result.post_type})")
            print(f"Files saved in: {posts_folder}")
            print(f"Metadata saved to: {post_meta_path}")
        else:
            print(f"Post generation had issues: {post_result.error_message}")
        
        return state
    
    def generate_storyboard_node(state: InstagramState) -> InstagramState:
        """Generate storyboard for reel content"""
        if state.get("content_type") != "reel":
            return state
        
        print("\nGenerating storyboard...")
        
        # Get models
        llm = get_llm_local()
        image_model = get_image_model()
        
        # Determine title
        if state.get("user_choice") == "series":
            current_day = state.get("current_day", 1)
            brand_name = state['brand_dna'].brand_name if state.get('brand_dna') else "Brand"
            title = f"{brand_name} - Day {current_day}"
        else:
            brand_name = state['brand_dna'].brand_name if state.get('brand_dna') else "Brand"
            title = f"{brand_name} - Reel"
        
        # Set output folder
        storyboard_folder = f"{state['campaign_folder']}/storyboards"
        
        try:
            # Step 1: Generate storyboard structure
            print("  Creating storyboard structure...")
            storyboard_data = _generate_storyboard_prompts(llm, title, state["current_idea"])
            
            # Step 2: Generate images or placeholders
            if image_model:
                print("  Generating storyboard images...")
                scene_images = _generate_storyboard_images(image_model, storyboard_data, title, storyboard_folder)
            else:
                print("  Creating text placeholders (image model unavailable)...")
                scene_images = []
                for i, prompt in enumerate(storyboard_data.scenePrompts, start=1):
                    dialogue = storyboard_data.dialogue[i-1] if i-1 < len(storyboard_data.dialogue) else ""
                    placeholder = _create_text_placeholder(prompt, i, title, storyboard_folder, dialogue)
                    scene_images.append(placeholder)
            
            # Create result
            sb_result = StoryboardResult(
                title=title,
                script=state["current_idea"],
                sceneimage=scene_images,
                storyBoard=storyboard_data,
                success=True,
                error_message=None
            )
            
        except Exception as e:
            print(f"Error during storyboard generation: {e}")
            sb_result = StoryboardResult(
                title=title,
                script=state["current_idea"],
                sceneimage=[],
                storyBoard=None,
                success=False,
                error_message=str(e)
            )
        
        # Add to state
        state["storyboards"].append(sb_result)
        
        # Save storyboard metadata
        sb_index = len(state["storyboards"])
        sb_meta_path = f"{state['campaign_folder']}/metadata/storyboard_{sb_index}.json"
        with open(sb_meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "title": sb_result.title,
                "script": sb_result.script,
                "scenes": sb_result.storyBoard.dict() if sb_result.storyBoard else {},
                "images": sb_result.sceneimage,
                "success": sb_result.success,
                "error": sb_result.error_message
            }, f, indent=2)
        
        if sb_result.success:
            print(f"Storyboard generated successfully!")
            print(f"Created {len(sb_result.sceneimage)} scenes")
            print(f"Files saved in: {storyboard_folder}")
            print(f"Metadata saved to: {sb_meta_path}")
        else:
            print(f"Storyboard generation had issues: {sb_result.error_message}")
        
        return state
    
    def generate_caption_node(state: InstagramState) -> InstagramState:
        """Generate engaging caption with hashtags"""
        llm = get_llm_local()
        
        # Get context
        content_type = state.get("content_type", "reel")
        is_series = state.get("user_choice") == "series"
        current_day = state.get("current_day", 1) if is_series else None
        total_days = state.get("days_series", 1) if is_series else None
        
        series_context = f"Series Day {current_day} of {total_days}" if is_series else "Single Post"
        
        prompt_template = """You are an expert Instagram caption writer specializing in high-engagement content.
        
        Brand DNA: {brand_dna}
        Content Type: {content_type}
        Campaign Type: {series_context}
        Content Idea/Script: {idea}
        
        Create an engaging Instagram caption with:
        
        1. HOOK (First Line): Attention-grabbing opener that stops scrolling
        2. VALUE BODY: 2-3 paragraphs delivering clear value/story
           - Use line breaks for readability
           - Include relevant emojis naturally
           - Tell a story or provide actionable insights
        3. CALL TO ACTION: Clear, specific action request
        4. HASHTAG STRATEGY: 12-15 hashtags mixing:
           - 3-4 broad/popular tags (100K+ posts)
           - 4-5 medium tags (10K-100K posts) 
           - 4-5 niche tags (1K-10K posts)
           - 2-3 branded/unique tags
        
        Make it:
        - Authentic and conversational
        - Value-driven and shareable
        - Optimized for engagement
        - Aligned with brand voice
        - Save-worthy content
        
        Format with proper spacing and emoji placement."""
        
        prompt = PromptTemplate(
            template=prompt_template,
            input_variables=["brand_dna", "content_type", "series_context", "idea"]
        )
        
        message = prompt.format(
            brand_dna=json.dumps(state["brand_dna"].dict()),
            content_type=content_type,
            series_context=series_context,
            idea=state["current_idea"]
        )
        
        response = llm.invoke(message)
        caption = response.content.strip()
        
        state["captions"].append(caption)
        
        # Save caption
        caption_index = len(state["captions"])
        caption_path = save_caption(caption, state["campaign_folder"], caption_index)
        
        print(f"\nCaption generated and saved!")
        print(f"Saved to: {caption_path}")
        print("\n" + "="*60)
        print("CAPTION PREVIEW:")
        print("="*60)
        print(caption)
        print("="*60)
        
        return state
    
    def check_series_completion(state: InstagramState) -> InstagramState:
        """Check if all days in series are complete"""
        current_day = state.get("current_day", 1)
        total_days = state.get("days_series", 1)
        
        if current_day < total_days:
            state["current_day"] = current_day + 1
            print(f"\nMoving to Day {state['current_day']} of {total_days}")
            print("="*50)
        
        return state
    
    def save_final_output(state: InstagramState) -> InstagramState:
        """Save all campaign outputs and create summary"""
        print("\nSaving final campaign outputs...")
        
        # Save complete metadata
        save_campaign_metadata(state)
        
        # Create comprehensive summary report
        summary_path = f"{state['campaign_folder']}/CAMPAIGN_SUMMARY.md"
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(f"# Instagram Campaign Summary\n\n")
            
            # Safely get brand name
            brand_name = "Unknown Brand"
            if state.get('brand_dna') and hasattr(state['brand_dna'], 'brand_name'):
                brand_name = state['brand_dna'].brand_name
            
            f.write(f"**Brand:** {brand_name}\n")
            
            # Safely handle campaign type and content type with proper fallbacks
            campaign_type = state.get('user_choice') or 'unknown'
            content_type = state.get('content_type') or 'unknown'
            
            f.write(f"**Campaign Type:** {campaign_type.capitalize()}\n")
            f.write(f"**Content Type:** {content_type.capitalize()}\n")
            f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            # Brand DNA section - with safety checks
            if state.get('brand_dna'):
                f.write(f"## Brand DNA\n")
                f.write(f"**Description:** {getattr(state['brand_dna'], 'brand_description', 'N/A')}\n")
                f.write(f"**Values:** {', '.join(getattr(state['brand_dna'], 'brand_values', []))}\n")
                f.write(f"**Target Audience:** {', '.join(getattr(state['brand_dna'], 'target_audience', []))}\n")
                f.write(f"**Instagram Expectations:** {', '.join(getattr(state['brand_dna'], 'instagram_expectations', []))}\n\n")
            
            if campaign_type == "series":
                f.write(f"## Series Details\n")
                f.write(f"**Total Days:** {state.get('days_series', 0)}\n\n")
                
                ideas = state.get("ideas", [])
                captions = state.get("captions", [])
                
                for i in range(max(len(ideas), len(captions))):
                    f.write(f"### Day {i + 1}\n")
                    
                    if i < len(ideas):
                        idea = ideas[i][:200] + "..." if len(ideas[i]) > 200 else ideas[i]
                        f.write(f"**Idea:**\n{idea}\n\n")
                    
                    if i < len(captions):
                        caption = captions[i][:200] + "..." if len(captions[i]) > 200 else captions[i]
                        f.write(f"**Caption Preview:**\n{caption}\n\n")
                    
                    # Add content-specific info
                    if content_type == "reel":
                        storyboards = state.get("storyboards", [])
                        if i < len(storyboards):
                            sb = storyboards[i]
                            if sb.success and sb.storyBoard:
                                f.write(f"**Scenes:** {sb.storyBoard.numOfScenes}\n")
                                f.write(f"**Images Generated:** {len(sb.sceneimage)}\n")
                    else:
                        instagram_posts = state.get("instagram_posts", [])
                        if i < len(instagram_posts):
                            post = instagram_posts[i]
                            if post.success:
                                f.write(f"**Post Type:** {post.post_type.capitalize()}\n")
                                f.write(f"**Images Generated:** {len(post.post_images)}\n")
                    
                    f.write("---\n\n")
            else:
                f.write(f"## Single Content\n")
                ideas = state.get("ideas", [])
                captions = state.get("captions", [])
                
                if ideas:
                    f.write(f"**Idea:**\n{ideas[0]}\n\n")
                if captions:
                    f.write(f"**Caption:**\n{captions[0]}\n\n")
                
                # Add content-specific info for single content
                if content_type == "reel":
                    storyboards = state.get("storyboards", [])
                    if storyboards:
                        sb = storyboards[0]
                        if sb.success and sb.storyBoard:
                            f.write(f"**Storyboard Scenes:** {sb.storyBoard.numOfScenes}\n")
                            f.write(f"**Images Generated:** {len(sb.sceneimage)}\n\n")
                else:
                    instagram_posts = state.get("instagram_posts", [])
                    if instagram_posts:
                        post = instagram_posts[0]
                        if post.success:
                            f.write(f"**Post Type:** {post.post_type.capitalize()}\n")
                            f.write(f"**Images Generated:** {len(post.post_images)}\n\n")
            
            # File structure section
            f.write(f"## Generated Files\n\n")
            f.write(f"```\n")
            f.write(f"{state['campaign_folder']}/\n")
            if content_type == "reel":
                f.write(f"├── storyboards/          # Scene images and storyboard files\n")
            else:
                f.write(f"├── posts/                # Post images and content\n")
            f.write(f"├── captions/             # Generated captions\n")
            f.write(f"├── metadata/             # Ideas, content data, campaign info\n")
            f.write(f"└── CAMPAIGN_SUMMARY.md   # This summary file\n")
            f.write(f"```\n\n")
            
            # Next steps
            f.write(f"## Next Steps\n\n")
            f.write(f"1. **Review Content:** Check all generated ideas and captions\n")
            if content_type == "reel":
                f.write(f"2. **Video Production:** Use storyboards to create actual video content\n")
            else:
                f.write(f"2. **Visual Review:** Review and refine generated post images\n")
            f.write(f"3. **Content Calendar:** Schedule posts for optimal engagement\n")
            f.write(f"4. **Track Performance:** Monitor engagement and adjust strategy\n\n")
            
            f.write(f"---\n")
            f.write(f"*Generated by Instagram Content Agent - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")
        
        print(f"Campaign Generation Complete!")
        print(f"Campaign Location: {state['campaign_folder']}")
        print(f"Summary Report: {summary_path}")
        
        # Add summary to saved files list
        state["saved_files"].append(summary_path)
        
        # Print final statistics - FIXED: Safe list operations
        total_ideas = len(state.get("ideas", []))
        total_captions = len(state.get("captions", []))
        total_storyboards = len(state.get("storyboards", []))
        total_posts = len(state.get("instagram_posts", []))
        
        print(f"\nCampaign Statistics:")
        print(f"   Ideas Generated: {total_ideas}")
        print(f"   Captions Created: {total_captions}")
        if content_type == "reel":
            print(f"   Storyboards Made: {total_storyboards}")
        else:
            print(f"   Posts Created: {total_posts}")
        
        return state
    
    # ------------------ ROUTING FUNCTIONS ------------------
    def route_after_user_choice(state: InstagramState) -> str:
        """Route based on series or single choice"""
        if state.get("user_choice") == "series":
            return "get_series_days"
        else:
            return "choose_content_type"
    
    def route_after_content_type(state: InstagramState) -> str:
        """Route based on content type for series vs single"""
        # Both series and single go to idea generation after content type selection
        if state.get("user_choice") == "series":
            return "generate_series_idea"
        else:
            return "generate_single_idea"
    
    def route_after_accept(state: InstagramState) -> str:
        """Route after accepting/modifying idea"""
        ideas = state.get("ideas", [])
        current_idea = state.get("current_idea")
        
        # Check if idea was accepted (added to ideas list)
        if current_idea and current_idea in ideas:
            # Idea was accepted, move forward based on content type
            if state.get("content_type") == "reel":
                return "generate_storyboard"
            else:
                return "generate_instagram_post"
        else:
            # Idea was rejected, regenerate based on series vs single
            if state.get("user_choice") == "series":
                return "generate_series_idea"
            else:
                return "generate_single_idea"
    
    def route_after_content_generation(state: InstagramState) -> str:
        """Route after storyboard or post generation"""
        # Both storyboard and post generation lead to caption
        return "generate_caption"
    
    def route_after_caption(state: InstagramState) -> str:
        """Route after caption generation"""
        if state.get("user_choice") == "series":
            current_day = state.get("current_day", 1)
            total_days = state.get("days_series", 1)
            
            if current_day < total_days:
                # More days to process
                return "check_series_completion"
            else:
                # Series complete
                return "save_final_output"
        else:
            # Single post complete
            return "save_final_output"
    
    def route_after_series_check(state: InstagramState) -> str:
        """Route after checking series completion"""
        current_day = state.get("current_day", 1)
        total_days = state.get("days_series", 1)
        
        if current_day <= total_days:
            # Continue with next day
            return "generate_series_idea"
        else:
            # Series complete
            return "save_final_output"
    
    # ------------------ BUILD WORKFLOW GRAPH ------------------
    workflow = StateGraph(InstagramState)
    
    # Add all nodes
    workflow.add_node("collect_brand_dna", collect_brand_dna)
    workflow.add_node("choose_series_or_single", choose_series_or_single)
    workflow.add_node("get_series_days", get_series_days)
    workflow.add_node("choose_content_type", choose_content_type)
    workflow.add_node("generate_series_idea", generate_series_idea)
    workflow.add_node("generate_single_idea", generate_single_idea)
    workflow.add_node("accept_or_modify_idea", accept_or_modify_idea)
    workflow.add_node("generate_storyboard", generate_storyboard_node)
    workflow.add_node("generate_instagram_post", generate_instagram_post_node)
    workflow.add_node("generate_caption", generate_caption_node)
    workflow.add_node("check_series_completion", check_series_completion)
    workflow.add_node("save_final_output", save_final_output)
    
    # Add edges following the corrected flow
    workflow.add_edge("collect_brand_dna", "choose_series_or_single")
    
    # Route after choosing series vs single
    workflow.add_conditional_edges(
        "choose_series_or_single",
        route_after_user_choice,
        {
            "get_series_days": "get_series_days",
            "choose_content_type": "choose_content_type"
        }
    )
    
    # Series path: get_series_days -> choose_content_type
    workflow.add_edge("get_series_days", "choose_content_type")
    
    # After content type selection, route to appropriate idea generation
    workflow.add_conditional_edges(
        "choose_content_type",
        route_after_content_type,
        {
            "generate_series_idea": "generate_series_idea",
            "generate_single_idea": "generate_single_idea"
        }
    )
    
    # Both idea generation paths lead to accept/modify
    workflow.add_edge("generate_series_idea", "accept_or_modify_idea")
    workflow.add_edge("generate_single_idea", "accept_or_modify_idea")
    
    # Accept/modify routes to either regenerate or continue to content generation
    workflow.add_conditional_edges(
        "accept_or_modify_idea",
        route_after_accept,
        {
            "generate_series_idea": "generate_series_idea",
            "generate_single_idea": "generate_single_idea",
            "generate_storyboard": "generate_storyboard",
            "generate_instagram_post": "generate_instagram_post"
        }
    )
    
    # Both content generation paths lead to caption
    workflow.add_edge("generate_storyboard", "generate_caption")
    workflow.add_edge("generate_instagram_post", "generate_caption")
    
    # Caption routing: either check series completion or finish
    workflow.add_conditional_edges(
        "generate_caption",
        route_after_caption,
        {
            "check_series_completion": "check_series_completion",
            "save_final_output": "save_final_output"
        }
    )
    
    # Series completion check: either continue or finish
    workflow.add_conditional_edges(
        "check_series_completion",
        route_after_series_check,
        {
            "generate_series_idea": "generate_series_idea",
            "save_final_output": "save_final_output"
        }
    )
    
    # Set entry and finish points
    workflow.set_entry_point("collect_brand_dna")
    workflow.set_finish_point("save_final_output")
    
    # Compile and run the workflow
    app = workflow.compile(checkpointer=memory_saver)
    
    print("\nStarting Instagram Content Agent")
    print("=" * 60)
    print("Following your exact workflow for content generation...")
    print("=" * 60)
    
    # Initialize state with proper empty lists
    initial_state = initialize_state()
    
    try:
        config = {"configurable": {"thread_id": "instagram_campaign"}}
        result = app.invoke(initial_state, config)
        
        print("\n" + "="*60)
        print("CAMPAIGN GENERATION COMPLETE!")
        print("="*60)
        print(f"All files saved in: {result.get('campaign_folder', 'campaigns/')}")
        print("="*60)
        
        return result
        
    except Exception as e:
        print(f"\nError during workflow execution: {e}")
        import traceback
        traceback.print_exc()
        return None

# Entry point
if __name__ == "__main__":
    result = run_instagram_agent()
    if result:
        print("\nAgent execution completed successfully!")
    else:
        print("\nAgent execution failed. Please check the errors above.")