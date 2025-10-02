# Deployment Guide - Fragrance Scout

Quick guide for deploying Fragrance Scout to Google Cloud Run.

## Prerequisites

1. **GCP Project**:
   - Create a new GCP project or use existing
   - Enable billing
   - Install gcloud CLI

2. **Tools**:
   - Docker installed locally
   - Terraform installed
   - gcloud CLI configured

## Step-by-Step Deployment

### 1. Configure GCP

```bash
# Set your project
export PROJECT_ID="fragrance-scout"
gcloud config set project $PROJECT_ID

# Authenticate
gcloud auth login
gcloud auth application-default login

# Enable required APIs
gcloud services enable run.googleapis.com
gcloud services enable cloudscheduler.googleapis.com
gcloud services enable secretmanager.googleapis.com
gcloud services enable cloudbuild.googleapis.com
```

### 2. Get Gemini API Key

1. Visit [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Create an API key
3. Store it in Secret Manager:

```bash
echo -n "YOUR_GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=-
```

### 3. Build and Push Container

```bash
# Configure Docker for GCR
gcloud auth configure-docker

# Build the container
docker build -t gcr.io/$PROJECT_ID/fragrance-scout:latest .

# Push to GCR
docker push gcr.io/$PROJECT_ID/fragrance-scout:latest
```

### 4. Deploy with Terraform

```bash
cd terraform

# Copy and edit variables
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars:
#   - Set project_id to your GCP project
#   - Set container_image to gcr.io/YOUR_PROJECT/fragrance-scout:latest

# Initialize Terraform
terraform init

# Review the plan
terraform plan

# Deploy
terraform apply
```

### 5. Verify Deployment

```bash
# Get the Cloud Run URL
terraform output cloud_run_url

# Test the web UI
open $(terraform output -raw cloud_run_url)

# Manually trigger a scan (optional)
curl "$(terraform output -raw cloud_run_url)/scan"
```

### 6. Monitor

```bash
# View Cloud Run logs
gcloud run services logs read fragrance-scout --limit=50

# View Cloud Scheduler job
gcloud scheduler jobs describe fragrance-scout-scan

# Check GCS bucket
gcloud storage ls gs://$(terraform output -raw gcs_bucket_name)/
```

## Architecture

The deployment creates:

1. **Cloud Run Service**:
   - Web UI at `/` showing found posts
   - `/scan` endpoint for scheduled scanning
   - Scales to zero when idle
   - Auto-scales with traffic

2. **Cloud Scheduler**:
   - Triggers `/scan` every 30 minutes
   - Uses OIDC authentication

3. **GCS Bucket**:
   - Stores `sent_posts.json` (tracking)
   - Stores `found_posts.json` (results)
   - 90-day retention policy

4. **Service Accounts**:
   - Cloud Run service account (storage + secret access)
   - Scheduler service account (Cloud Run invoker)

## Cost Estimates

Approximate monthly costs:

- **Cloud Run**: ~$0-5 (minimal CPU/memory, scales to zero)
- **Cloud Scheduler**: ~$0.10 (1440 jobs/month)
- **GCS Storage**: ~$0.01 (minimal storage)
- **Gemini API**: ~$0-10 (depends on usage, Flash tier is cheap)

**Total**: ~$0.11-$15/month

## Updating the Deployment

### Update Code

```bash
# Make changes to fragrance_scout.py

# Rebuild and push
docker build -t gcr.io/$PROJECT_ID/fragrance-scout:latest .
docker push gcr.io/$PROJECT_ID/fragrance-scout:latest

# Force Cloud Run to use new image
gcloud run services update fragrance-scout \
  --image gcr.io/$PROJECT_ID/fragrance-scout:latest \
  --region us-central1
```

### Update Infrastructure

```bash
cd terraform

# Make changes to *.tf files

# Apply changes
terraform plan
terraform apply
```

## Cleanup

To remove all resources:

```bash
cd terraform
terraform destroy

# Delete the secret
gcloud secrets delete gemini-api-key

# Delete container images
gcloud container images delete gcr.io/$PROJECT_ID/fragrance-scout:latest
```

## Troubleshooting

### Cloud Run not starting

```bash
# Check logs
gcloud run services logs read fragrance-scout --limit=100

# Common issues:
# 1. Gemini API key not set or invalid
# 2. Service account lacks permissions
# 3. Container image not found
```

### Scheduler not triggering

```bash
# Check scheduler job status
gcloud scheduler jobs describe fragrance-scout-scan

# View execution history
gcloud scheduler jobs describe fragrance-scout-scan --format="value(status)"

# Manually trigger
gcloud scheduler jobs run fragrance-scout-scan
```

### GCS permission errors

```bash
# Verify service account has access
gcloud storage buckets get-iam-policy gs://$(terraform output -raw gcs_bucket_name)

# Should show Cloud Run service account with storage.objectAdmin role
```

## Security Notes

- Cloud Run service is publicly accessible (for web UI)
- `/scan` endpoint is also public (no sensitive data exposed)
- Gemini API key is stored in Secret Manager
- Service accounts follow least-privilege principle
- GCS bucket has uniform access control
