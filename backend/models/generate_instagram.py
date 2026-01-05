from typing import TypedDict, List, Optional, Literal
import os, json
from datetime import datetime
from pathlib import Path

# ============= MODULE-LEVEL IMPORTS =============
try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import InMemorySaver
    from langchain_core.prompts import PromptTemplate
    from pydantic import BaseModel, Field
    LANGGRAPH_AVAILABLE = True
except ImportError as e:
    LANGGRAPH_AVAILABLE = False
    print(f"Warning: LangGraph not available: {e}")

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

try:
    import vertexai
    from google.oauth2 import service_account
    from vertexai.preview.vision_models import ImageGenerationModel
    VERTEX_AVAILABLE = True
except ImportError:
    VERTEX_AVAILABLE = False

# ============= MODULE-LEVEL CLASSES =============
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

class InstagramPostResult(BaseModel):
    title: str = Field(description="Title of the post")
    concept: str = Field(description="Post concept/idea")
    post_images: List[str] = Field(default=[], description="List of file paths for generated post images")
    post_type: str = Field(description="single or carousel")
    success: bool = Field(default=True, description="Whether generation was successful")
    error_message: Optional[str] = Field(default=None, description="Error message if failed")

class BrandDna(BaseModel):
    brand_name: str
    brand_description: str
    brand_values: List[str]
    target_audience: List[str]
    instagram_expectations: List[str]

# ============= EXPORTED FUNCTIONS FOR APP.PY =============

# Global cache for image model
_image_model_cache = None

def get_llm_local():
    """Get LLM instance - exported for app.py"""
    if not GEMINI_AVAILABLE:
        return None
    try:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            print("Warning: GOOGLE_API_KEY not set")
            return None
        return ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0.7,
            convert_system_message_to_human=True,
            api_key=api_key
        )
    except Exception as e:
        print(f"Error initializing LLM: {e}")
        return None

def get_image_model():
    """Get Vertex AI image model - exported for app.py"""
    global _image_model_cache
    if _image_model_cache is not None:
        return _image_model_cache
    
    if not VERTEX_AVAILABLE:
        print("Warning: Vertex AI not available")
        return None
    
    try:
        KEY_PATH = r"C:\Users\arnav\OneDrive\Desktop\Smb\backend\config\vertex-key.json"
        PROJECT = "spry-truck-482408-g5"
        LOCATION = "us-central1"
        
        if not os.path.exists(KEY_PATH):
            print(f"Warning: Vertex key file not found at {KEY_PATH}")
            return None
        
        base_credentials = service_account.Credentials.from_service_account_file(KEY_PATH)
        credentials = base_credentials.with_quota_project(PROJECT)
        
        vertexai.init(project=PROJECT, location=LOCATION, credentials=credentials)
        _image_model_cache = ImageGenerationModel.from_pretrained("imagegeneration@006")
        print("Vertex AI image model initialized successfully")
        return _image_model_cache
    except Exception as e:
        print(f"Error initializing Vertex AI image model: {e}")
        import traceback
        traceback.print_exc()
        return None

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

def _generate_storyboard_prompts(llm, title: str, script: str) -> StoryBoardPrompt:
    """Generate storyboard prompts using LLM - exported for app.py"""
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

def _generate_storyboard_images(image_model, storyboard_data: StoryBoardPrompt, title: str, output_folder: str) -> List[str]:
    """Generate images for storyboard scenes - exported for app.py"""
    os.makedirs(output_folder, exist_ok=True)
    scene_images = []
    
    if image_model is None:
        print("Warning: Image model not available, creating placeholders")
        for i, prompt in enumerate(storyboard_data.scenePrompts, start=1):
            dialogue = storyboard_data.dialogue[i-1] if i-1 < len(storyboard_data.dialogue) else ""
            placeholder = _create_text_placeholder(prompt, i, title, output_folder, dialogue)
            scene_images.append(placeholder)
        return scene_images
    
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
            import traceback
            traceback.print_exc()
            dialogue = storyboard_data.dialogue[i-1] if i-1 < len(storyboard_data.dialogue) else ""
            placeholder = _create_text_placeholder(prompt, i, title, output_folder, dialogue)
            scene_images.append(placeholder)
            
    return scene_images

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

def _generate_instagram_post_prompts(llm, title: str, concept: str) -> dict:
    """Generate Instagram post image prompts using LLM - exported for app.py"""
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

def _generate_instagram_post_images(image_model, post_data: dict, title: str, output_folder: str) -> List[str]:
    """Generate images for Instagram posts - exported for app.py"""
    os.makedirs(output_folder, exist_ok=True)
    post_images = []
    
    if image_model is None:
        print("Warning: Image model not available, creating placeholders")
        for i, prompt in enumerate(post_data['image_prompts'], start=1):
            description = post_data['image_descriptions'][i-1] if i-1 < len(post_data['image_descriptions']) else ""
            placeholder = _create_post_text_placeholder(prompt, i, title, output_folder, description)
            post_images.append(placeholder)
        return post_images
    
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
            import traceback
            traceback.print_exc()
            description = post_data['image_descriptions'][i-1] if i-1 < len(post_data['image_descriptions']) else ""
            placeholder = _create_post_text_placeholder(prompt, i, title, output_folder, description)
            post_images.append(placeholder)
            
    return post_images

# ============= BACKWARD COMPATIBILITY - run_instagram_agent =============
def run_instagram_agent(state=None, action="generate_idea"):
    """Main function to run the Instagram agent with API-compatible signature"""
    # This is kept for backward compatibility
    # The actual work is done by the exported functions above
    return {"success": True, "message": "Instagram agent executed", "action": action, "state": state}
