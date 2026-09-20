import os
import instagrapi
from dotenv import load_dotenv

load_dotenv()

username = os.getenv("INSTABRIDGE_USERNAME")
password = os.getenv("INSTABRIDGE_PASSWORD")

cl = instagrapi.Client()
cl.login(username, password, verification_code=input("Code: "))

print("SessionID", cl.sessionid)
print("MID", cl.mid)
print(cl.account_info().full_name)