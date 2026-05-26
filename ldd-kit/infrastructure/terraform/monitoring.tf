# =============================================================================
# Monitoring Infrastructure — {{ SERVICE_NAME }}
# =============================================================================
# This file defines:
#   - google_project_service: Enables required GCP monitoring APIs
#   - google_monitoring_notification_channel: Email alert destinations
#   - google_monitoring_alert_policy: Metric threshold alert policies
#   - google_monitoring_dashboard: Cloud Monitoring dashboards
#
# All alert policies use OR combiner logic — any single condition breach
# triggers a notification. This follows Google's SRE best practices for
# fast incident detection.
#
# This is a generic template. The adapt.py script replaces:
#   - {{ SERVICE_NAME }} with your service name
#   - Metric names and filters are adapted from config.yaml events
# =============================================================================

# =============================================================================
# API enablement
# =============================================================================
resource "google_project_service" "monitoring" {
  service            = "monitoring.googleapis.com"
  disable_on_destroy = false

  timeouts {
    create = "10m"
    update = "10m"
  }
}

# =============================================================================
# Notification channels
# =============================================================================
resource "google_monitoring_notification_channel" "email" {
  display_name = "${var.service_name} Alerts — ${var.alert_email}"
  type         = "email"
  project      = var.project_id

  labels = {
    email_address = var.alert_email
  }

  description = "Primary email notification channel for ${var.service_name} ${var.environment} alerts."

  depends_on = [google_project_service.monitoring]

  lifecycle {
    prevent_destroy = false
  }
}

resource "google_monitoring_notification_channel" "pagerduty" {
  count = var.pagerduty_integration_key != "" ? 1 : 0

  display_name = "${var.service_name} PagerDuty — ${var.environment}"
  type         = "pagerduty"
  project      = var.project_id

  labels = {
    service_key = var.pagerduty_integration_key
  }

  description = "PagerDuty notification channel for ${var.service_name} ${var.environment} critical alerts."

  depends_on = [google_project_service.monitoring]
}

resource "google_monitoring_notification_channel" "slack" {
  count = var.slack_channel != "" ? 1 : 0

  display_name = "${var.service_name} Slack — ${var.slack_channel}"
  type         = "slack"
  project      = var.project_id

  labels = {
    channel_name = var.slack_channel
  }

  sensitive_labels {
    auth_token = var.slack_auth_token
  }

  description = "Slack notification channel for ${var.service_name} ${var.environment} alerts."

  depends_on = [google_project_service.monitoring]
}

locals {
  notification_channels = concat(
    [google_monitoring_notification_channel.email.id],
    var.pagerduty_integration_key != "" ? [google_monitoring_notification_channel.pagerduty[0].id] : [],
    var.slack_channel != "" ? [google_monitoring_notification_channel.slack[0].id] : []
  )
}

# =============================================================================
# Alert Policy: High Latency (p95 > threshold)
# =============================================================================
resource "google_monitoring_alert_policy" "high_latency" {
  display_name = "${var.service_name}-${var.environment}-high-latency"
  project      = var.project_id
  combiner     = "OR"

  documentation {
    content   = <<-EOT
      ## ${title(var.service_name)} High Latency Alert

      This alert fires when the 95th percentile request latency exceeds
      ${var.latency_threshold_ms}ms for more than ${var.alert_duration_high_latency}.

      ### Common causes
      - Downstream API degradation
      - Increased payload sizes
      - Database connection pool exhaustion
      - Cold starts after deployments

      ### Runbook
      1. Check the [Overview dashboard](${"https://console.cloud.google.com/monitoring/dashboards/builder/${google_monitoring_dashboard.service_overview.dashboard_id}?project=${var.project_id}"})
      2. Inspect Cloud Trace for slow spans
      3. Review recent deployments for regressions
      4. Escalate to on-call if p99 > 10s for > 5 minutes
    EOT
    mime_type = "text/markdown"
    subject   = "${title(var.service_name)} ${var.environment}: High latency detected"
  }

  conditions {
    display_name = "p95 latency > ${var.latency_threshold_ms}ms"

    condition_threshold {
      filter = <<-EOT
        resource.type = "${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}"
        AND metric.type = "${var.cloud_run_service ? "run.googleapis.com/request_latencies" : "custom.googleapis.com/${var.service_name}/request_latency"}"
        ${var.cloud_run_service ? "AND metric.labels.service_name = \"${var.service_name}\"" : ""}
      EOT

      aggregations {
        alignment_period   = "300s" # 5-minute alignment
        per_series_aligner = "ALIGN_PERCENTILE_95"
      }

      comparison      = "COMPARISON_GT"
      threshold_value = var.latency_threshold_ms
      duration        = var.alert_duration_high_latency

      trigger {
        count = 1
      }
    }
  }

  notification_channels = local.notification_channels

  alert_strategy {
    auto_close = "86400s" # Auto-close after 24 hours
  }

  severity = "WARNING"

  depends_on = [
    google_project_service.monitoring,
    google_monitoring_notification_channel.email,
  ]

  labels = {
    service     = var.service_name
    environment = var.environment
    alert_type  = "latency"
  }

  user_labels = {
    runbook_url = "https://wiki.internal/${var.service_name}/runbooks/high-latency"
  }
}

