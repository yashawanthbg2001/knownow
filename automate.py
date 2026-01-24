import os, time, random, requests, wikipedia, libsql_client, asyncio
from groq import Groq

# --- CONFIGURATION ---
TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DEPLOY_HOOK = os.getenv("CLOUDFLARE_DEPLOY_HOOK")

# BROAD TECH NICHES
TECH_NICHES = [
    "Next-gen Consumer Electronics", "AI Hardware & LPUs", "Privacy-focused Mobile Apps",
    "Open Source Dev Tools", "Cloud Computing Trends 2026", "EV Battery Breakthroughs",
    "Smart Home Protocols", "Cybersecurity", "Space-Tech", "Biotech Gadgets"
]

ai = Groq(api_key=GROQ_API_KEY)

async def generate_with_validation(topic, data):
    draft_prompt = f"Write a 1200-word expert technical guide on {topic}. Context: {data}. Format: Clean HTML."
    draft = ai.chat.completions.create(model="llama3-70b-8192", messages=[{"role": "user", "content": draft_prompt}]).choices[0].message.content
    return draft # Simplified for debugging

async def main():
    # JITTER: 10-40 min delay
    delay = random.randint(600, 2400)
    print(f"Jitter: Sleeping for {delay//60} minutes...")
    await asyncio.sleep(delay)

    # Initialize Async Client
    async with libsql_client.create_client(url=TURSO_URL, auth_token=TURSO_TOKEN) as db:
        niche = random.choice(TECH_NICHES)
        try:
            search = wikipedia.search(niche)[0]
            page = wikipedia.page(search)
            content = await generate_with_validation(page.title, page.summary[:1500])
            
            await db.execute("INSERT INTO articles (title, content, source_url) VALUES (?, ?, ?)", 
                       (page.title, content, page.url))
            
            if DEPLOY_HOOK: 
                requests.post(DEPLOY_HOOK)
            print(f"Successfully published: {page.title}")
        except Exception as e:
            print(f"Skipping run due to error: {e}")

if __name__ == "__main__":
    asyncio.run(main())