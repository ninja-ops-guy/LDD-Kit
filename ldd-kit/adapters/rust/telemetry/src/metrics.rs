//! Prometheus metrics collector for Rust.
//!
//! Defines counters, histograms, and gauges using the `prometheus` crate and
//! exposes a `MetricsCollector` singleton for use throughout the application.
//!
//! # Example
//!
//! ```no_run
//! use telemetry::{get_collector, MetricsCollector};
//!
//! let collector = get_collector();
//! collector.record_request("GET", "/api/health", 200, 0.012);
//! collector.record_event("scan_completed");
//! collector.set_gauge("active_scans", 3.0);
//!
//! let metrics = collector.get_prometheus_metrics();
//! println!("{}", metrics);
//! ```

use prometheus::{
    register_counter_vec, register_gauge_vec, register_histogram_vec, CounterVec, Encoder,
    GaugeVec, HistogramVec, Registry, TextEncoder,
};
use std::sync::Mutex;

// ---------------------------------------------------------------------------
// Lazy-initialised metric definitions
// ---------------------------------------------------------------------------

lazy_static::lazy_static! {
    /// Total HTTP requests received.
    static ref REQUESTS_TOTAL: CounterVec = register_counter_vec!(
        "service_requests_total",
        "Total HTTP requests received",
        &["method", "path", "status"]
    ).expect("metric registration failed");

    /// HTTP request latency in seconds.
    static ref REQUEST_DURATION_SECONDS: HistogramVec = register_histogram_vec!(
        "service_request_duration_seconds",
        "HTTP request latency in seconds",
        &["method", "path"],
        vec![0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0]
    ).expect("metric registration failed");

    /// Custom operation duration in seconds (e.g. scan, processing).
    static ref CUSTOM_DURATION_SECONDS: HistogramVec = register_histogram_vec!(
        "service_custom_duration_seconds",
        "Custom operation duration in seconds",
        &["operation"],
        vec![1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0]
    ).expect("metric registration failed");

    /// Total events processed.
    static ref EVENTS_TOTAL: CounterVec = register_counter_vec!(
        "service_events_total",
        "Total events processed",
        &["event_type"]
    ).expect("metric registration failed");

    /// Model inference latency in seconds.
    static ref INFERENCE_DURATION_SECONDS: HistogramVec = register_histogram_vec!(
        "service_inference_duration_seconds",
        "Model inference latency in seconds",
        &["model"]
    ).expect("metric registration failed");

    /// Total outbound API calls.
    static ref API_CALLS_TOTAL: CounterVec = register_counter_vec!(
        "service_api_calls_total",
        "Total outbound API calls",
        &["service", "status"]
    ).expect("metric registration failed");

    /// Number of operations currently in progress.
    static ref ACTIVE_OPERATIONS: GaugeVec = register_gauge_vec!(
        "service_active_operations",
        "Number of operations currently in progress",
        &["operation_type"]
    ).expect("metric registration failed");
}

// ---------------------------------------------------------------------------
// Singleton collector
// ---------------------------------------------------------------------------

/// Singleton facade for updating Prometheus metrics.
///
/// All methods are safe for concurrent use via the underlying `prometheus`
/// crate's atomic operations.
///
/// Use [`get_collector`](fn@get_collector) to obtain the global instance.
pub struct MetricsCollector {
    registry: Registry,
}

impl MetricsCollector {
    /// Create a new MetricsCollector. This should generally not be called
    /// directly — use `get_collector()` for the singleton.
    pub fn new() -> Self {
        Self {
            registry: prometheus::default_registry().clone(),
        }
    }

