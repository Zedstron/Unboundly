from dotenv import load_dotenv
from app.services.bridges.providers.instagram import InstagramBridge

load_dotenv()
bridge = InstagramBridge()

input("Enter to Exit")