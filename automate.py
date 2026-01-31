import os
import time
import requests
import wikipedia
import sqlite3
import asyncio
import re
import hashlib
import json
from bs4 import BeautifulSoup
from groq import Groq
from datetime import datetime

# --- CONFIGURATION ---
DB_PATH = "content.db"
# SECURITY: These keys must be set in your Environment Variables for GitHub Actions
GROQ_API_KEY = "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"
GH_TOKEN = os.getenv("GH_TOKEN")
REPO_OWNER = "yashawanthbg2001"
REPO_NAME = "knownow"

# Deployment Target: Your latest Google Sheet Webhook URL
SHEET_URL = "https://script.google.com/macros/s/AKfycbwKiSlkcilabPXx87eZp6ZQN3qRK1uk_ZUEcDT_gdyR5Wo5txO5jGrpFSs0qCzcCU09/exec"

ai = Groq(api_key=GROQ_API_KEY)

# --- 1. SINGLE-ROW JOB LOGGER ---

class JobLogger:
    """Buffers technical steps for a gadget and sends a single professional row to Google Sheets."""
    def __init__(self, topic):
        self.topic = topic
        self.start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.steps = {
            "Wiki_Source": "⏳ Pending",
            "Image_Status": "⏳ Pending",
            "Official_Scrape": "⏳ Pending",
            "AI_Generation": "⏳ Pending",
            "Final_Status": "Processing",
            "Details": ""
        }

    def update(self, key, status, detail=""):
        self.steps[key] = status
        if detail:
            # Buffer details to explain any errors or successes
            self.steps["Details"] = f"{self.steps['Details']} | {detail}".strip(" |")

    def send_to_sheet(self):
        payload = {
            "timestamp": self.start_time,
            "topic": self.topic,
            "wiki_source": self.steps["Wiki_Source"],
            "image_status": self.steps["Image_Status"],
            "official_scrape": self.steps["Official_Scrape"],
            "ai_generation": self.steps["AI_Generation"],
            "final_status": self.steps["Final_Status"],
            "details": self.steps["Details"]
        }
        try:
            requests.post(SHEET_URL, json=payload, timeout=12)
        except Exception as e:
            print(f"⚠️ Sheet Link Error for {self.topic}: {e}")