# =============================================================================
# Alert Policy: High Error Rate (5xx > threshold)
# =============================================================================
resource "google_monitoring_alert_policy" "high_error_rate" {
  display_name = "${var.service_name}-${var.environment}-high-error-rate"
  project      = var.project_id
  combiner     = "OR"

  documentation {
    content   = <<-EOT
      ## ${title(var.service_name)} High Error Rate Alert

      This alert fires when the 5xx error rate exceeds
      ${var.error_rate_threshold * 100}% for more than ${var.alert_duration_error_rate}.

      ### Common causes
      - Unhandled exceptions in request handlers
      - Downstream service failures
      - Database connectivity issues
      - Out of memory errors

      ### Runbook
      1. Check Cloud Logging for ERROR-level logs with trace IDs
      2. Review the [Overview dashboard](${"https://console.cloud.google.com/monitoring/dashboards/builder/${google_monitoring_dashboard.service_overview.dashboard_id}?project=${var.project_id}"})
      3. Check if error spike correlates with a deployment
      4. Look for specific exception patterns in Error Reporting
      5. Rollback deployment if error rate > 5%
      6. Escalate to on-call if error rate > 10% for > 5 minutes
    EOT
    mime_type = "text/markdown"
    subject   = "${title(var.service_name)} ${var.environment}: High error rate detected"
  }

  conditions {
    display_name = "5xx error rate > ${var.error_rate_threshold * 100}%"

    condition_threshold {
      filter = <<-EOT
        resource.type = "${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}"
        AND metric.type = "${var.cloud_run_service ? "run.googleapis.com/request_count" : "custom.googleapis.com/${var.service_name}/request_count"}"
        AND metric.labels.response_code_class = "5xx"
      EOT

      aggregations {
        alignment_period     = "60s" # 1-minute alignment
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }

      comparison      = "COMPARISON_GT"
      threshold_value = var.error_rate_threshold
      duration        = var.alert_duration_error_rate

      trigger {
        count = 1
      }
    }
  }

  # Also alert on absolute 5xx count to catch low-traffic error spikes
  conditions {
    display_name = "5xx count > 10 requests/min"

    condition_threshold {
      filter = <<-EOT
        resource.type = "${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}"
        AND metric.type = "${var.cloud_run_service ? "run.googleapis.com/request_count" : "custom.googleapis.com/${var.service_name}/request_count"}"
        AND metric.labels.response_code_class = "5xx"
      EOT

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }

      comparison      = "COMPARISON_GT"
      threshold_value = 10
      duration        = var.alert_duration_error_rate

      trigger {
        count = 1
      }
    }
  }

  notification_channels = local.notification_channels

  alert_strategy {
    auto_close = "86400s"
  }

  severity = "CRITICAL"

  depends_on = [
    google_project_service.monitoring,
    google_monitoring_notification_channel.email,
  ]

  labels = {
    service     = var.service_name
    environment = var.environment
    alert_type  = "error_rate"
  }

  user_labels = {
    runbook_url = "https://wiki.internal/${var.service_name}/runbooks/high-error-rate"
  }
}

