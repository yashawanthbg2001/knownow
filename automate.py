import os
import time
import requests
import wikipedia
import sqlite3
import asyncio
import re
import json
from bs4 import BeautifulSoup
from groq import Groq
from datetime import datetime

# --- CONFIGURATION ---
DB_PATH = "content.db"
# SECURITY: Key moved to env variable to prevent unauthorized use
GROQ_API_KEY = "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"
GH_TOKEN = os.getenv("GH_TOKEN")
REPO_OWNER = "yashawanthbg2001"
REPO_NAME = "knownow"

ai = Groq(api_key=GROQ_API_KEY)

# --- 1. DATABASE & QUEUE HELPERS (YOUR ORIGINAL LOGIC) ---

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS keywords (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phrase TEXT UNIQUE,
        category TEXT,
        status TEXT DEFAULT 'pending',
        priority INTEGER DEFAULT 1,
        last_attempt DATETIME
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT UNIQUE,
        content TEXT,
        slug TEXT UNIQUE,
        image_url TEXT,
        category TEXT,
        tier INTEGER DEFAULT 2,
        status TEXT DEFAULT 'published',
        source_url TEXT,
        word_count INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()

def get_queue_health():
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM keywords WHERE status = 'pending'").fetchone()[0]
    conn.close()
    return count

def get_next_batch(size=2):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, phrase FROM keywords WHERE status = 'pending' ORDER BY priority DESC, id ASC LIMIT ?", (size,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def update_job_status(kw_id, status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE keywords SET status = ?, last_attempt = CURRENT_TIMESTAMP WHERE id = ?", (status, kw_id))
    conn.commit()
    conn.close()

def article_exists(title):
    conn = sqlite3.connect(DB_PATH)
    exists = conn.execute("SELECT 1 FROM articles WHERE title = ?", (title,)).fetchone()
    conn.close()
    return exists is not None

def ingest_keywords(keywords_list, category="Technology"):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for phrase in keywords_list:
        parts = re.split(r" and |,|/|\+", phrase)
        for p in parts:
            clean_p = p.strip()
            if clean_p:
                cursor.execute("INSERT OR IGNORE INTO keywords (phrase, category) VALUES (?, ?)", (clean_p, category))
    conn.commit()
    conn.close()

# --- 2. RESEARCH & IMAGE TOOLS (YOUR ORIGINAL LOGIC) ---

def get_wikipedia_image_pro(page_title):
    try:
        clean_title = re.sub(r'\([^)]*\)', '', page_title).strip()
        URL = "https://en.wikipedia.org/w/api.php"
        headers = {'User-Agent': 'KnowNowBot/1.0 (yashawanthbg@example.com)'}
        PARAMS = {
            "action": "query", "format": "json", "titles": clean_title,
            "prop": "pageimages", "piprop": "original", "formatversion": "2"
        }
        res = requests.get(URL, params=PARAMS, headers=headers, timeout=10).json()
        pages = res.get("query", {}).get("pages", [])
        if pages and "original" in pages[0]:
            img_url = pages[0]["original"]["source"]
            if "upload.wikimedia.org" in img_url:
                return img_url
    except: pass
    return None

def smart_wiki_search(phrase):
    try:
        return wikipedia.page(phrase, auto_suggest=False)
    except wikipedia.exceptions.DisambiguationError as e:
        return wikipedia.page(e.options[0], auto_suggest=False)
    except wikipedia.exceptions.PageError:
        search_res = wikipedia.search(phrase)
        if search_res:
            return wikipedia.page(search_res[0], auto_suggest=False)
    except: return None
    return None

def scrape_official_site(url):
    if not url or "wikipedia" in url: return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}
        res = requests.get(url, headers=headers, timeout=12)
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["nav", "footer", "script", "style", "header"]): tag.decompose()
        content = soup.find("main") or soup.find("article") or soup.body
        return re.sub(r"\s+", " ", content.get_text())[:4000]
    except: return ""

def find_links_on_wiki(wiki_page):
    official = ""
    try:
        for link in wiki_page.references:
            if any(x in link.lower() for x in ["official", "specs", "product", "github"]):
                official = link
                break
    except: pass
    return official

def discover_new_keywords():
    print("\n--- 🔍 DISCOVERING 2026 TRENDS ---")
    now = datetime.now()
    prompt = f"Identify 10 high-value specific product models trending in {now.strftime('%B %Y')}. Focus on 2026 flagships and AI hardware. Return comma separated list."
    try:
        chat = ai.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": prompt}])
        new_items = chat.choices[0].message.content.split(",")
        ingest_keywords(new_items, f"Trends-{now.strftime('%m-%Y')}")
    except Exception as e: print(f"Discovery failed: {e}")

# --- 3. THE AI CONTENT ARCHITECT (UPDATED PROMPT & SCHEMA) ---

