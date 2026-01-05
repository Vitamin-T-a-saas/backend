import vertexai
from google.oauth2 import service_account
from vertexai.preview.vision_models import ImageGenerationModel

KEY_PATH = r"C:\Users\arnav\OneDrive\Desktop\Smb\backend\config\vertex-key.json"

# Load service-account credentials
base_credentials = service_account.Credentials.from_service_account_file(KEY_PATH)

# Attach billing / quota project here 👇
credentials = base_credentials.with_quota_project("spry-truck-482408-g5")

vertexai.init(
    project="spry-truck-482408-g5",
    location="us-central1",
    credentials=credentials,
)

model = ImageGenerationModel.from_pretrained("imagegeneration@006")

images = model.generate_images( 
    prompt="Cinematic futuristic humanoid portrait, ultra detailed, dramatic lighting",
    number_of_images=1,
)

images[0].save("imagen.png")
print("Image generated -> imagen.png")
