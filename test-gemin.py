"""
Chowder.ng - Gemini API key tester
Run: python test_gemini.py
"""

import os
from dotenv import load_dotenv
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyCCv7FIzYH-QiPzLoz0Mlf4q0pwss97E4M")

print(f"✅ Key: {GEMINI_API_KEY[:8]}...{GEMINI_API_KEY[-4:]}\n")

# Models confirmed available from ListModels
MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite-001",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-pro-latest",
    "gemini-2.5-flash-lite",
]

try:
    from google import genai
    client = genai.Client(api_key=GEMINI_API_KEY)
    print("Using google-genai SDK\n")

    print("── Testing each model ─────────────────────────────────")
    for model in MODELS:
        try:
            response = client.models.generate_content(model=model, contents="Say: OK")
            print(f"  ✅ {model} → {response.text.strip()}")
        except Exception as e:
            short = str(e).split('\n')[0][:100]
            print(f"  ❌ {model} → {short}")

except ImportError:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    print("Using google-generativeai SDK (deprecated)\n")

    print("── Testing each model ─────────────────────────────────")
    for model in MODELS:
        try:
            m = genai.GenerativeModel(model)
            r = m.generate_content("Say: OK")
            print(f"  ✅ {model} → {r.text.strip()}")
        except Exception as e:
            short = str(e).split('\n')[0][:100]
            print(f"  ❌ {model} → {short}")