"""
Fragrance Scout - Reddit niche perfume review monitor

Monitors r/perfumes and r/nicheperfumes via JSON API for interesting niche/indie perfume reviews
Filters using Gemini AI and displays in web UI
"""

import requests
import json
import time
import logging
import threading
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, List, Optional
from flask import Flask, render_template_string, request, abort
import google.generativeai as genai
from google.cloud import storage
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type


# Configuration
SUBREDDITS = ["perfumes", "nicheperfumes"]
JSON_FEEDS = [
    f"https://www.reddit.com/r/{sub}/new.json?limit=20" for sub in SUBREDDITS
]

# LLM Configuration - use environment variable or default to local
LLM_URL = os.getenv("LLM_URL", "http://127.0.0.1:1234/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3-4b-thinking-2507")
USE_GEMINI = os.getenv("USE_GEMINI", "false").lower() == "true"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Check interval (30 minutes in seconds)
CHECK_INTERVAL = 30 * 60

# Web UI port
WEB_UI_PORT = int(os.getenv("PORT", "5002"))

# GCS Storage
GCS_BUCKET = os.getenv("GCS_BUCKET", "")

# Authentication token for scan endpoint (set via environment variable)
SCAN_AUTH_TOKEN = os.getenv("SCAN_AUTH_TOKEN", "")

# Tracking file (local or GCS)
if GCS_BUCKET:
    TRACKING_FILE = "sent_posts.json"  # Filename in GCS
    POSTS_FILE = "found_posts.json"  # Store found posts in GCS
else:
    TRACKING_FILE = Path(__file__).parent / "sent_posts.json"
    POSTS_FILE = None

# Store found posts for web UI
found_posts = []

# Logging setup
LOG_FILE = Path(__file__).parent / "fragrance_scout.log"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# LLM Prompt for filtering
FILTER_PROMPT = """You are a fragrance discovery agent tasked with identifying interesting niche and indie perfume reviews on Reddit.

**FOCUS ON:**
- Niche/indie/artisan brands (e.g., Nishane, Xerjoff, Amouage, Parfums de Marly, Roja, Zoologist, Slumberhouse, Bortnikoff, Papillon, Mona di Orio, Ormonde Jayne, Naomi Goodsir, Francesca Bianchi, Majda Bekkali, Hubigant, BDK, Areej Le Doré, etc.)
- Detailed reviews with scent notes, impressions, longevity, sillage, projection
- First impressions, wear tests, batch comparisons
- Discussion of note breakdowns and development
- Personal experiences with specific fragrances

**IGNORE:**
- Designer/mass-market brands (Dior, Boss, Chanel, Gucci, YSL, Armani, Versace, Paco Rabanne, etc.)
- Simple mentions without substance
- Purchase questions without reviews
- Recommendation requests (asking others for suggestions)
- "What should I buy?" or "Help me choose" posts
- Collection photos without detailed commentary
- Blind buy questions or shopping advice

**INPUT:** You will receive a Reddit post title and body.

**OUTPUT:** Respond with ONLY a JSON object (no markdown formatting, no code blocks):
{
  "interesting": true/false,
  "reason": "brief explanation of why this is or isn't interesting"
}

Be selective - only mark posts as interesting if they contain substantive review content about niche/indie fragrances."""


