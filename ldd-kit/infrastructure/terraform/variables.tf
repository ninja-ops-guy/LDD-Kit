# =============================================================================
# Variables — {{ SERVICE_NAME }} Telemetry Infrastructure
# =============================================================================
# All variables with proper validation, descriptions, and sensible defaults.
# =============================================================================

# ---------------------------------------------------------------------------
# Required variables
# ---------------------------------------------------------------------------

variable "project_id" {
  description = "GCP project ID where monitoring resources will be created."
  type        = string
  nullable    = false

  validation {
    condition     = length(var.project_id) > 0
    error_message = "project_id must not be empty."
  }
}

variable "alert_email" {
  description = "Email address for alert notifications. All alert policies will send notifications to this address."
  type        = string
  nullable    = false

  validation {
    condition     = length(var.alert_email) > 0
    error_message = "alert_email must not be empty."
  }
}

variable "service_name" {
  description = "Name of the service (used in resource IDs, metric names, labels). Must be lowercase alphanumeric with hyphens only."
  type        = string
  nullable    = false
  default     = "{{ SERVICE_NAME }}"

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.service_name))
    error_message = "service_name must be lowercase alphanumeric with hyphens only (e.g., 'my-service')."
  }
}

# ---------------------------------------------------------------------------
# Optional variables with sensible defaults
# ---------------------------------------------------------------------------

variable "region" {
  description = "GCP region for regional resources."
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Deployment environment label (production, staging, development)."
  type        = string
  default     = "production"

  validation {
    condition     = contains(["production", "staging", "development"], var.environment)
    error_message = "environment must be one of: production, staging, development."
  }
}

variable "cloud_run_service" {
  description = "Whether this service is deployed on Cloud Run (uses Cloud Run metric types) or generic compute."
  type        = bool
  default     = true
}

variable "service_account_email" {
  description = "Service account email for IAM bindings. Defaults to the App Engine default service account."
  type        = string
  default     = ""
}

variable "enable_log_sink" {
  description = "Whether to create a BigQuery log sink for long-term log storage."
  type        = bool
  default     = true
}

variable "enable_trace_sink" {
  description = "Whether to create a Cloud Monitoring trace sink for long-term trace retention."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "Number of days to retain logs in BigQuery. Default is 30 days."
  type        = number
  default     = 30

  validation {
    condition     = var.log_retention_days >= 1 && var.log_retention_days <= 3650
    error_message = "log_retention_days must be between 1 and 3650 (10 years)."
  }
}

variable "latency_threshold_ms" {
  description = "Latency threshold in milliseconds for high-latency alerts (p95)."
  type        = number
  default     = 5000 # 5 seconds

  validation {
    condition     = var.latency_threshold_ms >= 100
    error_message = "latency_threshold_ms must be at least 100ms."
  }
}

variable "error_rate_threshold" {
  description = "Error rate threshold for 5xx alerts (fraction, e.g., 0.01 = 1%)."
  type        = number
  default     = 0.01 # 1%

  validation {
    condition     = var.error_rate_threshold > 0 && var.error_rate_threshold <= 1
    error_message = "error_rate_threshold must be between 0 and 1."
  }
}

variable "cpu_threshold" {
  description = "CPU utilization threshold (fraction, e.g., 0.8 = 80%) for resource exhaustion alerts."
  type        = number
  default     = 0.8

  validation {
    condition     = var.cpu_threshold > 0 && var.cpu_threshold <= 1
    error_message = "cpu_threshold must be between 0 and 1."
  }
}

variable "memory_threshold" {
  description = "Memory utilization threshold (fraction, e.g., 0.85 = 85%) for resource exhaustion alerts."
  type        = number
  default     = 0.85

  validation {
    condition     = var.memory_threshold > 0 && var.memory_threshold <= 1
    error_message = "memory_threshold must be between 0 and 1."
  }
}

variable "alert_duration_high_latency" {
  description = "Duration condition for high latency alerts. Use Terraform duration format (e.g., 300s, 5m)."
  type        = string
  default     = "300s" # 5 minutes

  validation {
    condition     = can(regex("^[0-9]+[smhd]$", var.alert_duration_high_latency))
    error_message = "alert_duration_high_latency must be a valid duration (e.g., 300s, 5m, 1h)."
  }
}

variable "alert_duration_error_rate" {
  description = "Duration condition for error rate alerts."
  type        = string
  default     = "120s" # 2 minutes

  validation {
    condition     = can(regex("^[0-9]+[smhd]$", var.alert_duration_error_rate))
    error_message = "alert_duration_error_rate must be a valid duration (e.g., 120s, 2m)."
  }
}

variable "alert_duration_resource" {
  description = "Duration condition for resource exhaustion alerts."
  type        = string
  default     = "600s" # 10 minutes

  validation {
    condition     = can(regex("^[0-9]+[smhd]$", var.alert_duration_resource))
    error_message = "alert_duration_resource must be a valid duration (e.g., 600s, 10m)."
  }
}

variable "ai_inference_enabled" {
  description = "Whether AI inference dashboards and alerts are enabled. Set to true if your service uses ML models."
  type        = bool
  default     = false
}

variable "pagerduty_integration_key" {
  description = "PagerDuty integration key for critical alerts. Leave empty to disable PagerDuty notifications."
  type        = string
  default     = ""
  sensitive   = true
}

variable "slack_channel" {
  description = "Slack channel name for notifications (without #). Leave empty to disable Slack notifications."
  type        = string
  default     = ""
}

variable "slack_auth_token" {
  description = "Slack bot auth token for notification integration. Required if slack_channel is set."
  type        = string
  default     = ""
  sensitive   = true
}

variable "enable_terraform_backends" {
  description = "Whether to configure GCS backend for Terraform state. Set to true after creating the state bucket."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Custom alert variables (for business-specific metrics)
# ---------------------------------------------------------------------------

variable "custom_alert_enabled" {
  description = "Whether to create a custom business metric alert policy."
  type        = bool
  default     = false
}

variable "custom_alert_metric_name" {
  description = "Name of the custom metric to alert on."
  type        = string
  default     = ""
}

variable "custom_alert_filter" {
  description = "MQL filter for the custom metric alert."
  type        = string
  default     = ""
}

variable "custom_alert_threshold" {
  description = "Threshold value for the custom metric alert."
  type        = number
  default     = 0
}

variable "custom_alert_duration" {
  description = "Duration condition for the custom alert."
  type        = string
  default     = "300s"
}

variable "custom_alert_comparison" {
  description = "Comparison operator for the custom alert: 'gt' (greater than) or 'lt' (less than)."
  type        = string
  default     = "gt"

  validation {
    condition     = contains(["gt", "lt"], var.custom_alert_comparison)
    error_message = "custom_alert_comparison must be 'gt' or 'lt'."
  }
}

variable "custom_alert_aligner" {
  description = "Alignment function for the custom alert."
  type        = string
  default     = "ALIGN_MEAN"
}

variable "custom_alert_reducer" {
  description = "Cross-series reducer for the custom alert."
  type        = string
  default     = "REDUCE_MEAN"
}

variable "custom_alert_alignment_period" {
  description = "Alignment period for the custom alert."
  type        = string
  default     = "300s"
}

variable "custom_alert_severity" {
  description = "Severity level for the custom alert: CRITICAL, ERROR, WARNING, or INFO."
  type        = string
  default     = "WARNING"

  validation {
    condition     = contains(["CRITICAL", "ERROR", "WARNING", "INFO"], var.custom_alert_severity)
    error_message = "custom_alert_severity must be one of: CRITICAL, ERROR, WARNING, INFO."
  }
}
