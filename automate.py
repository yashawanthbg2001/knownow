import os
import time
import random
import requests
import wikipedia
import sqlite3
import asyncio
import re
from groq import Groq

# --- CONFIGURATION ---
DB_PATH = "content.db"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GH_TOKEN = os.getenv("GH_TOKEN") 
REPO_OWNER = "yashawanthbg2001"
REPO_NAME = "knownow"

ai = Groq(api_key=GROQ_API_KEY)

# --- 1. DATABASE FOUNDATION (The "Brain") ---

def init_db():
    """Initializes the multi-table SQLite schema for state tracking"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Keyword Queue (Keyword Ingestion & Deduplication)
    cursor.execute('''CREATE TABLE IF NOT EXISTS keywords (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phrase TEXT UNIQUE,
        category TEXT,
        status TEXT DEFAULT 'pending', -- pending, completed, failed
        priority INTEGER DEFAULT 1,
        last_attempt DATETIME
    )''')

    # Main Article Store (Metadata & Publishing Status)
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

# --- 2. KEYWORD INGESTION & SELECTION ---

def ingest_keywords(keywords_list, category="Technology"):
    """Adds new keywords to the queue, skipping duplicates automatically"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    added = 0
    for phrase in keywords_list:
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO keywords (phrase, category) VALUES (?, ?)", 
                (phrase.strip(), category)
            )
            if cursor.rowcount > 0:
                added += 1
        except Exception:
            continue
    conn.commit()
    conn.close()
    return added

def get_next_job():
    """Picks the next pending keyword from the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, phrase FROM keywords WHERE status = 'pending' ORDER BY priority DESC, id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return row # Returns (id, phrase)

def update_job_status(kw_id, status):
    """Updates the queue status so we don't repeat the same article"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE keywords SET status = ?, last_attempt = CURRENT_TIMESTAMP WHERE id = ?", (status, kw_id))
    conn.commit()
    conn.close()

# --- 3. CONTENT GENERATION (Fact Enrichment) ---

def discover_new_keywords():
    """Uses LLM to brainstorm 10 trending technical keywords based on the year 2026"""
    print("🔍 Discovering new tech niches...")
    
    prompt = """
    Generate a list of 10 trending, specific technical topics or product releases for January 2026.
    Focus on: Semiconductors, Space Exploration, AI hardware, and Web3 infrastructure.
    Return ONLY the phrases separated by commas. No numbers, no intro.
    Example: NVIDIA Blackwell B200, Starship Flight 7, TypeScript 5.8, Apple Vision Pro 2
    """
    
    try:
        completion = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        new_niches = completion.choices[0].message.content.split(',')
        # Clean and ingest
        added_count = ingest_keywords([n.strip() for n in new_niches], category="Auto-Discovered")
        print(f"✨ Successfully added {added_count} new trending keywords to the queue.")
    except Exception as e:
        print(f"Discovery Error: {e}")

def get_queue_health():
    """Checks if the queue is running low"""
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM keywords WHERE status = 'pending'").fetchone()[0]
    conn.close()
    return count

def create_slug(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return f"{text.strip('-')}-{int(time.time())}"

async def generate_article(topic, facts):
    """Llama 3.3 70B: Technical Content Generation"""
    model_name = "llama-3.3-70b-versatile" 
    prompt = f"""
    Write a 1200-word deep-dive technical guide on '{topic}'.
    Base Facts: {facts}
    
    Structure:
    1. Start with a 1-sentence TL;DR in a <blockquote>.
    2. Use <h2> for main sections and <h3> for technical details.
    3. Use <ul> and <li> for specifications.
    4. Write in a professional, authoritative tone for developers and tech enthusiasts.
    5. Output CLEAN HTML only.
    """
    try:
        completion = ai.chat.completions.create(
            model=model_name, 
            messages=[{"role": "system", "content": "You are a lead technical architect and writer."},
                      {"role": "user", "content": prompt}]
        )
        return completion.choices[0].message.content
    except Exception as e:
        print(f"AI Error: {e}")
        return None

# --- 4. THE CORE ENGINE ---

async def main():
    init_db()
    
    # 1. Check Queue Health
    if get_queue_health() < 5:
        discover_new_keywords()

    # 2. Get the next job from the newly filled queue
    job = get_next_job()
    if not job:
        print("❌ No keywords available even after discovery attempt.")
        return

    kw_id, phrase = job
    print(f"🚀 Processing: {phrase}")

    # ... (Rest of your Wikipedia/AI generation code remains the same)

    try:
        # Fact Enrichment via Wikipedia
        search_results = wikipedia.search(phrase)
        if not search_results:
            update_job_status(kw_id, "failed")
            return

        try:
            page = wikipedia.page(search_results[0], auto_suggest=False)
        except Exception:
            update_job_status(kw_id, "failed")
            return

        # Generation
        slug = create_slug(page.title)
        content = await generate_article(page.title, page.summary[:2000])
        
        if content:
            word_count = len(content.split())
            image_url = f"https://image.pollinations.ai/prompt/tech_photo_{slug}?width=1280&height=720&nologo=true"

            # Save to Article Store (Publishing Status Tracker)
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO articles (title, content, slug, image_url, source_url, word_count) VALUES (?, ?, ?, ?, ?, ?)", 
                (page.title, content, slug, image_url, page.url, word_count)
            )
            conn.commit()
            conn.close()

            # Mark Keyword as Completed (Deduplication)
            update_job_status(kw_id, "completed")
            print(f"✅ Article Published: {page.title} ({word_count} words)")

            # Trigger GitHub Rebuild (If applicable)
            if GH_TOKEN:
                requests.post(
                    f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/dispatches",
                    headers={"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"},
                    json={"event_type": "automation-trigger"}
                )
    except Exception as e:
        print(f"❌ Critical Failure: {e}")
        update_job_status(kw_id, "failed")

if __name__ == "__main__":
    asyncio.run(main())