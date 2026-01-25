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
# These should be set in your Environment Variables or GitHub Secrets
TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GH_TOKEN = os.getenv("GH_TOKEN") # GitHub Personal Access Token with 'repo' scope
REPO_OWNER = "yashawanthbg2001"
REPO_NAME = "knownow"

# 2026 Tech Trends for variety
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
    """Converts 'Hello World' to 'hello-world' and adds timestamp to prevent nulls/duplicates"""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    unique_id = int(time.time())
    return f"{text.strip('-')}-{unique_id}"

async def generate_with_validation(topic, data):
    """Uses Llama 3.3 70B to generate professional technical content"""
    model_name = "llama-3.3-70b-versatile" 
    
    prompt = f"""
    Write a 1200-word expert technical guide on '{topic}'. 
    Use this factual data as a base: {data}
    
    Requirements:
    - Language: English
    - Style: Professional Tech Journalist (like Wired or The Verge)
    - Format: Clean HTML using <h2>, <h3>, <p>, <ul>, and <li> tags.
    - SEO: Include a 1-sentence TL;DR at the start.
    - Accuracy: Ensure technical terms are used correctly.
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
    # 1. Randomize Niche
    niche = random.choice(TECH_NICHES)
    print(f"Targeting niche: {niche}")

    async with libsql_client.create_client(url=TURSO_URL, auth_token=TURSO_TOKEN) as db:
        try:
            # 2. Search Wikipedia for facts
            search_results = wikipedia.search(niche)
            if not search_results:
                print("No Wikipedia results found.")
                return
                
            page = wikipedia.page(search_results[0])
            
            # 3. Generate UNIQUE slug (Fixes the 'null' issue)
            slug = create_slug(page.title)
            
            # 4. Generate Image URL
            seed = random.randint(0, 999999)
            image_url = f"https://image.pollinations.ai/prompt/professional_tech_photography_of_{page.title.replace(' ', '_')}_high_resolution_8k?width=1280&height=720&nologo=true&seed={seed}"

            # 5. Generate AI Content
            content = await generate_with_validation(page.title, page.summary[:1500])
            
            if content:
                # 6. Save to Turso
                await db.execute(
                    "INSERT INTO articles (title, content, source_url, slug, image_url) VALUES (?, ?, ?, ?, ?)", 
                    [page.title, content, page.url, slug, image_url]
                )
                print(f"✅ Saved to Database: {page.title}")

                # 7. Trigger GitHub Action (The Rebuild)
                dispatch_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/dispatches"
                headers = {
                    "Authorization": f"token {GH_TOKEN}",
                    "Accept": "application/vnd.github.v3+json",
                }
                data = {"event_type": "automation-trigger"}
                
                response = requests.post(dispatch_url, headers=headers, json=data)
                
                if response.status_code == 204:
                    print(f"🚀 SUCCESS: GitHub build triggered for {page.title}")
                else:
                    print(f"❌ GitHub Trigger Failed: {response.status_code} - {response.text}")
                    
        except Exception as e:
            print(f"❌ Workflow Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())