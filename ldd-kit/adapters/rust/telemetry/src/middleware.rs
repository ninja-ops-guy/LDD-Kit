//! HTTP telemetry middleware for Rust (Axum).
//!
//! Provides middleware that generates request IDs, times requests, records
//! Prometheus metrics, and injects trace context into response headers.
//!
//! # Axum Usage
//!
//! ```no_run
//! use axum::{Router, routing::get};
//! use telemetry::telemetry_middleware;
//!
//! let app = Router::new()
//!     .route("/api/health", get(health_handler))
//!     .layer(telemetry_middleware());
//! ```
//!
//! # Actix-web Usage (in comments)
//!
//! For Actix-web, you can use a similar pattern with `actix_web::middleware::Transform`:
//!
//! ```ignore
//! use actix_web::{dev::ServiceRequest, Error};
//! use actix_web::middleware::Next;
//!
//! pub async fn telemetry_middleware_actix(
//!     req: ServiceRequest,
//!     next: Next<impl actix_web::body::MessageBody>,
//! ) -> Result<actix_web::dev::ServiceResponse<impl actix_web::body::MessageBody>, Error> {
//!     let request_id = uuid::Uuid::new_v4().to_string();
//!     let start = std::time::Instant::now();
//!
//!     // ... inject request_id into request extensions ...
//!
//!     let res = next.call(req).await?;
//!
//!     // ... record metrics, log request ...
//!
//!     Ok(res)
//! }
//! ```

use axum::{
    body::Body,
    extract::Request,
    http::{HeaderMap, HeaderValue, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
};
use std::time::Instant;
use tracing::{info, instrument, Span};
use uuid::Uuid;

use crate::logging;
use crate::metrics::get_collector;
use crate::tracing::{inject_trace_context, trace_id};

/// Header key for the generated request ID.
pub const HEADER_REQUEST_ID: &str = "x-request-id";

/// Header key for the trace ID in responses.
pub const HEADER_TRACE_ID: &str = "x-trace-id";

/// Axum middleware function that instruments HTTP requests with telemetry.
///
/// For every request the middleware:
/// 1. Generates an `x-request-id` (UUID4).
/// 2. Extracts traceparent from incoming headers (if present).
/// 3. Times the request and records Prometheus metrics.
/// 4. Injects `trace_id` and `request_id` into response headers.
/// 5. Logs the request with structured fields.
///
/// Use with `axum::middleware::from_fn`:
///
/// ```no_run
/// use axum::Router;
/// use telemetry::telemetry_middleware;
///
/// let app = Router::new().layer(axum::middleware::from_fn(telemetry_middleware));
/// ```
#[instrument(name = "http_request", skip_all, fields(method = %req.method(), path = %req.uri().path()))]
pub async fn telemetry_middleware(req: Request, next: Next) -> Response {
    let request_id = generate_request_id(&req);
    let method = req.method().to_string();
    let path = req.uri().path().to_string();
    let start = Instant::now();

    // Extract trace context from incoming headers.
    let incoming_headers = extract_headers_map(req.headers());
    let _otel_ctx = crate::tracing::extract_trace_context(&incoming_headers);

    // Record request ID and trace ID in the tracing span.
    let trace_id_str = trace_id();
    Span::current().record("request_id", &request_id.as_str());
    if !trace_id_str.is_empty() {
        Span::current().record("trace_id", &trace_id_str.as_str());
    }

    // Process the request.
    let mut response = next.run(req).await;

    // Calculate duration.
    let duration = start.elapsed().as_secs_f64();
    let status = response.status();
    let status_u16 = status.as_u16();

    // Record Prometheus metrics.
    {
        let collector = get_collector();
        collector.record_request(&method, &path, status_u16, duration);
    }

    // Inject response headers.
    let headers = response.headers_mut();
    headers.insert(HEADER_REQUEST_ID, HeaderValue::from_str(&request_id).unwrap_or_else(|_| HeaderValue::from_static("unknown")));
    if !trace_id_str.is_empty() {
        headers.insert(
            HEADER_TRACE_ID,
            HeaderValue::from_str(&trace_id_str).unwrap_or_else(|_| HeaderValue::from_static("")),
        );
    }

    // Log the request.
    let duration_ms = duration * 1000.0;
    info!(
        method = %method,
        path = %path,
        status = status_u16,
        duration_ms = %format!("{:.2}", duration_ms),
        trace_id = %trace_id_str,
        request_id = %request_id,
        "request_completed"
    );

    response
}

/// Create a Tower `Layer` for the telemetry middleware.
///
/// This is the recommended way to apply the middleware to an Axum router:
///
/// ```no_run
/// use axum::Router;
/// use telemetry::telemetry_layer;
///
/// let app = Router::new().layer(telemetry_layer());
/// ```
pub fn telemetry_layer() -> axum::middleware::from_fn::Layer<
    impl Fn(Request, Next) -> std::pin::Pin<Box<dyn std::future::Future<Output = Response> + Send>>,
> {
    axum::middleware::from_fn(telemetry_middleware)
}

/// Tower-compatible middleware factory.
///
/// Returns a closure suitable for use with `tower::ServiceBuilder`:
///
/// ```no_run
/// use tower::ServiceBuilder;
/// use telemetry::telemetry_service;
///
/// let service = ServiceBuilder::new()
///     .layer(telemetry_service())
///     .service(my_handler);
/// ```
pub fn telemetry_service<S>() -> tower::layer::util::Identity {
    // This is a placeholder — in practice you'd use tower::layer::Layer.
    // The axum::middleware::from_fn approach above is preferred.
    tower::layer::util::Identity::new()
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/// Generate or reuse a request ID from the incoming request.
fn generate_request_id(req: &Request) -> String {
    req.headers()
        .get(HEADER_REQUEST_ID)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string())
        .unwrap_or_else(|| Uuid::new_v4().to_string())
}

/// Extract headers from HeaderMap into a HashMap for trace propagation.
fn extract_headers_map(headers: &HeaderMap) -> std::collections::HashMap<String, String> {
    let mut map = std::collections::HashMap::new();
    for (key, value) in headers.iter() {
        if let Ok(v) = value.to_str() {
            map.insert(key.as_str().to_string(), v.to_string());
        }
    }
    map
}
