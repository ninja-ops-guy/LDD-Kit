// Package telemetry provides HTTP middleware for Go services.
//
// This file contains both a Gin middleware and a generic net/http middleware
// that generate request IDs, time requests, record Prometheus metrics, and
// inject trace context into response headers.
package telemetry

import (
	"context"
	"fmt"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"
)

const (
	// HeaderRequestID is the header key for the generated request ID.
	HeaderRequestID = "X-Request-ID"
	// HeaderTraceID is the header key for the trace ID in responses.
	HeaderTraceID = "X-Trace-ID"
)

// TelemetryMiddleware returns a gin.HandlerFunc that instruments HTTP
// requests with:
//   - X-Request-ID generation (UUID4)
//   - OpenTelemetry span creation
//   - Request timing + Prometheus metrics recording
//   - Trace ID injection in response headers
//   - Structured request logging
func TelemetryMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		requestID := c.GetHeader(HeaderRequestID)
		if requestID == "" {
			requestID = uuid.New().String()
		}
		c.Header(HeaderRequestID, requestID)

		method := c.Request.Method
		path := c.Request.URL.Path
		if path == "" {
			path = "/"
		}

		start := time.Now()

		// Extract trace context from incoming request headers.
		parentCtx := ExtractTraceContext(c.Request.Context(), headersToMap(c.Request.Header))
		c.Request = c.Request.WithContext(parentCtx)

		tracer := GetTracer("telemetry-middleware")
		ctx, span := tracer.Start(
			parentCtx,
			fmt.Sprintf("%s %s", method, path),
			trace.WithSpanKind(trace.SpanKindServer),
			trace.WithAttributes(
				attribute.String("http.method", method),
				attribute.String("http.path", path),
				attribute.String("http.request_id", requestID),
			),
		)
		defer span.End()

		// Bind trace_id to logging context.
		traceID := TraceID(ctx)
		lc := NewLogContext(ctx).WithTraceID(traceID).WithField("request_id", requestID)
		logCtx := lc.Context()

		// Store logger in Gin context for handler access.
		logger := LoggerFromContext(logCtx)
		_ = logger

		// Process the request.
		c.Next()

		// Capture results.
		duration := time.Since(start).Seconds()
		status := c.Writer.Status()
		if status == 0 {
			status = http.StatusOK
		}

		// Set span attributes.
		span.SetAttributes(attribute.Int("http.status_code", status))
		if len(c.Errors) > 0 {
			span.RecordError(c.Errors.Last())
			span.SetAttributes(attribute.Bool("error", true))
		}

		// Inject trace ID into response headers.
		if traceID != "" {
			c.Header(HeaderTraceID, traceID)
		}

		// Record Prometheus metrics.
		collector := GetCollector()
		collector.RecordRequest(method, path, status, duration)

		// Log the request.
		LogRequest(logCtx, method, path, status, duration*1000, requestID)
	}
}

// TelemetryHandler wraps an http.Handler with telemetry instrumentation.
// Use it when not using Gin:
//
//	handler := telemetry.TelemetryHandler(yourMux)
//	http.ListenAndServe(":8080", handler)
func TelemetryHandler(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requestID := r.Header.Get(HeaderRequestID)
		if requestID == "" {
			requestID = uuid.New().String()
		}
		w.Header().Set(HeaderRequestID, requestID)

		method := r.Method
		path := r.URL.Path
		if path == "" {
			path = "/"
		}

		start := time.Now()

		// Extract trace context from incoming headers.
		parentCtx := ExtractTraceContext(r.Context(), headersToMap(r.Header))
		r = r.WithContext(parentCtx)

		// Wrap the response writer to capture status code.
		rw := &responseRecorder{ResponseWriter: w, statusCode: http.StatusOK}

		tracer := GetTracer("telemetry-middleware")
		ctx, span := tracer.Start(
			parentCtx,
			fmt.Sprintf("%s %s", method, path),
			trace.WithSpanKind(trace.SpanKindServer),
			trace.WithAttributes(
				attribute.String("http.method", method),
				attribute.String("http.path", path),
				attribute.String("http.request_id", requestID),
			),
		)
		defer span.End()

		// Bind trace_id to logging context.
		traceID := TraceID(ctx)
		lc := NewLogContext(ctx).WithTraceID(traceID).WithField("request_id", requestID)
		logCtx := lc.Context()

		// Store context in request.
		r = r.WithContext(logCtx)

		// Process the request.
		next.ServeHTTP(rw, r)

		// Capture results.
		duration := time.Since(start).Seconds()
		status := rw.statusCode

		// Set span attributes.
		span.SetAttributes(attribute.Int("http.status_code", status))

		// Inject trace ID into response headers.
		if traceID != "" {
			w.Header().Set(HeaderTraceID, traceID)
		}

		// Record Prometheus metrics.
		collector := GetCollector()
		collector.RecordRequest(method, path, status, duration)

		// Log the request.
		LogRequest(logCtx, method, path, status, duration*1000, requestID)
	})
}

// responseRecorder wraps http.ResponseWriter to capture the status code.
type responseRecorder struct {
	http.ResponseWriter
	statusCode int
	written    bool
}

// WriteHeader captures the status code and delegates to the underlying writer.
func (rr *responseRecorder) WriteHeader(code int) {
	if !rr.written {
		rr.statusCode = code
		rr.written = true
		rr.ResponseWriter.WriteHeader(code)
	}
}

// Write delegates to the underlying ResponseWriter, capturing status 200 on first write.
func (rr *responseRecorder) Write(b []byte) (int, error) {
	if !rr.written {
		rr.WriteHeader(http.StatusOK)
	}
	return rr.ResponseWriter.Write(b)
}

// headersToMap converts http.Header to a simple map[string]string for
// trace context extraction.
func headersToMap(h http.Header) map[string]string {
	m := make(map[string]string, len(h))
	for k, v := range h {
		if len(v) > 0 {
			m[k] = v[0]
		}
	}
	return m
}

// RequestIDFromContext extracts the request ID from the context.
// Returns empty string if not found.
func RequestIDFromContext(ctx context.Context) string {
	if v, ok := ctx.Value("request_id").(string); ok {
		return v
	}
	return ""
}
