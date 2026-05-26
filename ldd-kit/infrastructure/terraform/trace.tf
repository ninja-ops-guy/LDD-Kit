# =============================================================================
# Cloud Trace Configuration — {{ SERVICE_NAME }}
# =============================================================================
# This file configures GCP Cloud Trace for distributed tracing:
#   - google_project_service: Enables the cloudtrace.googleapis.com API
#   - google_project_iam_member: Grants trace write/read permissions
#
# Application-level trace instrumentation is handled in code via
# OpenTelemetry SDK and OTLP exporter. This Terraform ensures the
# backend API is enabled and properly configured.
#
# Trace sampling rate is configured via environment variables on the
# deployment platform, NOT via Terraform (allows per-environment tuning).
# =============================================================================

# =============================================================================
# API enablement: Cloud Trace
# =============================================================================
resource "google_project_service" "cloudtrace" {
  service            = "cloudtrace.googleapis.com"
  disable_on_destroy = false

  timeouts {
    create = "10m"
    update = "10m"
  }
}

# =============================================================================
# IAM: Grant Cloud Trace Agent role to service account
# =============================================================================
# Allows the service to write trace spans directly to Cloud Trace
# without needing service account keys.
resource "google_project_iam_member" "cloudtrace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${var.service_account_email}"

  depends_on = [google_project_service.cloudtrace]
}

# =============================================================================
# IAM: Grant Cloud Trace User role for read access in dashboards
# =============================================================================
resource "google_project_iam_member" "cloudtrace_user" {
  project = var.project_id
  role    = "roles/cloudtrace.user"
  member  = "serviceAccount:${var.service_account_email}"

  depends_on = [google_project_service.cloudtrace]
}

# =============================================================================
# Note: Trace Sampling Rate Configuration
# =============================================================================
# Trace sampling rate is configured via environment variables on the
# deployment platform, NOT via Terraform. This allows per-environment
# tuning without infrastructure changes.
#
# Environment variables for production (example):
#   OTEL_TRACES_SAMPLER             = "parentbased_traceidratio"
#   OTEL_TRACES_SAMPLER_ARG         = "0.1"          # 10% in production
#   OTEL_SERVICE_NAME               = "{{ SERVICE_NAME }}"
#   OTEL_EXPORTER_OTLP_ENDPOINT     = "https://telemetry.googleapis.com"
#
# For development (100% sampling):
#   OTEL_TRACES_SAMPLER             = "always_on"
#   OTEL_TRACES_SAMPLER_ARG         = "1.0"
#
# For CI / testing (100% sampling):
#   OTEL_TRACES_SAMPLER             = "always_on"
#   OTEL_TRACES_SAMPLER_ARG         = "1.0"
#
# See README.md for full configuration reference.

# =============================================================================
# Note: Trace Retention
# =============================================================================
# Cloud Trace automatically retains trace data for 30 days.
# There is no Terraform resource to configure retention — it is a
# platform-managed setting. For longer retention, export traces to
# Cloud Storage or BigQuery using Cloud Monitoring trace sinks.
#
# To create a manual export pipeline:
#   1. Create a Cloud Storage bucket for trace archives
#   2. Set up a Cloud Monitoring sink with a trace filter
#   3. Configure retention policies on the bucket

# =============================================================================
# Optional: Cloud Monitoring trace sink for long-term retention
# =============================================================================
resource "google_monitoring_generic_service" "trace_service" {
  count = var.enable_trace_sink ? 1 : 0

  project      = var.project_id
  service_id   = "${var.service_name}-${var.environment}-traces"
  display_name = "${title(var.service_name)} ${var.environment} Traces"

  basic_service {
    service_type  = "CLOUD_RUN"
    service_labels = {
      service_name     = var.service_name
      location         = var.region
      revision_name    = "${var.service_name}-${var.environment}"
      configuration_name = var.service_name
    }
  }
}
