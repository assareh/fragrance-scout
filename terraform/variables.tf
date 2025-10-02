variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "fragrance-scout"
}

variable "region" {
  description = "GCP region for resources"
  type        = string
  default     = "us-west1"
}

variable "container_image" {
  description = "Container image for Cloud Run (e.g., gcr.io/project-id/fragrance-scout:latest)"
  type        = string
  default     = "gcr.io/cloudrun/hello" # Use hello world image initially, will be replaced by Cloud Build
}

variable "github_owner" {
  description = "GitHub repository owner/organization"
  type        = string
  default     = "assareh"
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
  default     = "fragrance-scout"
}