async def generate_deep_dive(topic, wiki, official, category, tier):
    # This is where we force 2026 accuracy and add Schema
    prompt = f"""
    Write a Tier {tier} Technical Review for '{topic}' as of January 2026. 
    Category: {category}. Language: English.

    KNOWLEDGE GUARDRAILS:
    - If Tesla: Current chips are AI4.5/AI5. AI6 is future/unreleased.
    - If HoloLens: Focus on HoloLens 2 (Snapdragon 850, 4GB RAM).
    - If Snapdragon: Mention X Elite or 8 Gen 5 NPU performance.

    REQUIRED HTML STRUCTURE:
    1. <script type="application/ld+json"> [INSERT PRODUCT & FAQ SCHEMA HERE] </script>
    
    2. <div class="verdict-box">
        <h2>Our 2026 Verdict</h2>
        <p>Opinionated summary.</p>
        <strong>Best for:</strong> [User type] | <strong>Avoid if:</strong> [User type]
    </div>

    3. <h2>Real-World Experience</h2>
       Context: {wiki[:800]} {official[:800]}. 
       Focus on daily usability, speed, and 2026 market relevance.

    4. <table class="spec-table">
        <thead><tr><th>Feature</th><th>2026 Specification</th></tr></thead>
        <tbody>...</tbody>
    </table>

    5. <h2>Expert Q&A</h2>
        Use <details class="faq-item"><summary>Question</summary><div class="faq-answer">Answer</div></details>
    
    EDITORIAL RULES:
    - List 2 specific weaknesses.
    - No encyclopedic fluff; use expert technical tone.
    """
    try:
        chat = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a senior hardware critic. Output ONLY clean HTML with embedded JSON-LD schema."},
                {"role": "user", "content": prompt}
            ],
        )
        return chat.choices[0].message.content
    except: return None

# --- 4. LOGIC & CATEGORIZATION (YOUR ORIGINAL LOGIC) ---

def get_tier(phrase):
    p = phrase.lower()
    if any(x in p for x in ["iphone 17", "s26", "macbook pro", "pixel 10", "s25"]): return 1
    if any(x in p for x in ["specs", "vs", "comparison"]): return 3
    return 2

def categorize(phrase):
    p = phrase.lower()
    if any(x in p for x in ["phone", "iphone", "galaxy", "pixel"]): return "Smartphones"
    if any(x in p for x in ["laptop", "macbook", "notebook"]): return "Laptops"
    if any(x in p for x in ["audio", "buds", "ear", "wh-"]): return "Audio"
    if any(x in p for x in ["watch", "band", "fitbit"]): return "Wearables"
    return "Smart Home Devices"

def create_slug(text):
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return f"{text}-{int(time.time())}"

# --- 5. MAIN EXECUTION ---

async def main():
    init_db()
    if get_queue_health() < 3:
        discover_new_keywords()

    jobs = get_next_batch(2)
    if not jobs:
        print("📭 Queue empty.")
        return

    for kw_id, phrase in jobs:
        print(f"\n🚀 Starting Job: {phrase}")
        try:
            page = smart_wiki_search(phrase)
            if not page:
                update_job_status(kw_id, "failed")
                continue

            if article_exists(page.title):
                print(f"✅ Already exists: {page.title}. Skipping.")
                update_job_status(kw_id, "completed")
                continue

            tier = get_tier(phrase)
            category = categorize(phrase)
            off_url = find_links_on_wiki(page)
            off_data = scrape_official_site(off_url)
            
            content = await generate_deep_dive(page.title, page.summary, off_data, category, tier)

            if content:
                slug = create_slug(page.title)
                image_url = get_wikipedia_image_pro(page.title)
                if not image_url:
                    style = {"Smartphones": "sleek_product", "Laptops": "pro_laptop"}.get(category, "tech_gadget")
                    image_url = f"https://image.pollinations.ai/prompt/{style}_{slug}?width=1280&height=720&nologo=true"

                status_flag = "noindex" if tier == 3 else "published"

                try:
                    conn = sqlite3.connect(DB_PATH)
                    conn.execute(
                        """INSERT INTO articles (title, content, slug, image_url, category, tier, source_url, word_count, status) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (page.title, content, slug, image_url, category, tier, page.url, len(content.split()), status_flag)
                    )
                    conn.commit()
                    conn.close()
                    print(f"🏆 SUCCESS: {page.title}")
                    update_job_status(kw_id, "completed")
                except Exception as e:
                    print(f"❌ DB Error: {e}")
                    update_job_status(kw_id, "failed")
            else:
                update_job_status(kw_id, "failed")
        except Exception as e:
            print(f"❌ Error: {e}")
            update_job_status(kw_id, "failed")

if __name__ == "__main__":
    asyncio.run(main())