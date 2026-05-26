# =============================================================================
# Logging Infrastructure — {{ SERVICE_NAME }}
# =============================================================================
# This file defines:
#   - google_project_service: Enables the logging.googleapis.com API
#   - google_logging_metric: Custom log-based metrics derived from structured logs
#   - google_logging_project_sink: Log routing to BigQuery for analysis
#   - google_bigquery_dataset: Destination for log sink exports
#
# Log-based metrics extract telemetry data from structured JSON logs,
# enabling monitoring without code-level metric instrumentation changes.
#
# All resources use var.service_name for generic adaptation across services.
# =============================================================================

# =============================================================================
# API enablement
# =============================================================================
resource "google_project_service" "logging" {
  service            = "logging.googleapis.com"
  disable_on_destroy = false

  timeouts {
    create = "10m"
    update = "10m"
  }
}

# =============================================================================
# Log-based Metric: Request Duration (from structured logs)
# =============================================================================
# Extracts request duration (in milliseconds) from structured log entries.
# Assumes logs contain a "duration_ms" field in the jsonPayload.
resource "google_logging_metric" "request_duration" {
  name   = "${var.service_name}/${var.environment}/request_duration"
  filter = <<-EOT
    resource.type="${var.cloud_run_service ? "cloud_run_revision" : "global"}"
    jsonPayload.event_type="HttpRequestCompleted"
    jsonPayload.duration_ms>0
    ${var.cloud_run_service ? "resource.labels.service_name=\"${var.service_name}\"" : ""}
  EOT
  project = var.project_id

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "DISTRIBUTION"
    unit         = "ms"
    description  = "Distribution of request durations in milliseconds for ${var.service_name} ${var.environment}."
    display_name = "${title(var.service_name)} Request Duration"

    labels {
      key         = "status"
      value_type  = "STRING"
      description = "HTTP response status code class: 2xx, 3xx, 4xx, 5xx"
    }

    labels {
      key         = "method"
      value_type  = "STRING"
      description = "HTTP method: GET, POST, PUT, DELETE, etc."
    }

    labels {
      key         = "path"
      value_type  = "STRING"
      description = "Request path pattern"
    }
  }

  value_extractor = "EXTRACT(jsonPayload.duration_ms)"

  label_extractors = {
    "status" = "EXTRACT(jsonPayload.status_code)"
    "method" = "EXTRACT(jsonPayload.method)"
    "path"   = "EXTRACT(jsonPayload.path)"
  }

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 64
      growth_factor      = 2
      scale              = 1.0
    }
  }

  depends_on = [google_project_service.logging]
}

# =============================================================================
# Log-based Metric: Business Event Count
# =============================================================================
# Counts business events (e.g., user.created, payment.processed).
# Labels capture event domain and outcome for dimensional analysis.
resource "google_logging_metric" "business_event_count" {
  name   = "${var.service_name}/${var.environment}/business_event_count"
  filter = <<-EOT
    resource.type="${var.cloud_run_service ? "cloud_run_revision" : "global"}"
    jsonPayload.event_domain!=""
    jsonPayload.event_type!=""
    ${var.cloud_run_service ? "resource.labels.service_name=\"${var.service_name}\"" : ""}
  EOT
  project = var.project_id

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    unit         = "1"
    description  = "Count of business events for ${var.service_name} ${var.environment}."
    display_name = "${title(var.service_name)} Business Events"

    labels {
      key         = "domain"
      value_type  = "STRING"
      description = "Business domain: user, payment, order, etc."
    }

    labels {
      key         = "event"
      value_type  = "STRING"
      description = "Event type: user.created, payment.failed, etc."
    }

    labels {
      key         = "outcome"
      value_type  = "STRING"
      description = "Event outcome: success, failure, timeout"
    }
  }

  value_extractor = "1"

  label_extractors = {
    "domain"  = "EXTRACT(jsonPayload.event_domain)"
    "event"   = "EXTRACT(jsonPayload.event_type)"
    "outcome" = "EXTRACT(jsonPayload.outcome)"
  }

  depends_on = [google_project_service.logging]
}

