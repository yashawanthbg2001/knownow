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
    # Updated to a 2026-supported model
    model_name = "llama-3.3-70b-versatile" 
    
    draft_prompt = f"Write a 1200-word expert technical guide on {topic}. Context: {data}. Format: Clean HTML."
    
    completion = ai.chat.completions.create(
        model=model_name, 
        messages=[{"role": "user", "content": draft_prompt}]
    )
    return completion.choices[0].message.content

async def main():
    # Check if we have the niche first so we don't waste time sleeping if it fails
    niche = random.choice(TECH_NICHES)
    
    # JITTER: Moving it inside the try block
    delay = random.randint(300, 600) # Shortened delay for testing; GitHub likes faster runs
    print(f"Jitter: Sleeping for {delay//60} minutes...")
    await asyncio.sleep(delay)

    async with libsql_client.create_client(url=TURSO_URL, auth_token=TURSO_TOKEN) as db:
        try:
            search_results = wikipedia.search(niche)
            if not search_results:
                print("No wikipedia results found.")
                return
                
            page = wikipedia.page(search_results[0])
            content = await generate_with_validation(page.title, page.summary[:1500])
            
            await db.execute("INSERT INTO articles (title, content, source_url) VALUES (?, ?, ?)", 
                       (page.title, content, page.url))
            
            if DEPLOY_HOOK: 
                requests.post(DEPLOY_HOOK)
            print(f"✅ Successfully published: {page.title}")
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())