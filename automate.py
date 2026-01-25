import os
import time
import random
import requests
import wikipedia
import libsql_client
import asyncio
import re
from groq import Groq

# --- CONFIGURATION ---
TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GH_TOKEN = os.getenv("GH_TOKEN") # GitHub Personal Access Token
REPO_OWNER = "yashawanthbg2001"
REPO_NAME = "knownow"

TECH_NICHES = [
    "Quantum Computing Breakthroughs", "Consumer Drone Regulations 2026", 
    "Open Source LLMs", "NVIDIA RTX 50-Series Rumors", "Apple Reality Pro Apps",
    "Sustainable Green Tech", "Foldable Phone Durability", "Cybersecurity for Remote Work",
    "SpaceX Starship Progress", "Solid State Battery Tech", "Smart Home Matter Devices",
    "Web3 Browser Security", "Mobile Photography Tips", "Linux Gaming on Steam Deck",
    "AI-Powered Coding Tools", "Electric Vertical Take-off (eVTOL)", "Micro-LED Displays"
]

ai = Groq(api_key=GROQ_API_KEY)

def create_slug(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')

async def generate_with_validation(topic, data):
    model_name = "llama-3.3-70b-versatile" 
    prompt = f"Write a 1200-word expert technical guide on '{topic}'. Base it on: {data}. Format: Clean HTML using <h2>, <h3>, <p>, <ul>, <li>. Include a TL;DR."
    
    try:
        completion = ai.chat.completions.create(
            model=model_name, 
            messages=[{"role": "system", "content": "You are a senior technical writer."},
                      {"role": "user", "content": prompt}]
        )
        return completion.choices[0].message.content
    except Exception as e:
        print(f"AI Generation Error: {e}")
        return None

async def main():
    niche = random.choice(TECH_NICHES)
    # Jitter removed for testing; add back await asyncio.sleep(random.randint(600, 2400)) for production
    
    async with libsql_client.create_client(url=TURSO_URL, auth_token=TURSO_TOKEN) as db:
        try:
            search_results = wikipedia.search(niche)
            if not search_results: return
                
            page = wikipedia.page(search_results[0])
            slug = f"{create_slug(page.title)}-{int(time.time())}"
            image_url = f"https://image.pollinations.ai/prompt/tech_photo_{page.title.replace(' ', '_')}?width=1280&height=720&seed={random.randint(0,999)}"

            content = await generate_with_validation(page.title, page.summary[:1500])
            
            if content:
                await db.execute(
                    "INSERT INTO articles (title, content, source_url, slug, image_url) VALUES (?, ?, ?, ?, ?)", 
                    [page.title, content, page.url, slug, image_url]
                )
                
                # TRIGGER GITHUB DEPLOYMENT
                dispatch_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/dispatches"
                headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
                res = requests.post(dispatch_url, headers=headers, json={"event_type": "automation-trigger"})
                
                if res.status_code == 204:
                    print(f"🚀 Success: {page.title} published and GitHub Build triggered.")
                else:
                    print(f"⚠️ Saved to DB, but GitHub trigger failed: {res.status_code}")
                    
        except Exception as e:
            print(f"❌ Workflow Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())