class FragranceScout:
    """Main fragrance scout service"""

    def __init__(self):
        self.gcs_client = storage.Client() if GCS_BUCKET else None
        self.sent_posts = self._load_tracking()
        self._load_found_posts()

    def _load_from_gcs(self, filename: str) -> Dict:
        """Load JSON data from GCS"""
        try:
            bucket = self.gcs_client.bucket(GCS_BUCKET)
            blob = bucket.blob(filename)
            if blob.exists():
                data = blob.download_as_text()
                return json.loads(data)
        except Exception as e:
            logger.error(f"Error loading {filename} from GCS: {e}")
        return {}

    def _save_to_gcs(self, filename: str, data: Dict):
        """Save JSON data to GCS"""
        try:
            bucket = self.gcs_client.bucket(GCS_BUCKET)
            blob = bucket.blob(filename)
            blob.upload_from_string(json.dumps(data, indent=2), content_type='application/json')
            logger.info(f"Saved {filename} to GCS")
        except Exception as e:
            logger.error(f"Error saving {filename} to GCS: {e}")

    def _load_tracking(self) -> Dict[str, str]:
        """Load tracking file of sent posts"""
        if GCS_BUCKET:
            return self._load_from_gcs(TRACKING_FILE)
        elif Path(TRACKING_FILE).exists():
            try:
                with open(TRACKING_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading tracking file: {e}")
        return {}

    def _save_tracking(self):
        """Save tracking file of sent posts"""
        try:
            # Keep only last 1000 entries to prevent file from growing indefinitely
            if len(self.sent_posts) > 1000:
                sorted_posts = sorted(self.sent_posts.items(), key=lambda x: x[1] if isinstance(x[1], str) else "")
                self.sent_posts = dict(sorted_posts[-1000:])

            if GCS_BUCKET:
                self._save_to_gcs(TRACKING_FILE, self.sent_posts)
            else:
                with open(TRACKING_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.sent_posts, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving tracking file: {e}")

    def _load_found_posts(self):
        """Load found posts from GCS or local storage"""
        global found_posts
        if GCS_BUCKET and POSTS_FILE:
            posts_data = self._load_from_gcs(POSTS_FILE)
            found_posts = posts_data.get("posts", [])
        # For local mode, found_posts stays in memory

    def _save_found_posts(self):
        """Save found posts to GCS"""
        if GCS_BUCKET and POSTS_FILE:
            self._save_to_gcs(POSTS_FILE, {"posts": found_posts})

    @retry(
        retry=retry_if_exception_type(requests.exceptions.HTTPError),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        stop=stop_after_attempt(3),
        reraise=True
    )
    def _fetch_reddit_json_with_retry(self, feed_url: str):
        """Fetch Reddit JSON feed with retry logic for rate limits"""
        headers = {
            'User-Agent': 'python:fragrance-scout:v1.0.0 (by /u/FragranceScoutBot)'
        }
        response = requests.get(feed_url, headers=headers, timeout=30)

        # Monitor Reddit rate limit headers per API documentation
        if 'X-Ratelimit-Remaining' in response.headers:
            remaining = response.headers.get('X-Ratelimit-Remaining')
            used = response.headers.get('X-Ratelimit-Used', 'N/A')
            reset = response.headers.get('X-Ratelimit-Reset', 'N/A')
            logger.debug(f"Reddit rate limit: {used} used, {remaining} remaining, resets in {reset}s")

            # Warn if we're getting close to the limit
            if remaining and int(float(remaining)) < 20:
                logger.warning(f"Reddit rate limit low: {remaining} requests remaining")

        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 60))
            logger.warning(f"Reddit rate limit hit, waiting {retry_after}s")
            time.sleep(retry_after)
            response.raise_for_status()
        response.raise_for_status()
        return response

    def _fetch_reddit_json(self, feed_url: str) -> List[Dict]:
        """Fetch and parse Reddit JSON feed with flair filtering"""
        try:
            logger.info(f"Fetching Reddit JSON feed: {feed_url}")
            response = self._fetch_reddit_json_with_retry(feed_url)
            data = response.json()

            if 'data' not in data or 'children' not in data['data']:
                logger.error(f"Unexpected JSON structure from {feed_url}")
                return []

            # Skip list - post flairs we don't want to process
            skip_flairs = [
                'recommendation',
                'collection pics',
                'bottle identification',
                'mod post',
                'look what i found'
            ]

            posts = []
            for child in data['data']['children']:
                post_data = child.get('data', {})

                # Get flair text (may be None or empty string)
                flair = post_data.get('link_flair_text', '') or ''

                # Skip posts with certain flairs
                if any(skip_flair in flair.lower() for skip_flair in skip_flairs):
                    logger.debug(f"Skipping post with flair: {flair}")
                    continue

                post = {
                    "id": post_data.get("name", ""),  # Reddit's unique ID (e.g., t3_abc123)
                    "title": post_data.get("title", ""),
                    "link": f"https://reddit.com{post_data.get('permalink', '')}",
                    "author": post_data.get("author", ""),
                    "published": str(post_data.get("created_utc", "")),
                    "summary": post_data.get("selftext", ""),
                    "flair": flair
                }
                posts.append(post)

            logger.info(f"Found {len(posts)} posts after flair filtering")
            return posts

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                logger.error(f"Reddit rate limit exhausted after retries for {feed_url}")
            else:
                logger.error(f"HTTP error fetching Reddit JSON {feed_url}: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching Reddit JSON feed {feed_url}: {e}")
            return []

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(3),
        reraise=True
    )
    def _query_gemini_with_retry(self, prompt: str, user_message: str):
        """Query Gemini with retry logic for rate limits"""
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            'gemini-2.5-flash',
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": {
                    "type": "object",
                    "properties": {
                        "interesting": {"type": "boolean"},
                        "reason": {"type": "string"}
                    },
                    "required": ["interesting", "reason"]
                }
            }
        )

        response = model.generate_content(f"{prompt}\n\n{user_message}")
        return json.loads(response.text)

    def _query_llm(self, title: str, body: str) -> Optional[Dict]:
        """Query LLM (Gemini or local) to determine if post is interesting"""
        try:
            user_message = f"TITLE: {title}\n\nBODY: {body}"

            # Log the full message being sent to LLM for debugging
            logger.debug("=" * 80)
            logger.debug("LLM REQUEST:")
            logger.debug(f"PROMPT:\n{FILTER_PROMPT}")
            logger.debug("-" * 80)
            logger.debug(f"USER MESSAGE:\n{user_message}")
            logger.debug("=" * 80)

            if USE_GEMINI:
                # Add throttling between requests (free tier: 10 req/min)
                time.sleep(6.5)  # ~9 requests per minute to stay under limit

                try:
                    return self._query_gemini_with_retry(FILTER_PROMPT, user_message)
                except Exception as e:
                    if "429" in str(e) or "quota" in str(e).lower():
                        logger.error(f"Gemini rate limit/quota exceeded: {e}")
                        return None
                    raise

            else:
                # Use local LLM with structured output
                payload = {
                    "model": LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": FILTER_PROMPT},
                        {"role": "user", "content": user_message}
                    ],
                    "temperature": 0.3,
                    "max_tokens": 500,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "fragrance_review_filter",
                            "strict": True,
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "interesting": {
                                        "type": "boolean",
                                        "description": "Whether this post is an interesting niche/indie perfume review"
                                    },
                                    "reason": {
                                        "type": "string",
                                        "description": "Brief explanation of why this is or isn't interesting"
                                    }
                                },
                                "required": ["interesting", "reason"],
                                "additionalProperties": False
                            }
                        }
                    }
                }

                response = requests.post(LLM_URL, json=payload, timeout=30)
                response.raise_for_status()

                result = response.json()
                assistant_message = result["choices"][0]["message"]["content"].strip()

                # Remove <think> tags if present (for thinking models)
                import re
                assistant_message = re.sub(r'<think>.*?</think>', '', assistant_message, flags=re.DOTALL).strip()

                # Remove markdown code blocks if present
                if assistant_message.startswith("```"):
                    assistant_message = assistant_message.split("\n", 1)[1]
                    assistant_message = assistant_message.rsplit("```", 1)[0].strip()

                # Try to extract JSON if response contains other text
                json_match = re.search(r'\{.*\}', assistant_message, re.DOTALL)
                if json_match:
                    assistant_message = json_match.group(0)

                if not assistant_message:
                    logger.error("LLM returned empty content after cleaning")
                    return None

                return json.loads(assistant_message)

        except Exception as e:
            logger.error(f"Error querying LLM: {e}")
            return None

    def _process_post(self, post: Dict) -> bool:
        """Process a single post - returns True if post was added"""
        post_id = post["id"]
        title = post["title"]
        link = post["link"]

        # Check if already sent
        if post_id in self.sent_posts:
            return False

        logger.info(f"Processing post: {title[:50]}...")

        # Extract body text from summary (Reddit RSS includes HTML)
        from html.parser import HTMLParser

        class MLStripper(HTMLParser):
            def __init__(self):
                super().__init__()
                self.reset()
                self.strict = False
                self.convert_charrefs = True
                self.text = []

            def handle_data(self, d):
                self.text.append(d)

            def get_data(self):
                return ''.join(self.text)

        stripper = MLStripper()
        stripper.feed(post["summary"])
        body = stripper.get_data()

        # Query LLM
        llm_result = self._query_llm(title, body)

        if not llm_result:
            logger.warning(f"LLM query failed for post: {title[:50]}...")
            return False

        if llm_result.get("interesting", False):
            logger.info(f"✨ INTERESTING POST FOUND: {title[:50]}...")
            logger.info(f"   Reason: {llm_result.get('reason', 'N/A')}")

            # Store post data for web UI
            post_data = {
                "timestamp": datetime.now(ZoneInfo("America/Los_Angeles")).isoformat(),
                "title": title,
                "author": post['author'],
                "link": link,
                "published": post['published'],
                "reason": llm_result.get('reason', 'N/A'),
                "body": body
            }
            found_posts.append(post_data)

            # Mark as processed
            logger.info("📱 Added to results")
            self.sent_posts[post_id] = datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()
            self._save_tracking()
            self._save_found_posts()  # Save to GCS if configured
            return True

        return False

    def run_once(self):
        """Run a single check cycle"""
        logger.info("=" * 80)
        logger.info("Starting fragrance scout check cycle")
        logger.info("=" * 80)

        total_posts = 0
        total_sent = 0

        for feed_url in JSON_FEEDS:
            posts = self._fetch_reddit_json(feed_url)
            total_posts += len(posts)

            for post in posts:
                try:
                    if self._process_post(post):
                        total_sent += 1
                        time.sleep(2)
                except Exception as e:
                    logger.error(f"Error processing post {post.get('id', 'unknown')}: {e}")

            # Delay between JSON API requests to be polite
            time.sleep(5)

        logger.info(f"Check cycle complete: {total_posts} posts checked, {total_sent} interesting posts found")
        logger.info("=" * 80)

    def run_forever(self):
        """Run continuously with configured interval"""
        logger.info("Fragrance Scout starting...")
        logger.info(f"Monitoring: {', '.join(SUBREDDITS)}")
        logger.info(f"Check interval: {CHECK_INTERVAL} seconds ({CHECK_INTERVAL // 60} minutes)")
        logger.info(f"LLM endpoint: {LLM_URL}")
        logger.info(f"Tracking file: {TRACKING_FILE}")

        while True:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Error in check cycle: {e}")

            logger.info(f"Sleeping for {CHECK_INTERVAL} seconds...")
            time.sleep(CHECK_INTERVAL)


