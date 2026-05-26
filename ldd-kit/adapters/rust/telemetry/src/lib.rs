//! LDD-Kit Telemetry Library for Rust
//!
//! Drop-in observability infrastructure providing structured logging,
//! OpenTelemetry tracing, Prometheus metrics, HTTP middleware, and
//! failure-analysis utilities for Rust services.
//!
//! # Modules
//!
//! - `logging` — Structured JSON logging with `tracing` + `tracing-subscriber`
//! - `tracing` — OpenTelemetry tracer setup + trace-context propagation
//! - `metrics` — Prometheus metrics (Counter, Histogram, Gauge) collection
//! - `middleware` — Axum middleware for request instrumentation
//! - `remediation` — Failure analyzer with pattern matching for common errors
//!
//! # Quick Start
//!
//! ```no_run
//! use telemetry::{init_logging, init_tracing, get_collector, MetricsCollector};
//!
//! #[tokio::main]
//! async fn main() {
//!     init_logging("json", "info");
//!     let _provider = init_tracing("my-service", Some("http://otel-collector:4318"));
//!
//!     let collector = get_collector();
//!     collector.record_request("GET", "/api/health", 200, 0.012);
//! }
//! ```

pub mod logging;
pub mod metrics;
pub mod middleware;
pub mod remediation;
pub mod tracing;

// Re-export commonly used items for convenience.

// Logging
pub use logging::init_logging;

// Tracing
pub use tracing::{init_tracing, inject_trace_context, shutdown_tracing};

// Metrics
pub use metrics::{get_collector, MetricsCollector};

// Middleware
pub use middleware::telemetry_middleware;

// Remediation
pub use remediation::{CorrelatedIncident, FailureAnalyzer, RemediationSuggestion};
