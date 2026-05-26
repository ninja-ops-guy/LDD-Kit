//! OpenTelemetry tracing helpers for Rust.
//!
//! Initialises a `TracerProvider`, provides trace-context propagation via W3C
//! TraceContext, and integrates with the `tracing` crate via
//! `tracing-opentelemetry`.
//!
//! The `#[tracing::instrument]` attribute from the `tracing` crate provides
//! automatic span creation for functions. This module handles the global
//! tracer provider setup and context propagation.
//!
//! # Example
//!
//! ```no_run
//! use telemetry::init_tracing;
//! use tracing::{info, instrument};
//!
//! #[tokio::main]
//! async fn main() {
//!     let _provider = init_tracing("my-service", Some("http://otel-collector:4318"));
//!
//!     process_image(vec![0u8; 1024]).await;
//! }
//!
//! #[tracing::instrument(fields(image_size = data.len()))]
//! async fn process_image(data: Vec<u8>) {
//!     info!(result = "ok", "image processed");
//! }
//! ```

use opentelemetry::{
    global,
    propagation::TextMapPropagator,
    sdk::{
        propagation::TraceContextPropagator,
        resource::Resource,
        trace::{self, TracerProvider},
        runtime::Tokio,
    },
    trace::{TraceError, Tracer},
    KeyValue,
};
use opentelemetry_otlp::WithExportConfig;
use std::collections::HashMap;
use tracing_opentelemetry::OpenTelemetryLayer;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

