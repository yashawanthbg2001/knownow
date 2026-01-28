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

# --- 1. DATABASE & QUEUE HELPERS ---

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

# --- 2. RESEARCH & IMAGE TOOLS (ENHANCED SCRAPER) ---

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
    """
    Enhanced scraper to get raw official data. 
    Uses real browser headers to avoid blocks.
    """
    if not url or "wikipedia" in url: return ""
    try:
        # Use headers that mimic a real user browser
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status() # Ensure we got a 200 OK
        
        soup = BeautifulSoup(res.text, "html.parser")
        
        # Strip away junk so AI gets only high-value text
        for tag in soup(["nav", "footer", "script", "style", "header", "aside", "form"]): 
            tag.decompose()
            
        # Try to find the main content block first
        content_area = soup.find("main") or soup.find("article") or soup.find("div", {"id": "content"}) or soup.body
        
        text = re.sub(r"\s+", " ", content_area.get_text())
        return text[:5000] # Return enough data for a deep dive
    except Exception as e:
        print(f"⚠️ Scraper failed for {url}: {e}")
        return ""

def find_links_on_wiki(wiki_page):
    official = ""
    try:
        # We look specifically for the 'official' or 'specs' link in Wikipedia references
        for link in wiki_page.references:
            if any(x in link.lower() for x in ["official", "specs", "product", "github", "documentation"]):
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

# --- 3. THE AI CONTENT ARCHITECT ---

async def generate_deep_dive(topic, wiki, official, category, tier):
    # Determine the context mix
    official_context = f"OFFICIAL SITE DATA (High Accuracy): {official}" if official else "No official site data found."
    
    prompt = f"""
    Write a Tier {tier} Technical Review for '{topic}' as of {datetime.now().strftime('%B %Y')}. 
    Category: {category}. Language: English.

    SOURCES PROVIDED:
    - Wikipedia: {wiki[:1000]}
    - {official_context}

    INSTRUCTIONS:
    1. If Official Data is provided, prioritize its technical specifications over Wikipedia.
    2. Format as clean HTML with a <script type="application/ld+json"> schema block for Product and FAQ.
    3. Include a <div class="verdict-box">, <h2>Real-World Experience</h2>, <table class="spec-table">, and <details class="faq-item">.
    4. Mention 2 specific weaknesses or trade-offs.
    5. Ensure the specs reflect the latest 2026 standards.
    """
    try:
        chat = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are a senior hardware critic. Merge provided Wikipedia and Official Site data into a high-authority review. Output ONLY clean HTML."},
                {"role": "user", "content": prompt}
            ],
        )
        return chat.choices[0].message.content
    except: return None

# --- 4. LOGIC & CATEGORIZATION ---

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
            
            # --- SCRAPER LOGIC: Get Official Data ---
            off_url = find_links_on_wiki(page)
            if off_url:
                print(f"🔗 Official site found: {off_url}. Scraping...")
                off_data = scrape_official_site(off_url)
            else:
                print("ℹ️ No official site link found in Wikipedia references.")
                off_data = ""
            
            # --- AI GENERATION: Use both Wiki and Official ---
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
                        (page.title, content, slug, image_url, category, tier, off_url or page.url, len(content.split()), status_flag)
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