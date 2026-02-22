import os
import json
from dotenv import load_dotenv
from breeze_connect import BreezeConnect

# Setup paths
project_root = r"C:\rajat\Algo_System"
env_path = os.path.join(project_root, "config", ".env")
session_path = os.path.join(project_root, "data", "breeze_session.json")

# 1. Load Data
load_dotenv(env_path)
api_key = os.getenv("BREEZE_API_KEY")
secret_key = os.getenv("BREEZE_SECRET_KEY")

try:
    with open(session_path, "r") as f:
        session_token = json.load(f).get("session_token")
except Exception as e:
    session_token = None
    print(f"File read error: {e}")

# 2. Print Sanity Check (Masked for safety)
print("--- AUTHENTICATION CHECK ---")
print(f"API Key loaded:    {'✅ YES' if api_key else '❌ NO'} (Length: {len(str(api_key)) if api_key else 0})")
print(f"Secret Key loaded: {'✅ YES' if secret_key else '❌ NO'} (Length: {len(str(secret_key)) if secret_key else 0})")
print(f"Session Token:     {'✅ YES' if session_token else '❌ NO'} (Length: {len(str(session_token)) if session_token else 0})")
print("----------------------------")

# 3. Attempt Connection
if api_key and secret_key and session_token:
    print("Attempting to connect to Breeze...")
    try:
        breeze = BreezeConnect(api_key=api_key)
        breeze.generate_session(api_secret=secret_key, session_token=session_token)
        print("✅ SUCCESS! The credentials are valid and the API accepted them.")
    except Exception as e:
        print(f"❌ CONNECTION FAILED: {e}")
else:
    print("❌ ABORTED: Missing one or more credentials. Check your .env or .json file paths.")