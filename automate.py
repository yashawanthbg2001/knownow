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
        title TEXT,
        content TEXT,
        slug TEXT UNIQUE,
        image_url TEXT,
        category TEXT,
        status TEXT DEFAULT 'published',
        source_url TEXT,
        word_count INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    return conn


def get_queue_health():
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM keywords WHERE status = 'pending'"
    ).fetchone()[0]
    conn.close()
    return count


def get_next_job():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, phrase FROM keywords WHERE status = 'pending' ORDER BY priority DESC, id ASC LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()
    return row


def update_job_status(kw_id, status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE keywords SET status = ?, last_attempt = CURRENT_TIMESTAMP WHERE id = ?",
        (status, kw_id),
    )
    conn.commit()
    conn.close()


def ingest_keywords(keywords_list, category="Technology"):
    """
    Ingests a list of keywords into the database, splitting multi-model phrases
    into separate jobs for individual products where necessary.
    """
    # Common phrases or delimiters that indicate multiple variants/products
    MULTI_SPLIT_MARKERS = [" and ", ",", "/", "+", " or "]

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for phrase in keywords_list:
        # Check if the phrase mentions multiple variants
        if any(marker in phrase.lower() for marker in MULTI_SPLIT_MARKERS):
            for product in re.split(
                r" and |,|/|\+", phrase
            ):  # Split the phrase by markers
                product = product.strip()
                if product:  # Avoid empty splits after stripping
                    cursor.execute(
                        "INSERT OR IGNORE INTO keywords (phrase, category) VALUES (?, ?)",
                        (product, category),
                    )
        else:
            cursor.execute(
                "INSERT OR IGNORE INTO keywords (phrase, category) VALUES (?, ?)",
                (phrase.strip(), category),
            )

    conn.commit()
    conn.close()


# --- 2. RESEARCH TOOLS ---


def fetch_github_readme(repo_path):
    if not repo_path or "github.com" not in repo_path:
        return ""
    path = repo_path.replace("https://github.com/", "").strip("/")
    url = f"https://api.github.com/repos/{path}/readme"
    headers = {"Authorization": f"token {GH_TOKEN}"} if GH_TOKEN else {}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            content_b64 = res.json().get("content", "")
            return base64.b64decode(content_b64).decode("utf-8")[:3500]
    except:
        return ""


def scrape_official_site(url):
    if not url or "wikipedia" in url:
        return ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code != 200:
            return ""
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["nav", "footer", "script", "style", "header"]):
            tag.decompose()
        content_area = soup.find("main") or soup.find("article") or soup.body
        important_elements = content_area.find_all(["table", "ul", "p", "h2"])
        text = " ".join([i.get_text() for i in important_elements])
        return re.sub(r"\s+", " ", text)[:5000]
    except:
        return ""


def find_links_on_wiki(wiki_page):
    official, github = "", ""
    try:
        for link in wiki_page.references:
            if "github.com" in link and not github:
                github = link
            elif (
                any(
                    x in link.lower()
                    for x in ["official", "product", "specs", "developer"]
                )
                and not official
            ):
                official = link
    except:
        pass
    return official, github


# --- 3. AI & CONTENT TOOLS ---


def create_slug(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return f"{text.strip('-')}-{int(time.time())}"


def discover_new_keywords():
    print("\n--- 🔍 DYNAMIC TREND DISCOVERY ---")
    now = datetime.now()
    current_month_year = now.strftime("%B %Y")

    # Force the AI to provide specific model names
    prompt = f"""
    It is currently {current_month_year}. 
    Identify 10 SPECIFIC trending product models (e.g., 'Sony WH-1000XM5', 'Samsung Galaxy S24 Ultra', 'MacBook Air M3'). 
    Do not provide general series names like 'RTX 40 series' or 'iPhone 15 lineup'.
    Focus on: Smartphones, Laptops, Audio, Smart Home, and Wearables.
    Return ONLY names separated by commas.
    """
    try:
        completion = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
        )
        new_niches = [
            n.strip() for n in completion.choices[0].message.content.split(",")
        ]
        ingest_keywords(new_niches, category=f"Discovery-{current_month_year}")
    except Exception as e:
        print(f"❌ Discovery Error: {e}")


