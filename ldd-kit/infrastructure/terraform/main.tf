# =============================================================================
# {{ SERVICE_NAME }} Telemetry Infrastructure — Terraform Root Configuration
# =============================================================================
# This module provisions GCP monitoring, logging, and tracing infrastructure
# for the {{ SERVICE_NAME }} application.
#
# Usage:
#   terraform init
#   terraform plan -var="project_id=YOUR_PROJECT" -var="alert_email=ops@example.com"
#   terraform apply
# =============================================================================

terraform {
  # ---------------------------------------------------------------------------
  # Version constraints
  # ---------------------------------------------------------------------------
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.0"
    }
  }

  # ---------------------------------------------------------------------------
  # Backend configuration (optional — configure via init or variables)
  # Example:
  #   terraform init \
  #     -backend-config="bucket=YOUR_TFSTATE_BUCKET" \
  #     -backend-config="prefix={{ SERVICE_NAME }}/monitoring"
  # ---------------------------------------------------------------------------
  backend "gcs" {
    # bucket and prefix provided via -backend-config at init time
    # or set enable_terraform_backends = true and configure below
  }
}

# =============================================================================
# Provider configuration
# =============================================================================
provider "google" {
  project = var.project_id
  region  = var.region

  default_labels = {
    environment = var.environment
    managed_by  = "terraform"
    service     = var.service_name
  }
}

provider "google-beta" {
  project = var.project_id
  region  = var.region

  default_labels = {
    environment = var.environment
    managed_by  = "terraform"
    service     = var.service_name
  }
}

# =============================================================================
# Local values
# =============================================================================
locals {
  # Default service account if none provided
  service_account_email = var.service_account_email != "" ? var.service_account_email : "${var.project_id}@appspot.gserviceaccount.com"
}
