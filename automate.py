import os
import time
import random
import requests
import wikipedia
import sqlite3
import asyncio
import re
import base64
from bs4 import BeautifulSoup
from groq import Groq

# --- CONFIGURATION ---
DB_PATH = "content.db"
# PROTECT YOUR KEY: Use os.getenv and set it in your system or GitHub Secrets
GROQ_API_KEY = "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"
GH_TOKEN = os.getenv("GH_TOKEN") 
REPO_OWNER = "yashawanthbg2001"
REPO_NAME = "knownow"

ai = Groq(api_key=GROQ_API_KEY)

# --- 1. DATABASE & QUEUE HELPERS ---

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS keywords (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phrase TEXT UNIQUE,
        category TEXT,
        status TEXT DEFAULT 'pending',
        priority INTEGER DEFAULT 1,
        last_attempt DATETIME
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        content TEXT,
        slug TEXT UNIQUE,
        image_url TEXT,
        status TEXT DEFAULT 'published',
        source_url TEXT,
        word_count INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    return conn

def get_queue_health():
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM keywords WHERE status = 'pending'").fetchone()[0]
    conn.close()
    return count

def get_next_job():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, phrase FROM keywords WHERE status = 'pending' ORDER BY priority DESC, id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return row

def update_job_status(kw_id, status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE keywords SET status = ?, last_attempt = CURRENT_TIMESTAMP WHERE id = ?", (status, kw_id))
    conn.commit()
    conn.close()

def ingest_keywords(keywords_list, category="Technology"):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for phrase in keywords_list:
        cursor.execute("INSERT OR IGNORE INTO keywords (phrase, category) VALUES (?, ?)", (phrase.strip(), category))
    conn.commit()
    conn.close()

# --- 2. RESEARCH TOOLS ---

def fetch_github_readme(repo_path):
    if not repo_path or "github.com" not in repo_path: return ""
    path = repo_path.replace("https://github.com/", "").strip("/")
    url = f"https://api.github.com/repos/{path}/readme"
    headers = {"Authorization": f"token {GH_TOKEN}"} if GH_TOKEN else {}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            content_b64 = res.json().get('content', '')
            return base64.b64decode(content_b64).decode('utf-8')[:3500]
    except: return ""

def scrape_official_site(url):
    if not url or "wikipedia" in url: return ""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
        }
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code != 200: return ""
        soup = BeautifulSoup(res.text, 'html.parser')
        for tag in soup(['nav', 'footer', 'script', 'style', 'header']):
            tag.decompose()
        content_area = soup.find('main') or soup.find('article') or soup.body
        important_elements = content_area.find_all(['table', 'ul', 'p', 'h2'])
        text = " ".join([i.get_text() for i in important_elements])
        return re.sub(r'\s+', ' ', text)[:5000]
    except: return ""

def find_links_on_wiki(wiki_page):
    official, github = "", ""
    try:
        for link in wiki_page.references:
            if "github.com" in link and not github: 
                github = link
            elif any(x in link.lower() for x in ["official", "product", "specs", "developer"]) and not official:
                official = link
    except: pass
    return official, github

# --- 3. AI & CONTENT TOOLS ---

def create_slug(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return f"{text.strip('-')}-{int(time.time())}"

def discover_new_keywords():
    print("\n--- 🔍 DISCOVERY PHASE ---")
    prompt = "Generate a list of 10 trending technical topics for 2026. Return ONLY phrases separated by commas."
    try:
        completion = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        new_niches = [n.strip() for n in completion.choices[0].message.content.split(',')]
        ingest_keywords(new_niches, category="Auto-Discovered")
        print(f"📡 AI Suggested: {', '.join(new_niches)}")
    except Exception as e:
        print(f"❌ Discovery Error: {e}")

async def generate_deep_dive(topic, wiki, github, official):
    prompt = f"Write a 1500-word deep-dive technical guide on '{topic}'. Wiki: {wiki[:1000]}. GitHub: {github[:1500]}. Official: {official[:2000]}. Use clean HTML."
    try:
        chat = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are a lead technical architect."},
                      {"role": "user", "content": prompt}]
        )
        return chat.choices[0].message.content
    except Exception as e:
        print(f"AI Error: {e}"); return None
        
        
        

# --- 4. THE MAIN LOOP ---
def notify_sheet(topic, status, word_count=0, details=""):
    SHEET_URL = "https://script.google.com/macros/s/AKfycbwaNy5Ei0iDmrEmj2iVp9gZdoVcd9y0r_d7Er7pi5zvvkjvlbRl5BcQQEqDwx-7fHVb/exec"
    payload = {
        "topic": topic,
        "status": status,
        "word_count": word_count,
        "details": details
    }
    try:
        requests.post(SHEET_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"📡 Sheet Notification Failed: {e}")

async def main():
    init_db()
    
    print("\n--- 🛠️ WORKFLOW INITIALIZED ---")
    health = get_queue_health()
    print(f"📊 Queue Health: {health} keywords pending.")

    if health < 3:
        discover_new_keywords()

    job = get_next_job()
    if not job: 
        print("📭 Nothing to process. Exiting.")
        return

    kw_id, phrase = job
    print(f"\n--- 🚀 PROCESSING JOB: {phrase} ---")

    try:
        search = wikipedia.search(phrase)
        if not search: raise Exception("No Wikipedia results found.")
        
        page = wikipedia.page(search[0], auto_suggest=False)
        off_url, git_url = find_links_on_wiki(page)
        
        off_data = scrape_official_site(off_url)
        git_data = fetch_github_readme(git_url)

        content = await generate_deep_dive(page.title, page.summary, git_data, off_data)
        
        if content:
            slug = create_slug(page.title)
            word_count = len(content.split())
            image_url = f"https://image.pollinations.ai/prompt/professional_studio_photo_of_{slug}_tech_gadget_8k?width=1280&height=720"
            
            print(f"📝 Article Generated. Word count: {word_count}")
            
            conn = sqlite3.connect(DB_PATH)
            conn.execute("""
            INSERT INTO articles (title, content, slug, image_url, source_url, word_count) 
            VALUES (?, ?, ?, ?, ?, ?)""", 
            (page.title, content, slug, image_url, off_url or page.url, word_count))
            conn.commit()
            conn.close()

            update_job_status(kw_id, 'completed')
            
            # ✅ SUCCESS LOGGING TO SHEET
            article_url = f"https://{REPO_OWNER}.github.io/{REPO_NAME}/article/{slug}"
            notify_sheet(page.title, "SUCCESS", word_count, article_url)
            print(f"🏆 SUCCESS: '{page.title}' is now in the database and logged to Sheet.")

    except Exception as e:
        # ❌ FAILURE LOGGING TO SHEET
        error_msg = str(e)
        notify_sheet(phrase, "FAILED", 0, error_msg)
        print(f"❌ CRITICAL FAILURE for '{phrase}': {error_msg}")
        update_job_status(kw_id, 'failed')

if __name__ == "__main__":
    asyncio.run(main())