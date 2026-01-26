import os
import time
import random
import requests
import wikipedia
import sqlite3  # Changed from libsql_client
import asyncio
import re
from groq import Groq

# --- CONFIGURATION ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GH_TOKEN = os.getenv("GH_TOKEN") 
REPO_OWNER = "yashawanthbg2001"
REPO_NAME = "knownow"
DB_PATH = "content.db" # The SQLite file in your repo

TECH_NICHES = [
    "Quantum Computing Breakthroughs", "Consumer Drone Regulations 2026", 
    "Open Source LLMs", "NVIDIA RTX 50-Series Rumors", "Apple Reality Pro Apps",
    "Sustainable Green Tech", "Foldable Phone Durability", "Cybersecurity for Remote Work",
    "SpaceX Starship Progress", "Solid State Battery Tech", "Smart Home Matter Devices",
    "Web3 Browser Security", "Mobile Photography Tips", "Linux Gaming on Steam Deck",
    "AI-Powered Coding Tools", "Electric Vertical Take-off (eVTOL)", "Micro-LED Displays"
]

ai = Groq(api_key=GROQ_API_KEY)

def init_db():
    """Initializes the local SQLite database if it doesn't exist"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            content TEXT,
            source_url TEXT,
            slug TEXT UNIQUE,
            image_url TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn

def create_slug(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    unique_id = int(time.time())
    return f"{text.strip('-')}-{unique_id}"

async def generate_with_validation(topic, data):
    model_name = "llama-3.3-70b-versatile" 
    prompt = f"""
    Write a 1200-word expert technical guide on '{topic}'. 
    Use this factual data as a base: {data}
    Requirements: Professional Tech Journalist style, Clean HTML (h2, h3, p, ul, li), 1-sentence TL;DR.
    """
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
    print(f"Targeting niche: {niche}")

    # Initialize SQLite Connection
    conn = init_db()
    cursor = conn.cursor()

    try:
        search_results = wikipedia.search(niche)
        if not search_results:
            print("No Wikipedia results found.")
            return
            
        try:
            page = wikipedia.page(search_results[0], auto_suggest=False)
        except wikipedia.DisambiguationError as e:
            page = wikipedia.page(e.options[0], auto_suggest=False)
        except wikipedia.PageError:
            print(f"Skipping: Page not found for {search_results[0]}")
            return
        
        slug = create_slug(page.title)
        seed = random.randint(0, 999999)
        image_url = f"https://image.pollinations.ai/prompt/professional_tech_photography_of_{page.title.replace(' ', '_')}_high_resolution_8k?width=1280&height=720&nologo=true&seed={seed}"

        content = await generate_with_validation(page.title, page.summary[:1500])
        
        if content:
            # 6. Save to LOCAL SQLite
            cursor.execute(
                "INSERT INTO articles (title, content, source_url, slug, image_url) VALUES (?, ?, ?, ?, ?)", 
                (page.title, content, page.url, slug, image_url)
            )
            conn.commit()
            print(f"✅ Saved to SQLite local file: {page.title}")

            # 7. Trigger GitHub Action (Only needed if running locally)
            if GH_TOKEN:
                dispatch_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/dispatches"
                headers = {
                    "Authorization": f"token {GH_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                }
                data = {"event_type": "automation-trigger"}
                response = requests.post(dispatch_url, headers=headers, json=data)
                if response.status_code == 204:
                    print(f"🚀 SUCCESS: GitHub build triggered.")
                
    except Exception as e:
        print(f"❌ Workflow Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    asyncio.run(main())