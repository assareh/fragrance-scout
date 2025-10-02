terraform {
  required_version = ">= 1.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# GCS bucket for storing tracking data and found posts
resource "google_storage_bucket" "fragrance_scout" {
  name                        = "${var.project_id}-fragrance-scout"
  location                    = var.region
  force_destroy               = false
  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.cloudresourcemanager]
}

# Secret for Gemini API key
resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "gemini-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager]
}

# Cloud Run service
# Note: Uncomment after the container image is built by Cloud Build
# resource "google_cloud_run_v2_service" "fragrance_scout" {
#   name               = "fragrance-scout"
#   location           = var.region
#   deletion_protection = false
#
#   template {
#     containers {
#       image = var.container_image
#
#       env {
#         name  = "USE_GEMINI"
#         value = "true"
#       }
#
#       env {
#         name  = "GCS_BUCKET"
#         value = google_storage_bucket.fragrance_scout.name
#       }
#
#       env {
#         name = "GEMINI_API_KEY"
#         value_source {
#           secret_key_ref {
#             secret  = google_secret_manager_secret.gemini_api_key.secret_id
#             version = "latest"
#           }
#         }
#       }
#
#       resources {
#         limits = {
#           cpu    = "1"
#           memory = "512Mi"
#         }
#       }
#     }
#
#     service_account = google_service_account.fragrance_scout.email
#
#     max_instance_request_concurrency = 10
#     timeout                          = "300s"
#
#     scaling {
#       min_instance_count = 0
#       max_instance_count = 1
#     }
#   }
#
#   traffic {
#     percent = 100
#     type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
#   }
#
#   depends_on = [google_project_service.run]
# }

# Service account for Cloud Run
resource "google_service_account" "fragrance_scout" {
  account_id   = "fragrance-scout"
  display_name = "Fragrance Scout Service Account"

  depends_on = [google_project_service.iam]
}

# IAM binding for GCS bucket access
# resource "google_storage_bucket_iam_member" "fragrance_scout_storage" {
#   bucket = google_storage_bucket.fragrance_scout.name
#   role   = "roles/storage.objectAdmin"
#   member = "serviceAccount:${google_service_account.fragrance_scout.email}"
# }

# IAM binding for Secret Manager access
# resource "google_secret_manager_secret_iam_member" "fragrance_scout_secret" {
#   secret_id = google_secret_manager_secret.gemini_api_key.secret_id
#   role      = "roles/secretmanager.secretAccessor"
#   member    = "serviceAccount:${google_service_account.fragrance_scout.email}"
# }

# Allow unauthenticated access to Cloud Run service
# resource "google_cloud_run_v2_service_iam_member" "public_access" {
#   location = google_cloud_run_v2_service.fragrance_scout.location
#   name     = google_cloud_run_v2_service.fragrance_scout.name
#   role     = "roles/run.invoker"
#   member   = "allUsers"
# }

# Cloud Scheduler job to trigger scanning every 30 minutes
# resource "google_cloud_scheduler_job" "fragrance_scout_scan" {
#   name             = "fragrance-scout-scan"
#   description      = "Trigger fragrance scout scanning every 30 minutes"
#   schedule         = "*/30 * * * *"
#   time_zone        = "UTC"
#   attempt_deadline = "320s"
#
#   http_target {
#     http_method = "GET"
#     uri         = "${google_cloud_run_v2_service.fragrance_scout.uri}/scan"
#
#     oidc_token {
#       service_account_email = google_service_account.scheduler.email
#     }
#   }
#
#   depends_on = [google_project_service.scheduler]
# }

# Service account for Cloud Scheduler
resource "google_service_account" "scheduler" {
  account_id   = "fragrance-scout-scheduler"
  display_name = "Fragrance Scout Scheduler Service Account"

  depends_on = [google_project_service.iam]
}