# =============================================================================
# Alert Policy: Resource Exhaustion (CPU / Memory)
# =============================================================================
resource "google_monitoring_alert_policy" "resource_exhaustion" {
  display_name = "${var.service_name}-${var.environment}-resource-exhaustion"
  project      = var.project_id
  combiner     = "OR"

  documentation {
    content   = <<-EOT
      ## ${title(var.service_name)} Resource Exhaustion Alert

      This alert fires when CPU utilization exceeds ${var.cpu_threshold * 100}%
      or memory utilization exceeds ${var.memory_threshold * 100}%.

      ### Common causes
      - Memory leaks in application code
      - Insufficient resource allocation
      - Traffic spikes beyond provisioned capacity
      - Blocking I/O operations

      ### Runbook
      1. Check the [Overview dashboard](${"https://console.cloud.google.com/monitoring/dashboards/builder/${google_monitoring_dashboard.service_overview.dashboard_id}?project=${var.project_id}"})
      2. Review recent traffic patterns
      3. Check for memory leaks in Error Reporting
      4. Scale up resources if sustained high usage
      5. Escalate if memory continuously climbing
    EOT
    mime_type = "text/markdown"
    subject   = "${title(var.service_name)} ${var.environment}: Resource exhaustion detected"
  }

  # CPU condition
  conditions {
    display_name = "CPU utilization > ${var.cpu_threshold * 100}%"

    condition_threshold {
      filter = <<-EOT
        resource.type = "${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}"
        AND metric.type = "${var.cloud_run_service ? "run.googleapis.com/container/cpu/utilizations" : "custom.googleapis.com/${var.service_name}/cpu_utilization"}"
        ${var.cloud_run_service ? "AND metric.labels.service_name = \"${var.service_name}\"" : ""}
      EOT

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_99"
        cross_series_reducer = "REDUCE_SUM"
      }

      comparison      = "COMPARISON_GT"
      threshold_value = var.cpu_threshold
      duration        = var.alert_duration_resource

      trigger {
        count = 1
      }
    }
  }

  # Memory condition
  conditions {
    display_name = "Memory utilization > ${var.memory_threshold * 100}%"

    condition_threshold {
      filter = <<-EOT
        resource.type = "${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}"
        AND metric.type = "${var.cloud_run_service ? "run.googleapis.com/container/memory/utilizations" : "custom.googleapis.com/${var.service_name}/memory_utilization"}"
        ${var.cloud_run_service ? "AND metric.labels.service_name = \"${var.service_name}\"" : ""}
      EOT

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_99"
        cross_series_reducer = "REDUCE_SUM"
      }

      comparison      = "COMPARISON_GT"
      threshold_value = var.memory_threshold
      duration        = var.alert_duration_resource

      trigger {
        count = 1
      }
    }
  }

  notification_channels = local.notification_channels

  alert_strategy {
    auto_close = "86400s"
  }

  severity = "WARNING"

  depends_on = [
    google_project_service.monitoring,
    google_monitoring_notification_channel.email,
  ]

  labels = {
    service     = var.service_name
    environment = var.environment
    alert_type  = "resource_exhaustion"
  }

  user_labels = {
    runbook_url = "https://wiki.internal/${var.service_name}/runbooks/resource-exhaustion"
  }
}

# =============================================================================
# Alert Policy: Custom Business Metric Alert (optional)
# =============================================================================
# This is a generic template for business-specific alerts.
# Duplicate and customize for each business metric you want to monitor.
resource "google_monitoring_alert_policy" "custom_metric" {
  count = var.custom_alert_enabled ? 1 : 0

  display_name = "${var.service_name}-${var.environment}-custom-${var.custom_alert_metric_name}"
  project      = var.project_id
  combiner     = "OR"

  documentation {
    content   = <<-EOT
      ## ${title(var.service_name)} Custom Metric Alert

      This alert fires when ${var.custom_alert_metric_name} exceeds the configured threshold.

      Metric filter: ${var.custom_alert_filter}
      Threshold: ${var.custom_alert_threshold}
      Duration: ${var.custom_alert_duration}
    EOT
    mime_type = "text/markdown"
    subject   = "${title(var.service_name)} ${var.environment}: Custom metric alert — ${var.custom_alert_metric_name}"
  }

  conditions {
    display_name = "${var.custom_alert_metric_name} ${var.custom_alert_comparison} ${var.custom_alert_threshold}"

    condition_threshold {
      filter = var.custom_alert_filter

      aggregations {
        alignment_period     = "${var.custom_alert_alignment_period}"
        per_series_aligner   = var.custom_alert_aligner
        cross_series_reducer = var.custom_alert_reducer
      }

      comparison      = var.custom_alert_comparison == "gt" ? "COMPARISON_GT" : var.custom_alert_comparison == "lt" ? "COMPARISON_LT" : "COMPARISON_GT"
      threshold_value = var.custom_alert_threshold
      duration        = var.custom_alert_duration

      trigger {
        count = 1
      }
    }
  }

  notification_channels = local.notification_channels

  alert_strategy {
    auto_close = "86400s"
  }

  severity = var.custom_alert_severity

  depends_on = [
    google_project_service.monitoring,
    google_monitoring_notification_channel.email,
  ]

  labels = {
    service     = var.service_name
    environment = var.environment
    alert_type  = "custom"
  }
}

