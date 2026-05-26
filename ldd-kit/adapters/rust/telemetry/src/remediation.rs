//! Failure analysis and remediation suggestion engine.
//!
//! Parses structured logs, identifies recurring error patterns, correlates
//! related incidents by trace context, and produces actionable remediation
//! suggestions with confidence scores.
//!
//! # Example
//!
//! ```no_run
//! use telemetry::FailureAnalyzer;
//! use serde_json::json;
//!
//! let analyzer = FailureAnalyzer::new();
//! let logs = vec![
//!     json!({"level": "error", "message": "cuda out of memory", "timestamp": "2024-01-01T00:00:00Z"}),
//! ];
//! let suggestions = analyzer.analyze(&logs);
//! assert!(!suggestions.is_empty());
//! ```

use chrono::{DateTime, Utc};
use regex::Regex;
use serde_json::Value;
use std::collections::HashMap;

// ---------------------------------------------------------------------------
// Data types
// ---------------------------------------------------------------------------

/// A single remediation suggestion produced by `FailureAnalyzer`.
#[derive(Debug, Clone, PartialEq)]
pub struct RemediationSuggestion {
    /// Short title of the identified issue.
    pub title: String,
    /// Detailed description of the problem.
    pub description: String,
    /// Confidence score from 0.0 to 1.0.
    pub confidence: f64,
    /// Ordered list of suggested remediation actions.
    pub suggested_actions: Vec<String>,
}

/// A cluster of related error events sharing a trace or temporal window.
#[derive(Debug, Clone, PartialEq)]
pub struct CorrelatedIncident {
    /// Unique incident identifier.
    pub incident_id: String,
    /// Trace ID if events are trace-correlated.
    pub trace_id: Option<String>,
    /// Earliest event timestamp.
    pub start_time: DateTime<Utc>,
    /// Latest event timestamp.
    pub end_time: DateTime<Utc>,
    /// Number of events in the cluster.
    pub event_count: usize,
    /// Detected error type patterns.
    pub error_types: Vec<String>,
    /// Most likely root cause pattern.
    pub root_cause_pattern: Option<String>,
    /// List of affected services.
    pub affected_services: Vec<String>,
}

// ---------------------------------------------------------------------------
// Failure Analyzer
// ---------------------------------------------------------------------------

/// Analyse structured log entries and produce remediation suggestions.
///
/// The analyser uses regex-based pattern matching to recognise common
/// failure modes (GPU OOM, timeouts, connection errors, etc.), groups
/// correlated events by *trace_id* and time window, and returns ranked
/// suggestions.
pub struct FailureAnalyzer {
    patterns: HashMap<String, Regex>,
}

impl FailureAnalyzer {
    /// Create a new `FailureAnalyzer` with the default compiled error patterns.
    pub fn new() -> Self {
        let mut patterns = HashMap::new();

        let defs: Vec<(&str, &str)> = vec![
            ("gpu_oom", r"(?i)(cuda out of memory|gpu oom|torch\.cuda\.OutOfMemory|out of memory.*gpu|nvml.*memory)"),
            ("timeout", r"(?i)(timeout|timed out|deadline exceeded|context deadline|read timeout|connect timeout|request timeout)"),
            ("connection_error", r"(?i)(connection refused|connection reset|connection closed|broken pipe|no route to host|dns.*fail|network.*unreachable|cannot connect|connection.*error)"),
            ("rate_limit", r"(?i)(rate limit|too many requests|429|throttle|quota exceeded|limit exceeded|capacity exceeded)"),
            ("auth_failure", r"(?i)(unauthorized|authentication.*fail|403|401|invalid.*token|token.*expired|credentials.*invalid|access denied|forbidden)"),
            ("database_error", r"(?i)(database.*error|sql.*error|psycopg|sqlite|pymongo|connection.*pool|lock.*timeout|deadlock|constraint.*fail)"),
            ("vision_model_error", r"(?i)(inference.*fail|model.*load|onnx.*error|tensorrt|inference.*timeout|model.*timeout|vision.*error|yolo.*error|detectron|tesseract.*error)"),
            ("memory_pressure", r"(?i)(memory.*exhausted|oom killed|killed process|memory.*pressure|swap.*full)"),
            ("dependency_unavailable", r"(?i)(service.*unavailable|503|dependency.*fail|health.*check.*fail|circuit breaker|upstream.*unavailable)"),
        ];

        for (key, pattern) in defs {
            if let Ok(re) = Regex::new(pattern) {
                patterns.insert(key.to_string(), re);
            }
        }

        Self { patterns }
    }