async def generate_deep_dive(
    topic,
    wiki,
    github,
    official,
    wiki_url,
    git_url=None,
    off_url=None,
    category="Technology",
):
    # Enforce single-product specificity but allow common tech descriptors
    DISALLOWED_TERMS = ["series", " and ", " vs ", " lineup"]

    # Check if the topic is too broad
    if any(term in topic.lower() for term in DISALLOWED_TERMS):
        print(f"⚠️ Skipping '{topic}': Topic is too broad/ambiguous.")
        return None  # Return None instead of raising an error to keep the loop running

    # AI prompt setup
    prompt = f"""
    Your task is to review the product '{topic}' as of 2026. 
    
    **STRUCTURE AND RULES:**
    - Start with a <div class="verdict-box">:
      - Summarize who should buy this product (2-3 bullet points).
      - Summarize who should avoid this product (2-3 bullet points).
      - Provide a short, opinionated final verdict.
    - Include a **"Should You Buy the {topic} in 2026?"** text.
    - Write **Real-World Performance Insights**:
      - Daily user experience, not just stats ("feels fluid even on heavy multitasking").
      - Specifics (gaming, battery, camera details, as applicable)
    - Include a **Key Specs Table with Explanations**:
      - Example: "8GB RAM – Smooth for multitasking but heavy apps may slow down."
    - Include **2-5 Buyer FAQs** (target high purchase intent).
    - Mention at least 2 concrete **pain points or trade-offs**.

    **DO NOT:**
    - Cover multiple products (focus only on '{topic}' if any variants exist).
    - Write encyclopedic/wikipedia-style content. This must be opinionated.
    - Avoid deep theoretical architecture discussions—keep it relevant to buyers as of 2026.

    **INPUT CONTEXT:**
    - Wikipedia Summary: {wiki[:1200]}
    - GitHub Data: {github[:800]}
    - Official Website Data: {official[:800]}
    - Wikipedia URL: {wiki_url}
    - GitHub URL (if available): {git_url or "N/A"}
    - Official Documentation URL (if available): {off_url or "N/A"}

    Write clean and valid HTML output. Your content must follow the structure exactly as described.
    """
    try:
        # Generate the article content using the AI model
        chat = ai.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert product reviewer writing for a high-buy-intent audience.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        return chat.choices[0].message.content
    except Exception as e:
        print(f"❌ AI Generation Error: {e}")
        return None


def notify_sheet(topic, status, category="N/A", word_count=0, details=""):
    SHEET_URL = "https://script.google.com/macros/s/AKfycbwaNy5Ei0iDmrEmj2iVp9gZdoVcd9y0r_d7Er7pi5zvvkjvlbRl5BcQQEqDwx-7fHVb/exec"
    payload = {
        "topic": topic,
        "status": status,
        "category": category,
        "word_count": word_count,
        "details": details,
    }
    try:
        requests.post(SHEET_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"📡 Sheet Notification Failed: {e}")


# --- 4. THE MAIN LOOP ---

# Allowed categories for content generation
ALLOWED_CATEGORIES = [
    "smartphones",
    "smartphone",
    "laptops",
    "laptop",
    "notebook",
    "audio",
    "headphones",
    "earbuds",
    "speakers",
    "earphones",
    "smart home",
    "thermostat",
    "light",
    "bulb",
    "switch",
    "plug",
    "hub",
    "wearables",
    "smartwatch",
    "watch",
    "fitness",
    "tracker",
    "band",
]


def categorize_phrase(phrase):
    """
    Determines the product category based on keywords in the phrase.
    Returns the matching category or None if no match is found.
    """
    phrase_lower = phrase.lower()
    if any(
        x in phrase_lower for x in ["smartphone", "phone", "galaxy", "iphone", "pixel"]
    ):
        return "Smartphones"
    elif any(x in phrase_lower for x in ["laptop", "notebook", "macbook"]):
        return "Laptops"
    elif any(
        x in phrase_lower
        for x in ["headphones", "earbuds", "earphones", "audio", "speakers"]
    ):
        return "Audio"
    elif any(
        x in phrase_lower
        for x in ["smart home", "thermostat", "light", "bulb", "switch", "plug", "hub"]
    ):
        return "Smart Home Devices"
    elif any(
        x in phrase_lower
        for x in ["wearables", "watch", "smartwatch", "fitness", "tracker", "band"]
    ):
        return "Wearables"
    return None


def get_tier(phrase):
    """
    Classifies a keyword or phrase (product/topic) into a Tier.
    Returns Tier 1, Tier 2, or Tier 3.
    """
    phrase_lower = phrase.lower()

    # Tier 1: High-demand or flagship products
    if any(
        x in phrase_lower for x in ["iphone 15", "galaxy s24", "macbook pro", "pixel 9"]
    ):
        return 1

    # Tier 2: Mid-range or older products
    if any(
        x in phrase_lower
        for x in [
            "iphone 14",
            "galaxy buds",
            "macbook air",
            "apple watch se",
            "rtx 4060",
        ]
    ):
        return 2

    # Tier 3: Long-tail queries or supporting pages
    if any(
        x in phrase_lower
        for x in ["specs", "compatibility", "vs", "comparison", "feature", "review"]
    ):
        return 3

    # Default to Tier 3 for uncategorized products/topics
    return 3


