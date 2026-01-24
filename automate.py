import os, time, random, requests, wikipedia, libsql_client
from groq import Groq

# --- CONFIGURATION ---
TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DEPLOY_HOOK = os.getenv("CLOUDFLARE_DEPLOY_HOOK")

# BROAD TECH NICHES (Broad & Specific Mix)
TECH_NICHES = [
    "Next-gen Consumer Electronics", "AI Hardware & LPUs", "Privacy-focused Mobile Apps",
    "Open Source Dev Tools", "Cloud Computing Trends 2026", "EV Battery Breakthroughs",
    "Smart Home Protocols (Matter/Thread)", "Cybersecurity for Startups", 
    "Space-Tech & Satellites", "Biotech Gadgets", "AR/VR Workplace Solutions"
]

db = libsql_client.create_client(url=TURSO_URL, auth_token=TURSO_TOKEN)
ai = Groq(api_key=GROQ_API_KEY)

def generate_with_validation(topic, data):
    # Step 1: Draft with "Information Gain" focus
    draft_prompt = f"""
    Write a 1200-word expert technical guide on {topic}. 
    Context: {data}
    Target: Senior Tech Professionals.
    Style: Use varied sentence lengths (burstiness) to maintain a human rhythm. 
    Avoid 'robotic' transitions like 'In conclusion'. Use conversational technical insights.
    Include: Structured Data (FAQ, Pros/Cons), and 1-sentence 'TL;DR' at the top for AI Snippets.
    Format: Clean HTML.
    """
    draft = ai.chat.completions.create(model="llama3-70b-8192", messages=[{"role": "user", "content": draft_prompt}]).choices[0].message.content

    # Step 2: Validation/Fact-Check Step
    val_prompt = f"Act as a senior tech fact-checker. Review this article for hallucinations or vague claims: {draft}. Rewrite any section that sounds like generic AI filler to be more specific and data-driven. Return only the final HTML."
    final_html = ai.chat.completions.create(model="llama3-70b-8192", messages=[{"role": "user", "content": val_prompt}]).choices[0].message.content
    return final_html

def main():
    # JITTER: Random 10-40 min delay to break "bot" patterns
    delay = random.randint(600, 2400)
    print(f"Jitter enabled: Sleeping for {delay//60} minutes...")
    time.sleep(delay)

    niche = random.choice(TECH_NICHES)
    try:
        search = wikipedia.search(niche)[0]
        page = wikipedia.page(search)
        content = generate_with_validation(page.title, page.summary[:1500])
        
        db.execute("INSERT INTO articles (title, content, source_url) VALUES (?, ?, ?)", 
                   (page.title, content, page.url))
        
        if DEPLOY_HOOK: requests.post(DEPLOY_HOOK)
        print(f"Successfully published: {page.title}")
    except Exception as e:
        print(f"Skipping run due to error: {e}")

if __name__ == "__main__":
    main()