# =============================================================================
# Dashboard: Service Overview
# =============================================================================
resource "google_monitoring_dashboard" "service_overview" {
  project        = var.project_id
  dashboard_id   = "${var.service_name}-${var.environment}-overview"
  display_name   = "${title(var.service_name)} ${var.environment} — Overview"

  dashboard_json = jsonencode({
    displayName = "${title(var.service_name)} ${var.environment} — Overview"
    gridLayout = {
      columns = "2"
      widgets = [
        {
          title = "Request Rate (rpm)"
          xyChart = {
            dataSets = [{
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}\" AND metric.type=\"${var.cloud_run_service ? "run.googleapis.com/request_count" : "custom.googleapis.com/${var.service_name}/request_count"}\""
                  aggregation = {
                    alignmentPeriod    = "60s"
                    perSeriesAligner   = "ALIGN_RATE"
                    crossSeriesReducer = "REDUCE_SUM"
                  }
                }
              }
            }]
          }
        },
        {
          title = "Error Rate (%)"
          xyChart = {
            dataSets = [{
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}\" AND metric.type=\"${var.cloud_run_service ? "run.googleapis.com/request_count" : "custom.googleapis.com/${var.service_name}/request_count"}\" AND metric.labels.response_code_class=\"5xx\""
                  aggregation = {
                    alignmentPeriod    = "60s"
                    perSeriesAligner   = "ALIGN_RATE"
                    crossSeriesReducer = "REDUCE_SUM"
                  }
                }
              }
            }]
          }
        },
        {
          title = "p50 / p95 / p99 Latency (ms)"
          xyChart = {
            dataSets = [
              {
                title = "p50"
                xyChart = {
                  dataSets = [{
                    timeSeriesQuery = {
                      timeSeriesFilter = {
                        filter = "resource.type=\"${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}\" AND metric.type=\"${var.cloud_run_service ? "run.googleapis.com/request_latencies" : "custom.googleapis.com/${var.service_name}/request_latency"}\""
                        aggregation = {
                          alignmentPeriod  = "60s"
                          perSeriesAligner = "ALIGN_PERCENTILE_50"
                        }
                      }
                    }
                  }]
                }
              },
              {
                title = "p95"
                xyChart = {
                  dataSets = [{
                    timeSeriesQuery = {
                      timeSeriesFilter = {
                        filter = "resource.type=\"${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}\" AND metric.type=\"${var.cloud_run_service ? "run.googleapis.com/request_latencies" : "custom.googleapis.com/${var.service_name}/request_latency"}\""
                        aggregation = {
                          alignmentPeriod  = "60s"
                          perSeriesAligner = "ALIGN_PERCENTILE_95"
                        }
                      }
                    }
                  }]
                }
              },
              {
                title = "p99"
                xyChart = {
                  dataSets = [{
                    timeSeriesQuery = {
                      timeSeriesFilter = {
                        filter = "resource.type=\"${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}\" AND metric.type=\"${var.cloud_run_service ? "run.googleapis.com/request_latencies" : "custom.googleapis.com/${var.service_name}/request_latency"}\""
                        aggregation = {
                          alignmentPeriod  = "60s"
                          perSeriesAligner = "ALIGN_PERCENTILE_99"
                        }
                      }
                    }
                  }]
                }
              }
            ]
          }
        },
        {
          title = "Active Instances"
          scoreCard = {
            timeSeriesQuery = {
              timeSeriesFilter = {
                filter = "resource.type=\"${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}\" AND metric.type=\"${var.cloud_run_service ? "run.googleapis.com/container/instance_count" : "custom.googleapis.com/${var.service_name}/active_instances"}\""
                aggregation = {
                  alignmentPeriod    = "60s"
                  perSeriesAligner   = "ALIGN_MEAN"
                  crossSeriesReducer = "REDUCE_SUM"
                }
              }
            }
          }
        },
        {
          title = "CPU Utilization"
          xyChart = {
            dataSets = [{
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}\" AND metric.type=\"${var.cloud_run_service ? "run.googleapis.com/container/cpu/utilizations" : "custom.googleapis.com/${var.service_name}/cpu_utilization"}\""
                  aggregation = {
                    alignmentPeriod    = "300s"
                    perSeriesAligner   = "ALIGN_PERCENTILE_95"
                    crossSeriesReducer = "REDUCE_MEAN"
                  }
                }
              }
            }]
          }
        },
        {
          title = "Memory Utilization"
          xyChart = {
            dataSets = [{
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}\" AND metric.type=\"${var.cloud_run_service ? "run.googleapis.com/container/memory/utilizations" : "custom.googleapis.com/${var.service_name}/memory_utilization"}\""
                  aggregation = {
                    alignmentPeriod    = "300s"
                    perSeriesAligner   = "ALIGN_PERCENTILE_95"
                    crossSeriesReducer = "REDUCE_MEAN"
                  }
                }
              }
            }]
          }
        }
      ]
    }
  })

  depends_on = [google_project_service.monitoring]
}

