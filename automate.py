import os
import time
import requests
import wikipedia
import sqlite3
import asyncio
import re
import hashlib
from bs4 import BeautifulSoup
from groq import Groq
from datetime import datetime

# --- CONFIGURATION ---
DB_PATH = "content.db"
# Use the key provided for your current session
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

# --- 2. MULTI-SOURCE SCRAPING & ACCURACY TOOLS ---

def create_unique_slug(title):
    """Generates a human-like slug using partial MD5 for SEO uniqueness."""
    base_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    hash_suffix = hashlib.md5(title.encode()).hexdigest()[:5]
    return f"{base_slug}-{hash_suffix}"

def validate_tech_data(content, source_text):
    """Ensures AI didn't hallucinate numbers by checking them against raw source text."""
    source_numbers = set(re.findall(r'\d+', source_text))
    content_numbers = set(re.findall(r'\d+', content))
    if not content_numbers or len(content_numbers.intersection(source_numbers)) < 2:
        return False
    return True

def get_wikipedia_image_pro(page_title):
    try:
        clean_title = re.sub(r'\([^)]*\)', '', page_title).strip()
        URL = "https://en.wikipedia.org/w/api.php"
        headers = {"User-Agent": "KnowNowBot/1.0 (yashawanthbg@example.com)"}
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
    except:
        search_res = wikipedia.search(phrase)
        if search_res:
            return wikipedia.page(search_res[0], auto_suggest=False)
    return None

def scrape_official_site(url):
    """Deep scrapes secondary sources found in Wikipedia references."""
    if not url or "wikipedia" in url: return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/121.0.0.0"}
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code != 200: return ""
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["script", "style", "footer", "nav", "header", "aside"]): tag.decompose()
        main_body = soup.find("main") or soup.find("article") or soup.body
        text = re.sub(r"\s+", " ", main_body.get_text())
        return text[:4000]
    except: return ""

def find_research_links(wiki_page):
    """Identifies potential official links for deeper scraping."""
    links = {"official": "", "github": ""}
    try:
        for link in wiki_page.references:
            if "github.com" in link: links["github"] = link
            elif any(x in link.lower() for x in ["official", "specs", "manual", "product"]):
                links["official"] = link
                break
    except: pass
    return links

# --- 3. TREND DISCOVERY & NOTIFICATION ---

def discover_new_keywords():
    print("\n--- 🔍 DISCOVERING 2026 TRENDS ---")
    now = datetime.now()
    prompt = f"Identify 10 specific trending product models (Samsung S26, Pixel 10, etc.) for {now.strftime('%B %Y')}. Return names separated by commas."
    try:
        completion = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        new_items = completion.choices[0].message.content.split(",")
        ingest_keywords(new_items, f"Trends-{now.strftime('%m-%Y')}")
        print(f"✅ Added {len(new_items)} new products to the queue.")
    except: print("❌ Discovery failed.")

def notify_sheet(topic, status, category="N/A", word_count=0, details=""):
    SHEET_URL = "https://script.google.com/macros/s/AKfycbwaNy5Ei0iDmrEmj2iVp9gZdoVcd9y0r_d7Er7pi5zvvkjvlbRl5BcQQEqDwx-7fHVb/exec"
    payload = {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "topic": topic, "status": status, "category": category, "word_count": word_count, "details": details}
    try: requests.post(SHEET_URL, json=payload, timeout=8)
    except: pass

# --- 4. AI CONTENT ARCHITECT (MULTI-SOURCE) ---

async def generate_deep_dive(topic, wiki_text, official_text, github_text):
    """Synthesizes all scraped data into a verified review."""
    prompt = f"""
    Write a 2026 Technical Review for '{topic}'.
    WIKI DATA: {wiki_text[:1000]}
    OFFICIAL DATA: {official_text[:1000]}
    GITHUB DATA: {github_text[:500]}

    REQUIRED HTML:
    1. <div class="verdict-box"><h2>Verdict</h2>2026 Buy/Avoid logic</div>
    2. <h2>Real-World Performance</h2>Synthetic vs Real usage.
    3. <table class="spec-table">Technical Specs</table>
    4. <details class="faq-item"><summary>FAQ</summary>Answer</details>

    RULES:
    - Prioritize OFFICIAL DATA over WIKI DATA if they conflict.
    - Mention 2 honest negatives.
    - No generic AI definitions.
    """
    try:
        result = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are a professional hardware researcher. Truth is everything."}, {"role": "user", "content": prompt}]
        )
        content = result.choices[0].message.content
        if validate_tech_data(content, wiki_text + official_text):
            return content
        return None
    except: return None

# --- 5. MAIN EXECUTION ---

async def main():
    init_db()
    if get_queue_health() < 3:
        discover_new_keywords()
        
    jobs = get_next_batch(2)
    if not jobs:
        print("📭 Queue is empty.")
        return

    for kw_id, phrase in jobs:
        print(f"\n🚀 Starting Multi-Source Job: '{phrase}'")
        notify_sheet(phrase, "Started")
        
        try:
            page = smart_wiki_search(phrase)
            if not page or article_exists(page.title):
                print(f"⏭️ Skipping {phrase}: Already exists or no context.")
                update_job_status(kw_id, "skipped")
                continue

            # 🖼️ STRICT IMAGE VALIDATION
            image_url = get_wikipedia_image_pro(page.title)
            if not image_url:
                print(f"⏭️ Skipping {page.title}: No real photo found.")
                update_job_status(kw_id, "skipped")
                continue

            # 🔎 Deep Research Phase
            print(f"🔎 Scraping official sources for {page.title}...")
            links = find_research_links(page)
            official_raw = scrape_official_site(links["official"])
            github_raw = scrape_official_site(links["github"])
            
            # ✍️ Content Generation
            content = await generate_deep_dive(page.title, page.summary, official_raw, github_raw)
            
            if content:
                slug = create_unique_slug(page.title)
                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    """INSERT INTO articles (title, slug, content, image_url, category, status, source_url, word_count) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (page.title, slug, content, image_url, "Hardware", "published", page.url, len(content.split()))
                )
                conn.commit()
                conn.close()
                update_job_status(kw_id, "completed")
                notify_sheet(phrase, "Completed", "Hardware", len(content.split()))
                print(f"✅ Published: {page.title}")
            else:
                print(f"❌ Validation failed for {phrase}.")
                update_job_status(kw_id, "failed")
        except Exception as e:
            print(f"❌ Fatal Error: {e}")
            update_job_status(kw_id, "failed")

if __name__ == "__main__":
    asyncio.run(main())