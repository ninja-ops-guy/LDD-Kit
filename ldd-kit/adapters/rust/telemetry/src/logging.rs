//! Structured logging configuration using `tracing` + `tracing-subscriber`.
//!
//! Provides JSON or pretty (console) log formatting, context propagation via
//! `tracing::Span`, and field-based structured logging compatible with the
//! ecosystem of tracing-compatible tools.
//!
//! # Example
//!
//! ```no_run
//! use telemetry::init_logging;
//! use tracing::{info, info_span};
//!
//! init_logging("json", "info");
//!
//! let span = info_span!("process_request", trace_id = "abc123", tenant_id = "t1");
//! let _guard = span.enter();
//! info!(scan_id = "scan-42", "processing roof scan");
//! ```

use tracing_subscriber::{
    fmt::{self, format::FmtSpan},
    layer::SubscriberExt,
    util::SubscriberInitExt,
    EnvFilter,
};

/// Initialise the global `tracing` subscriber with the specified format and
/// level.
///
/// # Arguments
///
/// * `format` — `"json"` for production (machine-readable JSON output) or
///   `"console"` for development (human-readable coloured output).
/// * `level` — Log level threshold. One of: `trace`, `debug`, `info`, `warn`,
///   `error`.
///
/// # Example
///
/// ```no_run
/// use telemetry::init_logging;
///
/// // Production: JSON logs
/// init_logging("json", "info");
///
/// // Development: coloured console output
/// // init_logging("console", "debug");
/// ```
pub fn init_logging(format: &str, level: &str) {
    let env_filter = EnvFilter::try_new(level).unwrap_or_else(|_| EnvFilter::new("info"));

    match format.to_lowercase().as_str() {
        "console" | "pretty" => {
            let fmt_layer = fmt::layer()
                .with_target(true)
                .with_thread_ids(true)
                .with_thread_names(true)
                .with_line_number(true)
                .with_file(true)
                .with_span_events(FmtSpan::CLOSE)
                .pretty();

            tracing_subscriber::registry()
                .with(env_filter)
                .with(fmt_layer)
                .init();
        }
        "json" | _ => {
            let fmt_layer = fmt::layer()
                .json()
                .with_target(true)
                .with_thread_ids(true)
                .with_thread_names(true)
                .with_line_number(true)
                .with_file(true)
                .with_span_events(FmtSpan::CLOSE)
                .with_current_span(true)
                .with_span_list(false);

            tracing_subscriber::registry()
                .with(env_filter)
                .with(fmt_layer)
                .init();
        }
    }
}

/// Initialise logging from environment variables.
///
/// Reads `RUST_LOG` for the log level and `LOG_FORMAT` for the format
/// (`json` or `console`). Falls back to the provided defaults.
///
/// # Arguments
///
/// * `default_format` — Default format if `LOG_FORMAT` is not set.
/// * `default_level` — Default level if `RUST_LOG` is not set.
///
/// # Example
///
/// ```no_run
/// use telemetry::init_logging_from_env;
///
/// init_logging_from_env("json", "info");
/// ```
pub fn init_logging_from_env(default_format: &str, default_level: &str) {
    let format = std::env::var("LOG_FORMAT").unwrap_or_else(|_| default_format.to_string());
    let level = std::env::var("RUST_LOG").unwrap_or_else(|_| default_level.to_string());
    init_logging(&format, &level);
}

// Convenience re-exports of tracing macros — users can use `tracing`
// directly, but these are available for consistency.
pub use tracing::{debug, error, info, trace, warn};