# Flask web UI
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>🌸 Fragrance Scout</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            color: #111;
            line-height: 1.6;
            margin: 0;
            padding: 0;
            background: #f8f9fa;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 32px;
        }
        .header {
            background: white;
            padding: 24px;
            border-radius: 8px;
            margin-bottom: 32px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .header h1 {
            margin: 0 0 8px 0;
            color: #4a90e2;
        }
        .header p {
            margin: 4px 0;
            color: #666;
        }
        .post {
            background: white;
            padding: 24px;
            border-radius: 8px;
            margin-bottom: 24px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .post-header {
            border-bottom: 2px solid #4a90e2;
            padding-bottom: 12px;
            margin-bottom: 16px;
        }
        .post-header h2 {
            margin: 0 0 8px 0;
            color: #333;
        }
        .post-meta {
            font-size: 0.9em;
            color: #666;
        }
        .post-link {
            color: #4a90e2;
            text-decoration: none;
            font-weight: 500;
        }
        .post-link:hover {
            text-decoration: underline;
        }
        .reason-box {
            background: #e3f2fd;
            border-left: 4px solid #4a90e2;
            padding: 16px;
            margin: 16px 0;
            border-radius: 0 8px 8px 0;
        }
        .reason-box h4 {
            margin: 0 0 8px 0;
            color: #4a90e2;
        }
        .body-box {
            background: #f8f9fa;
            padding: 16px;
            border-radius: 8px;
            margin: 16px 0;
            white-space: pre-wrap;
            max-height: 400px;
            overflow-y: auto;
        }
        .timestamp {
            font-size: 0.85em;
            color: #999;
        }
        .empty-state {
            text-align: center;
            padding: 64px 32px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .empty-state h3 {
            color: #666;
            margin-bottom: 8px;
        }
        .refresh-btn {
            display: inline-block;
            background: #4a90e2;
            color: white;
            padding: 10px 20px;
            border-radius: 6px;
            text-decoration: none;
            margin-top: 16px;
        }
        .refresh-btn:hover {
            background: #357abd;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🌸 Fragrance Scout</h1>
            <p><strong>Monitoring:</strong> r/perfumes, r/nicheperfumes</p>
            <p><strong>Posts found:</strong> {{ posts|length }}</p>
            <p class="timestamp">Last updated: {{ now }} UTC</p>
        </div>

        {% if posts %}
            {% for post in posts|reverse %}
            <div class="post">
                <div class="post-header">
                    <h2>{{ post.title }}</h2>
                    <div class="post-meta">
                        <strong>Author:</strong> u/{{ post.author }} •
                        <strong>Published:</strong> {{ post.published }} •
                        <a href="{{ post.link }}" target="_blank" class="post-link">Read on Reddit →</a>
                    </div>
                </div>

                <div class="reason-box">
                    <h4>Why it's interesting:</h4>
                    <p>{{ post.reason }}</p>
                </div>

                <div class="body-box">
                    <h4 style="margin-top:0;">Post Body:</h4>
                    {{ post.body }}
                </div>

                <div class="timestamp">Found: {{ post.timestamp }}</div>
            </div>
            {% endfor %}
        {% else %}
            <div class="empty-state">
                <h3>👀 Watching for interesting posts...</h3>
                <p>No posts found yet. The scout checks every 30 minutes.</p>
            </div>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    """Display found posts"""
    return render_template_string(
        HTML_TEMPLATE,
        posts=found_posts,
        now=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    )


@app.route('/scan')
def scan():
    """Endpoint for Cloud Scheduler to trigger scanning (requires authentication)"""
    # Verify auth token from header or query param
    auth_token = request.headers.get('X-Auth-Token') or request.args.get('token')

    if not SCAN_AUTH_TOKEN:
        logger.warning("SCAN_AUTH_TOKEN not configured - scan endpoint is unprotected!")
    elif auth_token != SCAN_AUTH_TOKEN:
        logger.warning(f"Unauthorized scan attempt from {request.remote_addr}")
        abort(401, description="Unauthorized")

    # Run scan asynchronously so we can return immediately
    def run_scan_async():
        scout = FragranceScout()
        scout.run_once()

    scan_thread = threading.Thread(target=run_scan_async, daemon=True)
    scan_thread.start()

    return {
        'status': 'success',
        'message': 'Scan started',
        'posts_found': len(found_posts)
    }


def run_scout_background():
    """Run the scout in a background thread"""
    scout = FragranceScout()
    scout.run_forever()


def load_posts_on_startup():
    """Load found posts from GCS on app startup"""
    global found_posts
    if GCS_BUCKET:
        try:
            storage_client = storage.Client()
            bucket = storage_client.bucket(GCS_BUCKET)
            blob = bucket.blob(POSTS_FILE)
            if blob.exists():
                posts_data = json.loads(blob.download_as_string())
                found_posts = posts_data.get("posts", [])
                logger.info(f"Loaded {len(found_posts)} posts from GCS on startup")
            else:
                logger.info("No found_posts.json in GCS yet")
        except Exception as e:
            logger.error(f"Error loading posts on startup: {e}")

def main():
    """Main entry point - runs web UI and scout in parallel"""
    logger.info("=" * 80)
    logger.info("Fragrance Scout starting")

    # Check if running in cloud mode (GCS_BUCKET set) or local mode
    if GCS_BUCKET:
        logger.info("Running in CLOUD MODE (Cloud Run)")
        logger.info(f"GCS Bucket: {GCS_BUCKET}")
        logger.info("Web UI will be available on Cloud Run URL")
        logger.info("/scan endpoint ready for Cloud Scheduler")

        # Load existing posts from GCS on startup
        load_posts_on_startup()

        # In cloud mode, just run the web UI (Cloud Scheduler will hit /scan)
        app.run(host='0.0.0.0', port=WEB_UI_PORT, debug=False)
    else:
        logger.info("Running in LOCAL MODE")
        logger.info(f"Web UI available at: http://127.0.0.1:{WEB_UI_PORT}")
        logger.info("=" * 80)

        # In local mode, start scout in background thread
        scout_thread = threading.Thread(target=run_scout_background, daemon=True)
        scout_thread.start()

        # Run Flask web UI
        app.run(host='127.0.0.1', port=WEB_UI_PORT, debug=False)


if __name__ == "__main__":
    main()
