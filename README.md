# Fragrance Scout

A self-contained Python application that monitors Reddit subreddits for interesting niche perfume reviews, filters them using an LLM, and displays them in a web UI.

## Features

- **Reddit API Monitoring**: Uses Reddit JSON API with OAuth to poll r/perfumes and r/nicheperfumes hourly
- **LLM Filtering**: Uses Gemini 2.5 Flash (cloud) or local LLM (dev) to identify substantive niche/indie perfume reviews
- **Web UI**: Dashboard showing interesting posts with dark theme support, user profile hover cards, and flair badges
- **Duplicate Prevention**: Tracks sent posts in GCS or local JSON to avoid re-processing
- **Self-Contained**: Minimal dependencies, easy to port to any environment
- **Cloud-Ready**: Deploys to Cloud Run with Cloud Scheduler for automated scanning

## Requirements

### Local Development
- Python 3.9+
- Local LLM server running at `http://127.0.0.1:1234` (e.g., LM Studio with qwen/qwen3-4b-thinking-2507)

### Cloud Deployment
- GCP project with billing enabled
- Gemini API key
- Terraform installed

## Quick Start - Local Development

### 1. Install Dependencies

```bash
cd fragrance_scout
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Start Local LLM

Ensure your local LLM server is running at `http://127.0.0.1:1234` with the model `qwen/qwen3-4b-thinking-2507` loaded.

Example using LM Studio:
- Load the Qwen 3 4B Thinking model
- Start the local server on port 1234

### 3. Run the Scout

```bash
python3 fragrance_scout.py
```

### 4. Open Web UI

Open your browser to **http://127.0.0.1:5002**

The web UI will:
- Show interesting posts as they're found
- Display full post content with subreddit links, author hover cards, and flair badges
- Support light/dark themes (system default, or manual selection)
- Work without any GCP configuration

In local mode:
- Runs background thread to check Reddit JSON API every 30 minutes
- Stores tracking data in local `sent_posts.json`
- Stores found posts in memory

## Cloud Deployment

Deploy to Google Cloud Run with automated scanning via Cloud Scheduler.

### Prerequisites

1. **GCP Project Setup**:
```bash
gcloud config set project fragrance-scout
gcloud auth application-default login
```