# =============================================================================
# Log-based Metric: Error Count
# =============================================================================
# Counts error-level log entries for rapid alerting on application errors.
resource "google_logging_metric" "error_count" {
  name   = "${var.service_name}/${var.environment}/error_count"
  filter = <<-EOT
    resource.type="${var.cloud_run_service ? "cloud_run_revision" : "global"}"
    severity>=ERROR
    ${var.cloud_run_service ? "resource.labels.service_name=\"${var.service_name}\"" : ""}
  EOT
  project = var.project_id

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    unit         = "1"
    description  = "Count of error-level log entries for ${var.service_name} ${var.environment}."
    display_name = "${title(var.service_name)} Error Count"

    labels {
      key         = "error_type"
      value_type  = "STRING"
      description = "Error classification: exception, timeout, validation, etc."
    }
  }

  value_extractor = "1"

  label_extractors = {
    "error_type" = "EXTRACT(jsonPayload.error_type)"
  }

  depends_on = [google_project_service.logging]
}

# =============================================================================
# Log Router Sink: Export service logs to BigQuery for analysis
# =============================================================================
# Creates a project-level log sink that routes all service logs
# to a BigQuery dataset for long-term storage and SQL-based analysis.
resource "google_logging_project_sink" "service_logs" {
  count       = var.enable_log_sink ? 1 : 0
  name        = "${var.service_name}-${var.environment}-logs"
  project     = var.project_id
  destination = "bigquery.googleapis.com/projects/${var.project_id}/datasets/${var.service_name}_logs_${var.environment}"

  # Filter to capture only service logs
  filter = <<-EOT
    resource.type="${var.cloud_run_service ? "cloud_run_revision" : "global"}"
    ${var.cloud_run_service ? "resource.labels.service_name=\"${var.service_name}\"" : "labels.service_name=\"${var.service_name}\""}
  EOT

  bigquery_options {
    use_partitioned_tables = true
  }

  unique_writer_identity = true

  depends_on = [google_project_service.logging]
}

# =============================================================================
# IAM binding: Grant BigQuery Data Editor role to the log sink's writer identity
# =============================================================================
resource "google_project_iam_member" "log_sink_bigquery" {
  count   = var.enable_log_sink ? 1 : 0
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = google_logging_project_sink.service_logs[0].writer_identity

  depends_on = [
    google_logging_project_sink.service_logs,
  ]
}

# =============================================================================
# BigQuery Dataset: Destination for log sink
# =============================================================================
# This dataset stores routed logs for long-term analysis, audit trails,
# and compliance reporting. Partitioned tables are used for cost efficiency.
resource "google_bigquery_dataset" "service_logs" {
  count         = var.enable_log_sink ? 1 : 0
  dataset_id    = "${replace(var.service_name, "-", "_")}_logs_${var.environment}"
  project       = var.project_id
  friendly_name = "${title(var.service_name)} Logs — ${var.environment}"
  description   = "BigQuery dataset for ${var.service_name} ${var.environment} structured logs exported via Cloud Logging sink."
  location      = var.region

  default_partition_expiration_ms = var.log_retention_days * 86400000 # days to ms

  labels = {
    environment = var.environment
    managed_by  = "terraform"
    service     = var.service_name
  }

  access {
    role          = "OWNER"
    special_group = "projectOwners"
  }

  access {
    role          = "READER"
    special_group = "projectReaders"
  }

  access {
    role          = "WRITER"
    special_group = "projectWriters"
  }

  # Grant access to the log sink writer identity
  dynamic "access" {
    for_each = var.enable_log_sink ? [1] : []
    content {
      role   = "WRITER"
      user_by_email = replace(
        google_logging_project_sink.service_logs[0].writer_identity,
        "serviceAccount:",
        ""
      )
    }
  }

  depends_on = [
    google_project_service.logging,
    google_logging_project_sink.service_logs,
  ]

  lifecycle {
    prevent_destroy = var.environment == "production"
  }
}

# =============================================================================
# Log exclusion filter: Reduce noise and cost
# =============================================================================
# Excludes high-volume, low-signal logs (health checks, static assets)
# to reduce logging costs. Only created in production environments.
resource "google_logging_project_exclusion" "noise_reduction" {
  count   = var.environment == "production" ? 1 : 0
  name    = "${var.service_name}-${var.environment}-noise-reduction"
  project = var.project_id

  description = "Exclude high-volume low-signal logs for ${var.service_name} ${var.environment}"

  filter = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${var.service_name}"
    (
      jsonPayload.path="/health" OR
      jsonPayload.path="/ready" OR
      jsonPayload.path="/metrics" OR
      (jsonPayload.path="/static/" AND jsonPayload.status_code=200) OR
      (jsonPayload.event_type="HttpRequestCompleted" AND jsonPayload.duration_ms<5)
    )
  EOT
}