# --- 2. DATABASE & INFRASTRUCTURE ---

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Ensure keywords table has category
    cursor.execute("""CREATE TABLE IF NOT EXISTS keywords (
        id INTEGER PRIMARY KEY AUTOINCREMENT, phrase TEXT UNIQUE, category TEXT,
        status TEXT DEFAULT 'pending', priority INTEGER DEFAULT 1, last_attempt DATETIME
    )""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT UNIQUE, content TEXT,
        slug TEXT UNIQUE, image_url TEXT, category TEXT, tier INTEGER DEFAULT 2,
        status TEXT DEFAULT 'published', source_url TEXT, word_count INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()

def get_queue_health():
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM keywords WHERE status = 'pending'").fetchone()[0]
    conn.close()
    return count

def get_next_batch(size=3):
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
    added = 0
    for phrase in keywords_list:
        clean_p = phrase.strip()
        # Filter out AI chatter or overly short phrases
        if clean_p and 3 < len(clean_p) < 65 and "not able" not in clean_p.lower():
            try:
                cursor.execute("INSERT OR IGNORE INTO keywords (phrase, category) VALUES (?, ?)", (clean_p, category))
                if cursor.rowcount > 0: added += 1
            except: pass
    conn.commit()
    conn.close()
    print(f"📥 Queue: Ingested {added} unique keywords.")

# --- 3. ADVANCED MULTI-SOURCE RESEARCH ---

def create_unique_slug(title):
    base_slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    hash_suffix = hashlib.md5(title.encode()).hexdigest()[:5]
    return f"{base_slug}-{hash_suffix}"

def get_wikipedia_image_strict(page_title):
    try:
        clean_title = re.sub(r'\([^)]*\)', '', page_title).strip()
        URL = "https://en.wikipedia.org/w/api.php"
        headers = {"User-Agent": "KnowNowBot/1.0"}
        params = {"action": "query", "format": "json", "titles": clean_title, "prop": "pageimages", "piprop": "original", "formatversion": "2"}
        res = requests.get(URL, params=params, headers=headers, timeout=10).json()
        pages = res.get("query", {}).get("pages", [])
        if pages and "original" in pages[0]:
            img_url = pages[0]["original"]["source"]
            if "upload.wikimedia.org" in img_url: return img_url
    except: pass
    return None

def find_deep_links(wiki_page):
    """Finds top 3 technical/official links, filtering out social media clutter."""
    found = []
    noise = ["facebook", "twitter", "instagram", "youtube", "amazon", "linkedin"]
    try:
        for link in wiki_page.references:
            if not any(x in link.lower() for x in noise):
                found.append(link)
            if len(found) >= 3: break
    except: pass
    return found

def scrape_url_technical(url):
    """Aggressively scrapes raw text data from tech documentation."""
    if not url or "wikipedia" in url: return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code != 200: return ""
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]): tag.decompose()
        main_content = soup.find("main") or soup.find("article") or soup.body
        return re.sub(r"\s+", " ", main_content.get_text())[:3500]
    except: return ""

# --- 4. TREND DISCOVERY ---

def discover_new_keywords():
    print("\n--- 🔍 DISCOVERING 2026 TRENDS ---")
    now = datetime.now()
    prompt = "List 10 specific hardware models or tech products released or trending in January 2026. Return ONLY comma-separated names."
    try:
        completion = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are a 2026 tech analyst. Return only comma-separated product names."},
                      {"role": "user", "content": prompt}]
        )
        ingest_keywords(completion.choices[0].message.content.split(","), f"Trends-{now.strftime('%m-%Y')}")
    except: print("❌ Discovery failed.")

# --- 5. AI CONTENT ARCHITECT ---

async def generate_authority_article(topic, wiki, aggregated_data):
    """Synthesizes multiple sources into a professional technical review."""
    prompt = f"""
    Write a 1200-word Technical Review for '{topic}' as of 2026.
    PRIMARY SOURCE (Wiki): {wiki[:1000]}
    EXTERNAL DATA WALL: {aggregated_data[:4000]}
    
    REQUIRED HTML STRUCTURE:
    - <div class="verdict-box"><h2>Verdict</h2>Expert analysis</div>
    - <h2>Technical Architecture</h2>Silicon, Build, Engineering paragraphs.
    - <table class="spec-table">Technical specs</table>
    - <h2>Frequently Asked Questions</h2>Static divs (no accordions).
    - <script type="application/ld+json">JSON-LD Schema block</script>
    
    Strict Rule: prioritize External Data wall for real numbers and benchmarks.
    """
    try:
        chat = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "Professional Technical Lead. Output clean HTML only."},
                      {"role": "user", "content": prompt}]
        )
        return chat.choices[0].message.content
    except: return None

# --- 6. MAIN EXECUTION ---

async def main():
    init_db()
    if get_queue_health() < 5: discover_new_keywords()
        
    jobs = get_next_batch(3)
    if not jobs:
        print("📭 Queue is empty.")
        return

    for kw_id, phrase in jobs:
        logger = JobLogger(phrase)
        print(f"\n⚡ Processing: {phrase}")

        try:
            # 1. Wiki Search
            try:
                page = wikipedia.page(phrase, auto_suggest=False)
            except:
                s = wikipedia.search(phrase)
                if not s:
                    logger.update("Wiki_Source", "❌ Failed", "No match"); update_job_status(kw_id, "failed"); logger.send_to_sheet(); continue
                page = wikipedia.page(s[0], auto_suggest=False)
            
            logger.update("Wiki_Source", "✅ Success", page.title)

            if article_exists(page.title):
                logger.update("Final_Status", "⏭️ Skipped", "Already in DB"); update_job_status(kw_id, "completed"); logger.send_to_sheet(); continue

            # 2. Image Strict
            img_url = get_wikipedia_image_strict(page.title)
            if not img_url:
                logger.update("Image_Status", "❌ Failed", "No photo found"); update_job_status(kw_id, "skipped"); logger.send_to_sheet(); continue
            logger.update("Image_Status", "✅ Success")

            # 3. Aggressive Scrape (The Fix)
            print(f"🔎 Aggregating data for {page.title}...")
            deep_links = find_deep_links(page)
            master_context = ""
            for url in deep_links:
                raw_text = scrape_url_technical(url)
                if raw_text: master_context += f"\nSOURCE ({url}):\n{raw_text}\n"
            
            scraped_count = len(master_context)
            logger.update("Official_Scrape", "✅ Done", f"Sources: {len(deep_links)} | Chars: {scraped_count}")

            # 4. AI Synthesis
            content = await generate_authority_article(page.title, page.summary, master_context)

            if content:
                slug = create_unique_slug(page.title)
                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    "INSERT INTO articles (title, slug, content, image_url, category, source_url, word_count) VALUES (?,?,?,?,?,?,?)",
                    (page.title, slug, content, img_url, "Hardware", page.url, len(content.split()))
                )
                conn.commit(); conn.close()
                update_job_status(kw_id, "completed")
                
                logger.update("AI_Generation", "✅ Success")
                logger.update("Final_Status", "Published")
                print(f"✅ Published: {page.title}")
            else:
                logger.update("Final_Status", "❌ Failed", "AI Error"); update_job_status(kw_id, "failed")

        except Exception as e:
            logger.update("Final_Status", "⚠️ Error", str(e)); update_job_status(kw_id, "failed")
        
        logger.send_to_sheet()

if __name__ == "__main__":
    asyncio.run(main())