# =============================================================================
# Dashboard: Service Pipeline (adapt panels based on your domain)
# =============================================================================
resource "google_monitoring_dashboard" "service_pipeline" {
  project        = var.project_id
  dashboard_id   = "${var.service_name}-${var.environment}-pipeline"
  display_name   = "${title(var.service_name)} ${var.environment} — Pipeline"

  dashboard_json = jsonencode({
    displayName = "${title(var.service_name)} ${var.environment} — Pipeline"
    gridLayout = {
      columns = "2"
      widgets = concat(
        [
          {
            title = "Request Throughput (rpm)"
            xyChart = {
              dataSets = [{
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "resource.type=\"${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}\" AND metric.type=\"${var.cloud_run_service ? "run.googleapis.com/request_count" : "custom.googleapis.com/${var.service_name}/request_count"}\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_RATE"
                      crossSeriesReducer = "REDUCE_SUM"
                    }
                  }
                }
              }]
            }
          },
          {
            title = "Pipeline Stage Duration (avg ms)"
            xyChart = {
              dataSets = [{
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "resource.type=\"${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}\" AND metric.type=\"logging.googleapis.com/user/${var.service_name}/${var.environment}/pipeline_duration\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_MEAN"
                      crossSeriesReducer = "REDUCE_MEAN"
                    }
                  }
                }
              }]
            }
          }
        ],
        var.ai_inference_enabled ? [
          {
            title = "Inference Latency (p95)"
            xyChart = {
              dataSets = [{
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "resource.type=\"${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}\" AND metric.type=\"custom.googleapis.com/${var.service_name}/inference_latency\""
                    aggregation = {
                      alignmentPeriod  = "60s"
                      perSeriesAligner = "ALIGN_PERCENTILE_95"
                    }
                  }
                }
              }]
            }
          },
          {
            title = "Inference Count (rpm)"
            scoreCard = {
              timeSeriesQuery = {
                timeSeriesFilter = {
                  filter = "resource.type=\"${var.cloud_run_service ? "cloud_run_revision" : "generic_task"}\" AND metric.type=\"custom.googleapis.com/${var.service_name}/inference_count\""
                  aggregation = {
                    alignmentPeriod    = "300s"
                    perSeriesAligner   = "ALIGN_RATE"
                    crossSeriesReducer = "REDUCE_SUM"
                  }
                }
              }
            }
          }
        ] : []
      )
    }
  })

  depends_on = [google_project_service.monitoring]
}