    /// Match a log message against known error patterns.
    ///
    /// Returns the pattern key (e.g. `"gpu_oom"`) or `None`.
    fn detect_error_type(&self, message: &str) -> Option<String> {
        for (key, pattern) in &self.patterns {
            if pattern.is_match(message) {
                return Some(key.clone());
            }
        }
        None
    }

    /// Analyse a list of structured log entries and produce suggestions.
    ///
    /// Each log entry should be a `serde_json::Value` object with keys such
    /// as `"event"`, `"message"`, `"error_message"`, `"level"`, and
    /// `"timestamp"`.
    pub fn analyze(&self, logs: &[Value]) -> Vec<RemediationSuggestion> {
        let mut suggestions: Vec<RemediationSuggestion> = Vec::new();
        if logs.is_empty() {
            return suggestions;
        }

        // ---- Pattern frequency analysis -----------------------------------
        let mut error_counts: HashMap<String, usize> = HashMap::new();
        let mut error_logs: HashMap<String, Vec<&Value>> = HashMap::new();

        for entry in logs {
            let msg = self.extract_message(entry);
            let level = self.extract_level(entry);
            if level == "ERROR" || level == "CRITICAL" || level == "EXCEPTION" {
                if let Some(err_type) = self.detect_error_type(&msg) {
                    *error_counts.entry(err_type.clone()).or_insert(0) += 1;
                    error_logs.entry(err_type).or_default().push(entry);
                }
            }
        }

        // ---- Generate suggestions based on detected patterns ---------------
        let mut sorted_errors: Vec<(String, usize)> =
            error_counts.iter().map(|(k, v)| (k.clone(), *v)).collect();
        sorted_errors.sort_by(|a, b| b.1.cmp(&a.1));

        for (err_type, count) in &sorted_errors {
            if let Some(suggestion) = self.suggest_for_pattern(err_type, *count) {
                suggestions.push(suggestion);
            }
        }

        // ---- Spike detection ----------------------------------------------
        if logs.len() >= 2 {
            let timestamps = self.extract_timestamps(logs);
            if timestamps.len() >= 2 {
                let time_window = (timestamps[timestamps.len() - 1] - timestamps[0])
                    .num_seconds() as f64;
                if time_window > 0.0 {
                    let total_errors: usize = error_counts.values().sum();
                    let error_rate = total_errors as f64 / (time_window / 60.0).max(1.0);
                    if error_rate > 10.0 {
                        suggestions.push(RemediationSuggestion {
                            title: "Error Rate Spike Detected".to_string(),
                            description: format!(
                                "Detected {:.1} errors per minute over {:.0} seconds. Consider scaling or circuit-breaker activation.",
                                error_rate, time_window
                            ),
                            confidence: (0.95_f64).min(error_rate / 50.0),
                            suggested_actions: vec![
                                "Check auto-scaling configuration".to_string(),
                                "Verify circuit breaker thresholds".to_string(),
                                "Review downstream service health".to_string(),
                                "Consider temporary traffic reduction".to_string(),
                            ],
                        });
                    }
                }
            }
        }

        // ---- Repeated error detection -------------------------------------
        for (err_type, count) in &error_counts {
            if *count >= 5 {
                let label = err_type.replace('_', " ");
                let label: String = label
                    .split_whitespace()
                    .map(|word| {
                        let mut chars = word.chars();
                        match chars.next() {
                            None => String::new(),
                            Some(first) => first.to_uppercase().collect::<String>() + &chars.as_str().to_lowercase(),
                        }
                    })
                    .collect::<Vec<_>>()
                    .join(" ");

                suggestions.push(RemediationSuggestion {
                    title: format!("Recurring {} Errors", label),
                    description: format!(
                        "{} occurrences of '{}' detected. This suggests a persistent issue requiring attention.",
                        count, err_type
                    ),
                    confidence: (0.9_f64).min(0.5 + *count as f64 / 20.0),
                    suggested_actions: vec![
                        "Review service configuration".to_string(),
                        "Check resource allocation".to_string(),
                        "Verify downstream dependency health".to_string(),
                        "Consider rolling back recent deployments".to_string(),
                    ],
                });
            }
        }

        suggestions
    }

