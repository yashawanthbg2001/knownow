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
DEPLOY_HOOK = os.getenv("CLOUDFLARE_DEPLOY_HOOK")

# BROAD TECH NICHES (Updated for 2026 Trends)
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
    """Converts 'Hello World' to 'hello-world' for SEO URLs"""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')

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
    # 1. Randomize Niche and Delay (Bot Detection Protection)
    niche = random.choice(TECH_NICHES)
    delay = random.randint(600, 2400) # 10 to 40 minutes jitter
    print(f"Jitter: Sleeping for {delay//60} minutes before targeting: {niche}")
    await asyncio.sleep(delay)

    async with libsql_client.create_client(url=TURSO_URL, auth_token=TURSO_TOKEN) as db:
        try:
            # 2. Search Wikipedia for stable factual data
       
            search_results = wikipedia.search(niche)
            if not search_results:
                print("No results found.")
                return
                
            page = wikipedia.page(search_results[0])
            slug = create_slug(page.title)
            
            img_topic = page.title.replace(" ", "%20")
            image_url = f"https://image.pollinations.ai/prompt/professional_tech_photography_of_{img_topic}_high_resolution_8k?width=1280&height=720&nologo=true"

            content = await generate_with_validation(page.title, page.summary[:1500])
            
            if content:
                # Optimized SQL execution to avoid 'result' key errors
                await db.execute(
                    "INSERT INTO articles (title, content, source_url, slug, image_url) VALUES (?, ?, ?, ?, ?)", 
                    [page.title, content, page.url, slug, image_url]
                )
                
                if DEPLOY_HOOK: 
                    requests.post(DEPLOY_HOOK)
                    print(f"🚀 Published & Rebuild Triggered: {page.title}")
                else:
                    print(f"✅ Saved to DB: {page.title}")
                    
        except Exception as e:
            print(f"❌ Workflow Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())