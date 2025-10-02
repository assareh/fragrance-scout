variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "fragrance-scout"
}

variable "region" {
  description = "GCP region for resources"
  type        = string
  default     = "us-central1"
}

variable "container_image" {
  description = "Container image for Cloud Run (e.g., gcr.io/project-id/fragrance-scout:latest)"
  type        = string
}