    /// Group related error events by trace_id and time window (5 min).
    ///
    /// Each event should be a JSON object optionally containing `"trace_id"`
    /// and `"timestamp"`.
    pub fn correlate(&self, events: &[Value]) -> Vec<CorrelatedIncident> {
        if events.is_empty() {
            return Vec::new();
        }

        // Index events by trace_id
        let mut by_trace: HashMap<String, Vec<&Value>> = HashMap::new();
        let mut no_trace: Vec<&Value> = Vec::new();

        for evt in events {
            if let Some(tid) = evt.get("trace_id").and_then(|v| v.as_str()) {
                by_trace.entry(tid.to_string()).or_default().push(evt);
            } else {
                no_trace.push(evt);
            }
        }

        let mut incidents: Vec<CorrelatedIncident> = Vec::new();
        let now = Utc::now();

        // ---- Group by trace_id -------------------------------------------
        for (trace_id, evts) in &by_trace {
            if let Some(incident) = self.build_incident(Some(trace_id.clone()), evts) {
                incidents.push(incident);
            }
        }

        // ---- Group no-trace events by time window (5 min) ----------------
        if !no_trace.is_empty() {
            no_trace.sort_by(|a, b| {
                let ta = self.parse_timestamp(a.get("timestamp"), &now);
                let tb = self.parse_timestamp(b.get("timestamp"), &now);
                ta.cmp(&tb)
            });

            let window = chrono::Duration::minutes(5);
            let mut current_group: Vec<&Value> = Vec::new();

            for evt in &no_trace {
                let ts = self.parse_timestamp(evt.get("timestamp"), &now);

                if current_group.is_empty() {
                    current_group.push(*evt);
                    continue;
                }

                let last_ts = self.parse_timestamp(
                    current_group[current_group.len() - 1].get("timestamp"),
                    &now,
                );

                if ts - last_ts <= window {
                    current_group.push(*evt);
                } else {
                    if let Some(inc) = self.build_incident(None, &current_group) {
                        incidents.push(inc);
                    }
                    current_group = vec![*evt];
                }
            }

            if !current_group.is_empty() {
                if let Some(inc) = self.build_incident(None, &current_group) {
                    incidents.push(inc);
                }
            }
        }

        // Sort by event count descending
        incidents.sort_by(|a, b| b.event_count.cmp(&a.event_count));
        incidents
    }

    // ------------------------------------------------------------------------
    // Private helpers
    // ------------------------------------------------------------------------

    /// Build a `CorrelatedIncident` from a group of events.
    fn build_incident(
        &self,
        trace_id: Option<String>,
        events: &[&Value],
    ) -> Option<CorrelatedIncident> {
        if events.is_empty() {
            return None;
        }

        let now = Utc::now();
        let mut timestamps: Vec<DateTime<Utc>> = Vec::new();
        let mut error_types: Vec<String> = Vec::new();
        let mut services: Vec<String> = Vec::new();

        let mut seen_errors = std::collections::HashSet::new();
        let mut seen_services = std::collections::HashSet::new();

        for evt in events {
            let ts = self.parse_timestamp(evt.get("timestamp"), &now);
            timestamps.push(ts);

            let msg = self.extract_message(evt);
            if let Some(err) = self.detect_error_type(&msg) {
                if seen_errors.insert(err.clone()) {
                    error_types.push(err);
                }
            }

            let svc = evt
                .get("service")
                .or_else(|| evt.get("service_name"))
                .and_then(|v| v.as_str());
            if let Some(s) = svc {
                if seen_services.insert(s.to_string()) {
                    services.push(s.to_string());
                }
            }
        }

        let (start_time, end_time) = if !timestamps.is_empty() {
            (
                timestamps.iter().min().copied().unwrap_or(now),
                timestamps.iter().max().copied().unwrap_or(now),
            )
        } else {
            (now, now)
        };

        let root_cause = error_types.first().cloned();

        let inc_id = if let Some(ref tid) = trace_id {
            let short_tid = if tid.len() > 12 {
                &tid[..12]
            } else {
                tid
            };
            format!("inc-{}-{}", short_tid, events.len())
        } else {
            format!(
                "inc-{}-{}",
                start_time.format("%Y%m%d%H%M%S"),
                events.len()
            )
        };

        Some(CorrelatedIncident {
            incident_id: inc_id,
            trace_id,
            start_time,
            end_time,
            event_count: events.len(),
            error_types,
            root_cause_pattern: root_cause,
            affected_services: services,
        })
    }

