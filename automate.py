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
from datetime import datetime 

# --- CONFIGURATION ---
DB_PATH = "content.db"
# IMPORTANT: Rotate your key since it was previously exposed!
GROQ_API_KEY = os.getenv("GROQ_API_KEY") 
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
        category TEXT,
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
    print("\n--- 🔍 DYNAMIC TREND DISCOVERY ---")
    now = datetime.now()
    current_month_year = now.strftime("%B %Y") 
    
    prompt = f"""
    It is currently {current_month_year}. 
    Identify 10 high-value tech topics trending specifically for {current_month_year}. 
    Balance: 60% Hardware (specific models like GPUs, phones, CPUs), 
    20% AI breakthroughs (models, frameworks), 
    20% Developer tools (languages, libraries).
    Return ONLY names separated by commas. No intro, no numbers.
    """
    
    try:
        completion = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        new_niches = [n.strip() for n in completion.choices[0].message.content.split(',')]
        print(f"📡 {current_month_year} Trends Found: {', '.join(new_niches)}")
        ingest_keywords(new_niches, category=f"Discovery-{current_month_year}")
    except Exception as e:
        print(f"❌ Discovery Error: {e}")

async def generate_deep_dive(topic, wiki, github, official, wiki_url, git_url=None, off_url=None, category="Technology"):
    references = f"<li>Main Reference: {wiki_url}</li>"
    if git_url: references += f"<li>Development Hub: {git_url}</li>"
    if off_url: references += f"<li>Official Documentation: {off_url}</li>"

    prompt = f"""
    Write a 1500-word authoritative Technical Deep-Dive on '{topic}'. 
    CATEGORY: {category}

    DATA PROVIDED:
    - Wikipedia: {wiki[:1000]}
    - GitHub README: {github[:1500]}
    - Official Docs: {official[:2000]}
    
    REQUIRED HTML STRUCTURE:
    1. A <blockquote> containing a high-level technical TL;DR.
    2. <h2>Architecture & Core Engineering</h2>
    3. <h2>Key Specifications & Performance</h2> (Use <ul> and <li> for data points)
    4. <h2>Technical Verdict</h2>
    5. <hr><h3>Sources & Technical References</h3><ul>{references}</ul>
    
    TONE: Lead Systems Architect. Clean HTML only.
    """
    
    try:
        chat = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are a lead technical architect specializing in systems and hardware."},
                      {"role": "user", "content": prompt}]
    )
        return chat.choices[0].message.content
    except Exception as e:
        print(f"❌ AI Generation Error: {e}")
        return None

# --- 4. THE MAIN LOOP ---
def notify_sheet(topic, status, category="N/A", word_count=0, details=""):
    SHEET_URL = "https://script.google.com/macros/s/AKfycbwaNy5Ei0iDmrEmj2iVp9gZdoVcd9y0r_d7Er7pi5zvvkjvlbRl5BcQQEqDwx-7fHVb/exec"
    payload = {
        "topic": topic,
        "status": status,
        "category": category,
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
    if health < 3:
        discover_new_keywords()

    job = get_next_job()
    if not job: 
        print("📭 Nothing to process.")
        return

    kw_id, phrase = job
    print(f"\n--- 🚀 PROCESSING JOB: {phrase} ---")

    # --- HYBRID STRATEGY CATEGORIZATION ---
    phrase_lower = phrase.lower()
    if any(x in phrase_lower for x in ['rtx', 'amd', 'intel', 'iphone', 'galaxy', 'pro', 'chip', 'm4', 'm5']):
        category = "Hardware"
        img_style = "hyper-realistic_studio_product_photography_sleek_tech_8k"
    elif any(x in phrase_lower for x in ['ai', 'gpt', 'llama', 'quantum', 'starship', 'robot']):
        category = "AI & Innovations"
        img_style = "futuristic_tech_visualization_glowing_abstract_8k"
    else:
        category = "Software & Dev"
        img_style = "minimalist_software_interface_code_background_8k"
    
    print(f"📂 Assigned Category: {category}")

    try:
        search = wikipedia.search(phrase)
        if not search: raise Exception("No Wikipedia results found.")
        
        page = wikipedia.page(search[0], auto_suggest=False)
        off_url, git_url = find_links_on_wiki(page)
        off_data = scrape_official_site(off_url)
        git_data = fetch_github_readme(git_url)

        content = await generate_deep_dive(page.title, page.summary, git_data, off_data, page.url, git_url, off_url, category)
        
        if content:
            slug = create_slug(page.title)
            word_count = len(content.split())
            image_url = f"https://image.pollinations.ai/prompt/{img_style}_{slug}?width=1280&height=720&nologo=true"
            
            conn = sqlite3.connect(DB_PATH)
            conn.execute("""
            INSERT INTO articles (title, content, slug, image_url, category, source_url, word_count) 
            VALUES (?, ?, ?, ?, ?, ?, ?)""", 
            (page.title, content, slug, image_url, category, off_url or page.url, word_count))
            conn.commit()
            conn.close()

            update_job_status(kw_id, 'completed')
            
            article_url = f"https://{REPO_OWNER}.github.io/{REPO_NAME}/article/{slug}"
            notify_sheet(page.title, "SUCCESS", category, word_count, article_url)
            print(f"🏆 SUCCESS: [{category}] '{page.title}' published.")

    except Exception as e:
        error_msg = str(e)
        notify_sheet(phrase, "FAILED", category, 0, error_msg)
        print(f"❌ CRITICAL FAILURE: {error_msg}")
        update_job_status(kw_id, 'failed')

if __name__ == "__main__":
    asyncio.run(main())