    /// Record an HTTP request with its latency.
    ///
    /// # Arguments
    ///
    /// * `method` — HTTP verb (GET, POST, …).
    /// * `path` — Request path (e.g. `"/api/v1/scans"`).
    /// * `status` — HTTP status code.
    /// * `duration` — Request duration in seconds.
    pub fn record_request(&self, method: &str, path: &str, status: u16, duration: f64) {
        let status_str = status.to_string();
        REQUESTS_TOTAL
            .with_label_values(&[method, path, &status_str])
            .inc();
        REQUEST_DURATION_SECONDS
            .with_label_values(&[method, path])
            .observe(duration);
    }

    /// Record the duration of a custom domain-specific operation.
    ///
    /// Use this for operations like `"roof_scan"`, `"image_processing"`,
    /// `"report_generation"`, etc.
    ///
    /// # Arguments
    ///
    /// * `operation` — Operation name.
    /// * `duration` — Duration in seconds.
    pub fn record_custom_duration(&self, operation: &str, duration: f64) {
        CUSTOM_DURATION_SECONDS
            .with_label_values(&[operation])
            .observe(duration);
    }

    /// Record (increment) an event counter.
    ///
    /// # Arguments
    ///
    /// * `event_type` — Event type (e.g. `"lead_generated"`,
    ///   `"scan_completed"`).
    pub fn record_event(&self, event_type: &str) {
        EVENTS_TOTAL.with_label_values(&[event_type]).inc();
    }

    /// Record model inference latency.
    ///
    /// # Arguments
    ///
    /// * `model` — Model identifier / version.
    /// * `duration` — Inference duration in seconds.
    pub fn record_inference(&self, model: &str, duration: f64) {
        INFERENCE_DURATION_SECONDS
            .with_label_values(&[model])
            .observe(duration);
    }

    /// Record an outbound API call.
    ///
    /// # Arguments
    ///
    /// * `service` — External service name.
    /// * `status` — HTTP status code returned by the service.
    pub fn record_api_call(&self, service: &str, status: u16) {
        API_CALLS_TOTAL
            .with_label_values(&[service, &status.to_string()])
            .inc();
    }

    /// Set a gauge value for a specific operation type.
    ///
    /// # Arguments
    ///
    /// * `operation_type` — Operation type (e.g. `"active_scans"`).
    /// * `value` — Gauge value.
    pub fn set_gauge(&self, operation_type: &str, value: f64) {
        ACTIVE_OPERATIONS
            .with_label_values(&[operation_type])
            .set(value);
    }

    /// Increment a gauge by 1.
    ///
    /// # Arguments
    ///
    /// * `operation_type` — Operation type.
    pub fn inc_gauge(&self, operation_type: &str) {
        ACTIVE_OPERATIONS
            .with_label_values(&[operation_type])
            .inc();
    }

    /// Decrement a gauge by 1 (won't go below 0).
    ///
    /// # Arguments
    ///
    /// * `operation_type` — Operation type.
    pub fn dec_gauge(&self, operation_type: &str) {
        ACTIVE_OPERATIONS
            .with_label_values(&[operation_type])
            .dec();
    }

    /// Return the current Prometheus metrics snapshot in text exposition
    /// format.
    pub fn get_prometheus_metrics(&self) -> String {
        let encoder = TextEncoder::new();
        let metric_families = self.registry.gather();
        let mut buffer = Vec::new();
        encoder.encode(&metric_families, &mut buffer).unwrap_or_default();
        String::from_utf8(buffer).unwrap_or_default()
    }
}

impl Default for MetricsCollector {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Singleton
// ---------------------------------------------------------------------------

use std::sync::LazyLock;

/// Global singleton instance of `MetricsCollector`.
static COLLECTOR: LazyLock<Mutex<MetricsCollector>> =
    LazyLock::new(|| Mutex::new(MetricsCollector::new()));

/// Return the global `MetricsCollector` singleton.
///
/// # Panics
///
/// Panics if the singleton mutex is poisoned (another thread panicked while
/// holding it).
pub fn get_collector() -> std::sync::MutexGuard<'static, MetricsCollector> {
    COLLECTOR.lock().expect("metrics collector mutex poisoned")
}