    /// Return a `RemediationSuggestion` for a recognised pattern.
    fn suggest_for_pattern(
        &self,
        pattern: &str,
        count: usize,
    ) -> Option<RemediationSuggestion> {
        let count_f = count as f64;

        let suggestion = match pattern {
            "gpu_oom" => RemediationSuggestion {
                title: "GPU Out-of-Memory Errors".to_string(),
                description: format!(
                    "{} GPU OOM error(s) detected. The vision model is exhausting GPU memory during inference.",
                    count
                ),
                confidence: (0.95_f64).min(0.6 + count_f / 10.0),
                suggested_actions: vec![
                    "Reduce batch size for vision inference".to_string(),
                    "Enable model quantization (FP16 / INT8)".to_string(),
                    "Scale GPU nodes horizontally".to_string(),
                    "Implement request queueing with back-pressure".to_string(),
                    "Consider model sharding for large inputs".to_string(),
                ],
            },
            "timeout" => RemediationSuggestion {
                title: "Request Timeout Errors".to_string(),
                description: format!(
                    "{} timeout error(s) detected. Downstream services or inference is taking longer than expected.",
                    count
                ),
                confidence: (0.9_f64).min(0.5 + count_f / 15.0),
                suggested_actions: vec![
                    "Increase timeout thresholds temporarily".to_string(),
                    "Review slow query / endpoint performance".to_string(),
                    "Enable async processing for long-running operations".to_string(),
                    "Add caching for frequently accessed data".to_string(),
                    "Scale affected service replicas".to_string(),
                ],
            },
            "connection_error" => RemediationSuggestion {
                title: "Connection Errors".to_string(),
                description: format!(
                    "{} connection error(s) detected. Network issues or downstream service unavailability.",
                    count
                ),
                confidence: (0.85_f64).min(0.5 + count_f / 15.0),
                suggested_actions: vec![
                    "Verify downstream service health endpoints".to_string(),
                    "Check DNS resolution and network policies".to_string(),
                    "Review connection pool configuration".to_string(),
                    "Enable retry with exponential backoff".to_string(),
                    "Check firewall / security group rules".to_string(),
                ],
            },
            "rate_limit" => RemediationSuggestion {
                title: "Rate Limiting / Throttling".to_string(),
                description: format!(
                    "{} rate-limit error(s) detected. External API quotas are being exceeded.",
                    count
                ),
                confidence: (0.88_f64).min(0.55 + count_f / 12.0),
                suggested_actions: vec![
                    "Implement client-side rate limiting".to_string(),
                    "Add request caching to reduce API calls".to_string(),
                    "Request quota increase from provider".to_string(),
                    "Enable request batching".to_string(),
                    "Add circuit breaker for external APIs".to_string(),
                ],
            },
            "auth_failure" => RemediationSuggestion {
                title: "Authentication Failures".to_string(),
                description: format!(
                    "{} authentication error(s) detected. Token expiry or credential misconfiguration.",
                    count
                ),
                confidence: (0.82_f64).min(0.5 + count_f / 15.0),
                suggested_actions: vec![
                    "Check token expiry and refresh logic".to_string(),
                    "Verify API key / credential configuration".to_string(),
                    "Review IAM policies and permissions".to_string(),
                    "Rotate credentials if compromised".to_string(),
                    "Audit recent permission changes".to_string(),
                ],
            },
            "database_error" => RemediationSuggestion {
                title: "Database Errors".to_string(),
                description: format!(
                    "{} database error(s) detected. Connection pool exhaustion or query issues.",
                    count
                ),
                confidence: (0.85_f64).min(0.5 + count_f / 15.0),
                suggested_actions: vec![
                    "Check connection pool size and usage".to_string(),
                    "Review slow query log".to_string(),
                    "Verify database replication lag".to_string(),
                    "Add read replicas for query offload".to_string(),
                    "Check for lock contention and deadlocks".to_string(),
                ],
            },
            "vision_model_error" => RemediationSuggestion {
                title: "Vision Model Inference Errors".to_string(),
                description: format!(
                    "{} vision model error(s) detected. Model loading or inference pipeline failure.",
                    count
                ),
                confidence: (0.88_f64).min(0.55 + count_f / 12.0),
                suggested_actions: vec![
                    "Verify model artifact availability in storage".to_string(),
                    "Check model version compatibility".to_string(),
                    "Validate input image format and size".to_string(),
                    "Restart inference worker pods".to_string(),
                    "Roll back to previous model version".to_string(),
                ],
            },
            "memory_pressure" => RemediationSuggestion {
                title: "System Memory Pressure".to_string(),
                description: format!(
                    "{} memory pressure event(s) detected. Host-level memory exhaustion detected.",
                    count
                ),
                confidence: (0.85_f64).min(0.5 + count_f / 12.0),
                suggested_actions: vec![
                    "Increase container memory limits".to_string(),
                    "Add memory-based Horizontal Pod Autoscaler".to_string(),
                    "Review memory leaks in application code".to_string(),
                    "Enable swap or increase node memory".to_string(),
                    "Restart affected services to reclaim memory".to_string(),
                ],
            },
            "dependency_unavailable" => RemediationSuggestion {
                title: "Dependency Service Unavailable".to_string(),
                description: format!(
                    "{} dependency unavailable error(s) detected. Required downstream service is not reachable.",
                    count
                ),
                confidence: (0.87_f64).min(0.52 + count_f / 14.0),
                suggested_actions: vec![
                    "Check health status of all downstream services".to_string(),
                    "Verify service discovery / registry configuration".to_string(),
                    "Review recent deployment changes".to_string(),
                    "Enable graceful degradation mode".to_string(),
                    "Scale downstream service replicas".to_string(),
                ],
            },
            _ => return None,
        };

        Some(suggestion)
    }