/// Initialise the global OpenTelemetry tracer provider.
///
/// If `otlp_endpoint` is provided, an OTLP HTTP exporter is configured. If
/// not, a stdout exporter is used for development.
///
/// # Arguments
///
/// * `service_name` — Value for the `service.name` resource attribute.
/// * `otlp_endpoint` — OTLP HTTP endpoint URL (e.g.
///   `"http://otel-collector:4318/v1/traces"`). If `None`, spans are printed
///   to stdout.
///
/// # Returns
///
/// The configured `TracerProvider`. This should be held for the lifetime of
/// the application and shut down on exit:
///
/// ```no_run
/// use telemetry::{init_tracing, shutdown_tracing};
///
/// #[tokio::main]
/// async fn main() {
///     let provider = init_tracing("my-service", None);
///     // ... application logic ...
///     shutdown_tracing();
/// }
/// ```
pub fn init_tracing(service_name: &str, otlp_endpoint: Option<&str>) -> TracerProvider {
    let resource = Resource::new(vec![KeyValue::new(
        opentelemetry_semantic_conventions::resource::SERVICE_NAME,
        service_name.to_string(),
    )]);

    let exporter: Box<dyn trace::SpanExporter + Send + Sync> = match otlp_endpoint {
        Some(endpoint) => {
            let otlp_exporter = opentelemetry_otlp::new_exporter()
                .http()
                .with_endpoint(endpoint.to_string());

            match otlp_exporter.build_span_exporter() {
                Ok(exporter) => Box::new(exporter),
                Err(e) => {
                    eprintln!(
                        "telemetry: OTLP exporter failed ({}), falling back to stdout",
                        e
                    );
                    Box::new(
                        trace::stdout::new_pipeline()
                            .with_trace_config(
                                trace::config().with_resource(resource.clone()),
                            )
                            .install_simple(),
                    )
                }
            }
        }
        None => Box::new(
            trace::stdout::new_pipeline()
                .with_trace_config(trace::config().with_resource(resource.clone()))
                .install_simple(),
        ),
    };

    let provider = TracerProvider::builder()
        .with_resource(resource)
        .with_batch_exporter(exporter, Tokio)
        .build();

    global::set_tracer_provider(provider.clone());

    // Set up the TraceContext propagator for W3C traceparent/tracestate.
    global::set_text_map_propagator(TraceContextPropagator::new());

    // Install the tracing-opentelemetry layer so that #[tracing::instrument]
    // spans are exported via OTel.
    let otel_layer = OpenTelemetryLayer::new(provider.tracer(service_name));

    // Only install the subscriber if one hasn't already been set.
    // This is a best-effort — in production, the application should set up
    // the full subscriber in main().
    let _ = tracing_subscriber::registry()
        .with(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .with(otel_layer)
        .try_init();

    provider
}

/// Get a named `Tracer` from the global provider.
///
/// # Arguments
///
/// * `name` — Tracer name — conventionally the module path.
pub fn get_tracer(name: &'static str) -> opentelemetry::global::BoxedTracer {
    global::tracer(name)
}

/// Shutdown the global tracer provider, flushing any pending spans.
///
/// Call this before your application exits.
pub fn shutdown_tracing() {
    global::shutdown_tracer_provider();
}

/// Inject the current span context from the active tracing span into the
/// provided headers map using the W3C TraceContext propagator.
///
/// # Arguments
///
/// * `headers` — Mutable `HashMap` to inject trace context into.
///
/// # Example
///
/// ```no_run
/// use telemetry::inject_trace_context;
/// use std::collections::HashMap;
///
/// let mut headers = HashMap::new();
/// headers.insert("content-type".to_string(), "application/json".to_string());
/// inject_trace_context(&mut headers);
/// // headers now contains traceparent and tracestate
/// ```
pub fn inject_trace_context(headers: &mut HashMap<String, String>) {
    let propagator = TraceContextPropagator::new();
    let mut injector = HeaderInjector { headers };
    propagator.inject_context(&tracing::Span::current().context(), &mut injector);
}

/// Extract trace context from incoming headers and return an OpenTelemetry
/// `Context` carrying the extracted span.
///
/// # Arguments
///
/// * `headers` — Incoming headers that may contain traceparent.
///
/// # Returns
///
/// An OpenTelemetry `Context` with the extracted span context, or an empty
/// context if extraction fails.
pub fn extract_trace_context(headers: &HashMap<String, String>) -> opentelemetry::Context {
    let propagator = TraceContextPropagator::new();
    let extractor = HeaderExtractor { headers };
    propagator.extract(&extractor)
}

/// Extract the trace ID from the current active span as a hex string.
/// Returns an empty string if no span is active.
pub fn trace_id() -> String {
    let span = tracing::Span::current();
    let ctx = span.context();
    let span_context = ctx.span().span_context();
    if span_context.is_valid() {
        span_context.trace_id().to_string()
    } else {
        String::new()
    }
}

/// Extract the span ID from the current active span as a hex string.
/// Returns an empty string if no span is active.
pub fn span_id() -> String {
    let span = tracing::Span::current();
    let ctx = span.context();
    let span_context = ctx.span().span_context();
    if span_context.is_valid() {
        span_context.span_id().to_string()
    } else {
        String::new()
    }
}

// ---------------------------------------------------------------------------
// Internal: HeaderInjector / HeaderExtractor for W3C propagation
// ---------------------------------------------------------------------------

/// Implements `Injector` for HashMap-based headers.
struct HeaderInjector<'a> {
    headers: &'a mut HashMap<String, String>,
}

impl<'a> opentelemetry::propagation::Injector for HeaderInjector<'a> {
    fn set(&mut self, key: &str, value: String) {
        self.headers.insert(key.to_lowercase(), value);
    }
}

/// Implements `Extractor` for HashMap-based headers.
struct HeaderExtractor<'a> {
    headers: &'a HashMap<String, String>,
}

impl<'a> opentelemetry::propagation::Extractor for HeaderExtractor<'a> {
    fn get(&self, key: &str) -> Option<&str> {
        self.headers
            .get(&key.to_lowercase())
            .map(|v| v.as_str())
    }

    fn keys(&self) -> Vec<&str> {
        self.headers.keys().map(|k| k.as_str()).collect()
    }
}