def can_generate_more_tier(tier):
    """
    Checks whether more articles can be generated for a specific Tier.
    Limits:
        - Tier 1: 3 articles/day.
        - Tier 2: 8 articles/day.
        - Tier 3: 2 articles/day.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    tier_count = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE DATE(created_at) = ? AND tier = ?",
        (today, tier),
    ).fetchone()[0]
    conn.close()
    if tier == 1 and tier_count >= 3:
        return False
    if tier == 2 and tier_count >= 8:
        return False
    if tier == 3 and tier_count >= 2:
        return False
    return True


async def main():
    init_db()
    print("\n--- 🛠️ WORKFLOW INITIALIZED ---")

    if get_queue_health() < 3:
        discover_new_keywords()

    job = get_next_job()
    if not job:
        print("📭 Nothing to process.")
        return

    kw_id, phrase = job
    print(f"\n--- 🚀 PROCESSING JOB: {phrase} ---")

    # Categorize the phrase by Tier
    tier = get_tier(phrase)
    print(f"📂 Categorized as: Tier {tier}")

    # Tier-specific processing logic
    if tier == 1:
        print(
            "⏫ High-priority content! Manually review this output for quality control."
        )
    elif tier == 2:
        print("⚙️ Standard content. Follow template.")
    elif tier == 3:
        print("📄 Reference/low-priority content. May be noindexed.")

    # Fetch Wikipedia and other sources
    search = wikipedia.search(phrase)
    if not search:
        print(f"❌ No Wikipedia results found for {phrase}")
        update_job_status(kw_id, "failed")
        return

    page = wikipedia.page(search[0], auto_suggest=False)
    category = categorize_phrase(phrase)
    off_url, git_url = find_links_on_wiki(page)
    off_data = scrape_official_site(off_url)
    git_data = fetch_github_readme(git_url)

    # Generate content
    # Generate content
    try:
        content = await generate_deep_dive(
            topic=page.title,
            wiki=page.summary,
            github=git_data,
            official=off_data,
            wiki_url=page.url,
            git_url=git_url,
            off_url=off_url,
            category=category,
        )
    except Exception as e:
        print(f"❌ AI Generation Error: {e}")
        content = None

    if content:
        # ... (your existing database save logic) ...
        print(f"🏆 SUCCESS: {page.title} published.")
        update_job_status(kw_id, "completed")  # Mark as finished
    else:
        print(f"⏭️ Job '{phrase}' was skipped or failed.")
        update_job_status(kw_id, "skipped")  # Prevent re-processing bad keywords

    # Save content in the database
    # Tier 3: Default behavior is to set status `noindex`
    noindex_flag = "noindex" if tier == 3 else "published"
# ... after generate_deep_dive() call ...

   # --- Inside your main() function, where you prepare data for the INSERT ---

    if content:
        slug = create_slug(page.title)
        
        # 1. Define styles based on your 5 core niches
        style_map = {
            "Smartphones": "sleek_minimalist_smartphone_product_photography_8k",
            "Laptops": "high_end_laptop_on_wooden_desk_cinematic_lighting",
            "Audio": "professional_headphones_close_up_depth_of_field",
            "Wearables": "smartwatch_on_wrist_modern_active_lifestyle",
            "Smart Home Devices": "modern_minimalist_living_room_with_smart_tech"
        }
        
        # 2. Get the style or use a high-tech default
        style = style_map.get(category, "cutting_edge_technology_product_shot")
        
        # 3. Generate the actual URL
        image_url = f"https://image.pollinations.ai/prompt/{style}_{slug}?width=1280&height=720&nologo=true"

        # 4. Save to Database with Error Handling
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                """INSERT INTO articles (title, content, slug, image_url, category, source_url, word_count, status) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (page.title, content, slug, image_url, category, page.url, len(content.split()), noindex_flag),
            )
            conn.commit()
            conn.close()
            
            print(f"🏆 SUCCESS: {page.title} published.")
            update_job_status(kw_id, "completed")
            
        except Exception as e:
            print(f"❌ Database Save Error: {e}")
            update_job_status(kw_id, "failed")
    else:
        # Handles broad-topic skips or AI generation failures
        print(f"⏭️ Job '{phrase}' was skipped or failed to generate.")
        update_job_status(kw_id, "skipped")


if __name__ == "__main__":
    asyncio.run(main())