    // ------------------------------------------------------------------------
    // Extraction helpers
    // ------------------------------------------------------------------------

    /// Extract the message string from a log entry.
    fn extract_message(&self, entry: &Value) -> String {
        for key in &["event", "message", "error_message", "msg"] {
            if let Some(v) = entry.get(key) {
                return v.as_str().map(String::from).unwrap_or_else(|| v.to_string());
            }
        }
        String::new()
    }

    /// Extract and normalize the log level from a log entry.
    fn extract_level(&self, entry: &Value) -> String {
        let level = entry
            .get("log_level")
            .or_else(|| entry.get("level"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_uppercase();
        level
    }

    /// Parse an ISO 8601 timestamp from a JSON value.
    fn parse_timestamp(&self, ts: Option<&Value>, fallback: &DateTime<Utc>) -> DateTime<Utc> {
        let ts_str = match ts {
            Some(Value::String(s)) => s.as_str(),
            _ => return *fallback,
        };

        DateTime::parse_from_rfc3339(ts_str)
            .map(|dt| dt.with_timezone(&Utc))
            .unwrap_or(*fallback)
    }

    /// Extract all valid timestamps from log entries.
    fn extract_timestamps(&self, logs: &[Value]) -> Vec<DateTime<Utc>> {
        let now = Utc::now();
        logs.iter()
            .filter_map(|entry| {
                entry
                    .get("timestamp")
                    .and_then(|v| v.as_str())
                    .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
                    .map(|dt| dt.with_timezone(&Utc))
            })
            .collect()
    }
}

impl Default for FailureAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}