2. **Get Gemini API Key**:
- Visit [Google AI Studio](https://aistudio.google.com/app/apikey)
- Create an API key
- Save it for the next step

3. **Store Gemini API Key**:
```bash
echo -n "YOUR_GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=-
```

### Deploy with Terraform

1. **Build and push container**:
```bash
# Build the container
docker build -t gcr.io/fragrance-scout/fragrance-scout:latest .

# Push to GCR
docker push gcr.io/fragrance-scout/fragrance-scout:latest
```

2. **Configure Terraform**:
```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
```

3. **Deploy infrastructure**:
```bash
terraform init
terraform plan
terraform apply
```

This creates:
- **GCS Bucket**: Stores tracking data and found posts
- **Cloud Run Service**: Runs the web UI and /scan endpoint with health probes
- **Cloud Scheduler**: Triggers scanning every hour
- **Service Accounts**: With appropriate IAM permissions
- **Secret Manager**: Stores Gemini API key and Reddit OAuth credentials

4. **Get the Cloud Run URL**:
```bash
terraform output cloud_run_url
```

### Cloud Architecture

In cloud mode:
- **Web UI**: Displays found posts at the Cloud Run URL with theme support
- **Scanning**: Cloud Scheduler hits `/scan` endpoint every hour
- **Storage**: GCS bucket stores `sent_posts.json` and `found_posts.json`
- **LLM**: Uses Gemini 2.5 Flash API (no local LLM needed)
- **Scaling**: Scales to zero when idle with startup/liveness probes (cost-effective)

## Configuration

Configuration is controlled via environment variables:

### Local Development
```bash
# No environment variables needed for local mode
# Uses local LLM at http://127.0.0.1:1234
python3 fragrance_scout.py
```

### Cloud Deployment (set by Terraform)
```bash
USE_GEMINI=true              # Use Gemini API instead of local LLM
GEMINI_API_KEY=<key>        # Gemini API key (from Secret Manager)
GCS_BUCKET=<bucket-name>    # GCS bucket for storage
PORT=8080                    # Cloud Run port
```

### Code Configuration
In `fragrance_scout.py`:
```python
# Subreddits to monitor (Reddit JSON API endpoints)
SUBREDDITS = ["perfumes", "nicheperfumes"]

# Check interval (30 minutes for local dev)
CHECK_INTERVAL = 30 * 60

# Cloud deployment runs hourly via Cloud Scheduler
```

## Running Locally in Background (Optional)

For local development, you can run in the background:

### Option 1: Using screen/tmux

```bash
screen -S fragrance-scout
source venv/bin/activate
python3 fragrance_scout.py
# Press Ctrl+A, then D to detach
```

### Option 2: Using nohup

```bash
nohup python3 fragrance_scout.py > output.log 2>&1 &
```

**Note**: For production, use the cloud deployment instead of running locally in the background.

## LLM Filtering Logic

The LLM evaluates posts based on:

**Interesting (✅):**
- Niche/indie brands (Nishane, Xerjoff, Amouage, Zoologist, etc.)
- Detailed reviews with scent notes, impressions
- Wear tests, batch comparisons, note breakdowns

**Ignored (❌):**
- Designer brands (Dior, Boss, Chanel, etc.)
- Simple mentions without substance
- Generic recommendation requests

## File Structure

```
fragrance_scout/
├── fragrance_scout.py       # Main application
├── requirements.txt         # Python dependencies
├── Dockerfile              # Container definition for Cloud Run
├── README.md               # This file
├── .gitignore              # Git ignore rules
├── terraform/              # Infrastructure as code
│   ├── main.tf            # Main Terraform configuration
│   ├── variables.tf       # Variable definitions
│   └── terraform.tfvars.example  # Example values
├── sent_posts.json         # Tracking file (local mode, auto-created)
└── fragrance_scout.log     # Log file (local mode, auto-created)
```

## Logs

### Local Mode
Activity is logged to both console and `fragrance_scout.log`:

```
2025-10-02 15:30:00 - INFO - Starting fragrance scout check cycle
2025-10-02 15:30:05 - INFO - Fetching RSS feed: https://www.reddit.com/r/perfumes/new/.rss
2025-10-02 15:30:07 - INFO - Found 20 posts in feed
2025-10-02 15:30:10 - INFO - ✨ INTERESTING POST FOUND: Nishane Ani Review...
2025-10-02 15:30:12 - INFO - 📱 Added to results
```

### Cloud Mode
Logs are available in Google Cloud Logging:
```bash
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=fragrance-scout" --limit 50
```

## Troubleshooting

### Local Development

**LLM connection failed:**
- Ensure LM Studio (or other LLM server) is running on port 1234
- Test with: `curl http://127.0.0.1:1234/v1/models`

**No posts found:**
- Check RSS feed manually: `curl https://www.reddit.com/r/perfumes/new/.rss`
- Verify subreddit names are correct

### Cloud Deployment

**Cloud Run service not starting:**
- Check Cloud Run logs: `gcloud run services logs read fragrance-scout`
- Verify Gemini API key is set: `gcloud secrets versions access latest --secret=gemini-api-key`

**Scheduler not triggering:**
- Check scheduler status: `gcloud scheduler jobs describe fragrance-scout-scan`
- Verify service account has run.invoker permission

**Storage errors:**
- Check GCS bucket exists: `gcloud storage buckets list | grep fragrance-scout`
- Verify service account has storage.objectAdmin permission

## Maintenance Notes

### TODO: Remove Legacy Timestamp Conversion (After ~1 Week)

**Location**: `fragrance_scout.py` in the `index()` function (around line 714-731)

The code currently includes runtime conversion for posts with old epoch timestamp format:
```python
# Convert old epoch timestamps to readable format for display
posts_display = []
for post in found_posts:
    post_copy = post.copy()
    published = post_copy.get('published', '')
    # ... conversion logic ...
```

**When to remove**: After all existing posts in GCS have cycled out (posts older than 90 days are auto-deleted per GCS lifecycle rules, but realistically after ~1 week all displayed posts should have the new format).

**How to verify**: Check the web UI - if all "Published" timestamps show as "October 02, 2025 at 11:42 AM PT" format (not epoch like "1759326944.0"), it's safe to remove.

**What to do**: Replace the `index()` function with the simpler version:
```python
@app.route('/')
def index():
    """Display found posts"""
    pacific_time = datetime.now(ZoneInfo("America/Los_Angeles"))
    return render_template_string(
        HTML_TEMPLATE,
        posts=found_posts,
        now=pacific_time.strftime('%B %d, %Y at %I:%M %p PT')
    )
```

## License

MIT
