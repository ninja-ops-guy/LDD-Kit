# =============================================================================
# Outputs — {{ SERVICE_NAME }} Telemetry Infrastructure
# =============================================================================
# These values are exposed after terraform apply for integration with
# downstream systems, runbooks, and documentation.
# =============================================================================

# ---------------------------------------------------------------------------
# Alert policies
# ---------------------------------------------------------------------------
output "alert_policy_names" {
  description = "Map of alert policy display names to their resource IDs."
  value = {
    high_latency         = google_monitoring_alert_policy.high_latency.display_name
    high_error_rate      = google_monitoring_alert_policy.high_error_rate.display_name
    resource_exhaustion  = google_monitoring_alert_policy.resource_exhaustion.display_name
    custom_metric        = var.custom_alert_enabled ? google_monitoring_alert_policy.custom_metric[0].display_name : null
  }
}

output "alert_policy_ids" {
  description = "Raw resource IDs of the created alert policies."
  value = {
    high_latency         = google_monitoring_alert_policy.high_latency.id
    high_error_rate      = google_monitoring_alert_policy.high_error_rate.id
    resource_exhaustion  = google_monitoring_alert_policy.resource_exhaustion.id
    custom_metric        = var.custom_alert_enabled ? google_monitoring_alert_policy.custom_metric[0].id : null
  }
}

output "alert_policy_conditions" {
  description = "Summary of alert conditions and their thresholds for runbook reference."
  value = {
    high_latency = {
      metric       = "p95 latency"
      threshold    = "${var.latency_threshold_ms}ms"
      duration     = var.alert_duration_high_latency
      severity     = "WARNING"
    }
    high_error_rate = {
      metric       = "5xx error rate"
      threshold    = "${var.error_rate_threshold * 100}%"
      duration     = var.alert_duration_error_rate
      severity     = "CRITICAL"
    }
    resource_exhaustion = {
      cpu_threshold    = "${var.cpu_threshold * 100}%"
      memory_threshold = "${var.memory_threshold * 100}%"
      duration         = var.alert_duration_resource
      severity         = "WARNING"
    }
  }
}

# ---------------------------------------------------------------------------
# Notification channels
# ---------------------------------------------------------------------------
output "notification_channel_ids" {
  description = "IDs of the created notification channels."
  value = compact([
    google_monitoring_notification_channel.email.id,
    var.pagerduty_integration_key != "" ? google_monitoring_notification_channel.pagerduty[0].id : "",
    var.slack_channel != "" ? google_monitoring_notification_channel.slack[0].id : ""
  ])
}

output "notification_channel_emails" {
  description = "Email addresses configured for notifications."
  value       = [google_monitoring_notification_channel.email.labels["email_address"]]
}

# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------
output "dashboard_urls" {
  description = "Direct URLs to Cloud Monitoring dashboards."
  value = {
    overview = "https://console.cloud.google.com/monitoring/dashboards/builder/${google_monitoring_dashboard.service_overview.dashboard_id}?project=${var.project_id}"
    pipeline = "https://console.cloud.google.com/monitoring/dashboards/builder/${google_monitoring_dashboard.service_pipeline.dashboard_id}?project=${var.project_id}"
  }
}

output "dashboard_ids" {
  description = "Dashboard resource IDs for programmatic reference."
  value = {
    overview = google_monitoring_dashboard.service_overview.dashboard_id
    pipeline = google_monitoring_dashboard.service_pipeline.dashboard_id
  }
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
output "log_sink_name" {
  description = "Name of the log sink created for service logs."
  value       = var.enable_log_sink ? google_logging_project_sink.service_logs[0].name : null
}

output "log_sink_destination" {
  description = "Destination of the log sink (BigQuery dataset)."
  value       = var.enable_log_sink ? google_logging_project_sink.service_logs[0].destination : null
}

output "log_sink_writer_identity" {
  description = "Service account used by the log sink to write to BigQuery."
  value       = var.enable_log_sink ? google_logging_project_sink.service_logs[0].writer_identity : null
}

output "log_based_metrics" {
  description = "Names of log-based metrics created."
  value = {
    request_duration     = google_logging_metric.request_duration.name
    business_event_count = google_logging_metric.business_event_count.name
    error_count          = google_logging_metric.error_count.name
  }
}

output "bigquery_dataset_id" {
  description = "BigQuery dataset ID for exported logs."
  value       = var.enable_log_sink ? google_bigquery_dataset.service_logs[0].dataset_id : null
}

output "bigquery_dataset_friendly_name" {
  description = "Human-readable name of the BigQuery dataset."
  value       = var.enable_log_sink ? google_bigquery_dataset.service_logs[0].friendly_name : null
}

# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------
output "trace_enabled_services" {
  description = "List of GCP service APIs enabled for tracing."
  value       = [google_project_service.cloudtrace.name]
}

output "trace_service_account_roles" {
  description = "IAM roles granted for Cloud Trace access."
  value = {
    agent = "roles/cloudtrace.agent"
    user  = "roles/cloudtrace.user"
  }
}

# ---------------------------------------------------------------------------
# Service metadata
# ---------------------------------------------------------------------------
output "service_name" {
  description = "Service name used for resource naming."
  value       = var.service_name
}

output "environment" {
  description = "Deployment environment."
  value       = var.environment
}

output "project_id" {
  description = "GCP project ID."
  value       = var.project_id
}

output "region" {
  description = "GCP region."
  value       = var.region
}
