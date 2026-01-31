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
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
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
    print(f"🔍 Searching for an image on Wikipedia for '{page_title}'...")
    try:
        clean_title = re.sub(r'\([^)]*\)', '', page_title).strip()
        URL = "https://en.wikipedia.org/w/api.php"
        headers = {"User-Agent": "KnowNowBot/1.0"}
        PARAMS = {
            "action": "query",
            "format": "json",
            "titles": clean_title,
            "prop": "pageimages",
            "piprop": "original",
            "formatversion": "2"
        }
        res = requests.get(URL, params=PARAMS, headers=headers, timeout=10).json()
        pages = res.get("query", {}).get("pages", [])
        if pages and "original" in pages[0]:
            img_url = pages[0]["original"]["source"]
            print(f"✅ Wikipedia image found: {img_url}")
            return img_url
        print("⚠️ No image found on Wikipedia for this article.")
    except Exception as e:
        print(f"❌ Error fetching Wikipedia image: {e}")
    return None

def smart_wiki_search(phrase):
    try:
        print(f"🔍 Searching Wikipedia for '{phrase}'...")
        return wikipedia.page(phrase, auto_suggest=False)
    except wikipedia.exceptions.DisambiguationError as e:
        print(f"⚠️ Disambiguation detected for '{phrase}'. Using '{e.options[0]}'...")
        return wikipedia.page(e.options[0], auto_suggest=False)
    except wikipedia.exceptions.PageError:
        print(f"⚠️ No exact match found for '{phrase}'. Attempting search fallback...")
        search_res = wikipedia.search(phrase)
        if search_res:
            return wikipedia.page(search_res[0], auto_suggest=False)
    except Exception as e:
        print(f"❌ Error during Wikipedia search: {e}")
    return None

def scrape_official_site(url):
    if not url or "wikipedia" in url:
        return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0"}
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["script", "style", "footer", "nav", "header"]):
            tag.decompose()
        return re.sub(r"\s+", " ", soup.get_text())[:5000]
    except Exception as e:
        print(f"⚠️ Failed to scrape official site: {e}")
        return ""

def find_links_on_wiki(wiki_page):
    try:
        print(f"🔗 Searching for official references...")
        for link in wiki_page.references:
            if any(x in link.lower() for x in ["official", "specs", "product", "github"]):
                return link
    except Exception as e:
        print(f"⚠️ Error fetching references: {e}")
    return ""

# --- 3. NOTIFICATION TO SHEETS ---

def notify_sheet(topic, status, category="N/A", word_count=0, details=""):
    """
    Log detailed job statuses to Google Sheets.
    """
    SHEET_URL = "https://script.google.com/macros/s/AKfycbwaNy5Ei0iDmrEmj2iVp9gZdoVcd9y0r_d7Er7pi5zvvkjvlbRl5BcQQEqDwx-7fHVb/exec"
    payload = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "topic": topic,
        "status": status,
        "category": category,
        "word_count": word_count,
        "details": details
    }
    try:
        response = requests.post(SHEET_URL, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"✅ Logged to sheet: {topic}, Status: {status}")
        else:
            print(f"⚠️ Failed to log to sheet. HTTP {response.status_code}")
    except Exception as e:
        print(f"❌ Error while logging to sheet: {e}")

# --- 4. AI GENERATION ---

async def generate_deep_dive(topic, wiki_summary, official_data, category, tier):
    prompt = f"""
    Create a {category} review for '{topic}'. Use Wikipedia: {wiki_summary[:800]}, Official: {official_data[:800]}.
    Include FAQs, pros, cons, and detailed specs.
    """
    try:
        result = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "Produce article reviews."}, {"role": "user", "content": prompt}]
        )
        return result.choices[0].message.content
    except Exception as e:
        print(f"❌ Error generating AI content: {e}")
        return None

# --- 5. MAIN EXECUTION ---

async def main():
    init_db()
    if get_queue_health() == 0:
        print("📭 No pending jobs.")
        return
    for kw_id, phrase in get_next_batch(2):
        print(f"\n🚀 Starting Job: '{phrase}'")
        notify_sheet(phrase, "Started", details="Processing initiated")
        try:
            page = smart_wiki_search(phrase)
            slug = create_slug(page.title) if page else ""
            image_url = get_wikipedia_image_pro(page.title) or "AI-generated fallback"
            content = await generate_deep_dive(page.title, page.summary, "", "Laptops", 2)
            if content:
                conn = sqlite3.connect(DB_PATH)
                conn.execute("""INSERT INTO articles (title, slug, content, image_url) VALUES (?, ?, ?, ?)""",
                             (page.title, slug, content, image_url))
                conn.commit()
                conn.close()
                notify_sheet(phrase, "Completed", "Laptops", len(content.split()), "Published successfully")
            else:
                notify_sheet(phrase, "Failed", details="Content generation failed")
        except Exception as e:
            notify_sheet(phrase, "Failed", details=str(e))

if __name__ == "__main__":
    asyncio.run(main())