# IAM binding for Cloud Scheduler to invoke Cloud Run
# resource "google_cloud_run_v2_service_iam_member" "scheduler_invoker" {
#   location = google_cloud_run_v2_service.fragrance_scout.location
#   name     = google_cloud_run_v2_service.fragrance_scout.name
#   role     = "roles/run.invoker"
#   member   = "serviceAccount:${google_service_account.scheduler.email}"
# }

# Enable required APIs
resource "google_project_service" "cloudresourcemanager" {
  service            = "cloudresourcemanager.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "run" {
  service            = "run.googleapis.com"
  disable_on_destroy = false
  depends_on         = [google_project_service.cloudresourcemanager]
}

resource "google_project_service" "scheduler" {
  service            = "cloudscheduler.googleapis.com"
  disable_on_destroy = false
  depends_on         = [google_project_service.cloudresourcemanager]
}

resource "google_project_service" "secretmanager" {
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
  depends_on         = [google_project_service.cloudresourcemanager]
}

resource "google_project_service" "cloudbuild" {
  service            = "cloudbuild.googleapis.com"
  disable_on_destroy = false
  depends_on         = [google_project_service.cloudresourcemanager]
}


resource "google_project_service" "iam" {
  service            = "iam.googleapis.com"
  disable_on_destroy = false
  depends_on         = [google_project_service.cloudresourcemanager]
}

# Cloud Build trigger for GitHub
resource "google_cloudbuild_trigger" "github_push" {
  name        = "fragrance-scout-github-push"
  description = "Build and deploy on push to main branch"
  location    = var.region

  repository_event_config {
    repository = "projects/${var.project_id}/locations/${var.region}/connections/github/repositories/${var.github_repo}"
    push {
      branch = "^main$"
    }
  }

  build {
    step {
      name = "gcr.io/cloud-builders/docker"
      args = ["build", "-t", "gcr.io/$PROJECT_ID/fragrance-scout:$COMMIT_SHA", "-t", "gcr.io/$PROJECT_ID/fragrance-scout:latest", "."]
    }
    step {
      name = "gcr.io/cloud-builders/docker"
      args = ["push", "gcr.io/$PROJECT_ID/fragrance-scout:$COMMIT_SHA"]
    }
    step {
      name = "gcr.io/cloud-builders/docker"
      args = ["push", "gcr.io/$PROJECT_ID/fragrance-scout:latest"]
    }
    step {
      name       = "gcr.io/google.com/cloudsdktool/cloud-sdk"
      entrypoint = "gcloud"
      args       = ["run", "services", "update", "fragrance-scout", "--image=gcr.io/$PROJECT_ID/fragrance-scout:$COMMIT_SHA", "--region=${var.region}", "--platform=managed"]
    }
    images = ["gcr.io/$PROJECT_ID/fragrance-scout:$COMMIT_SHA", "gcr.io/$PROJECT_ID/fragrance-scout:latest"]
  }

  depends_on = [
    google_project_service.cloudbuild,
    google_project_iam_member.cloudbuild_run_admin,
    google_project_iam_member.cloudbuild_sa_user
  ]
}

# Grant Cloud Build service account permissions
resource "google_project_iam_member" "cloudbuild_run_admin" {
  project    = var.project_id
  role       = "roles/run.admin"
  member     = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
  depends_on = [google_project_service.cloudbuild]
}

resource "google_project_iam_member" "cloudbuild_sa_user" {
  project    = var.project_id
  role       = "roles/iam.serviceAccountUser"
  member     = "serviceAccount:${data.google_project.project.number}@cloudbuild.gserviceaccount.com"
  depends_on = [google_project_service.cloudbuild]
}

# Get project data
data "google_project" "project" {
  project_id = var.project_id
}

# Outputs
# output "cloud_run_url" {
#   value       = google_cloud_run_v2_service.fragrance_scout.uri
#   description = "URL of the Cloud Run service"
# }

output "gcs_bucket_name" {
  value       = google_storage_bucket.fragrance_scout.name
  description = "Name of the GCS bucket"
}

# output "scheduler_job_name" {
#   value       = google_cloud_scheduler_job.fragrance_scout_scan.name
#   description = "Name of the Cloud Scheduler job"
# }
