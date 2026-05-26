// Package telemetry provides OpenTelemetry tracing helpers for Go services.
//
// This file contains tracer initialization, span wrappers, and trace-context
// propagation utilities.
package telemetry

import (
	"context"
	"fmt"
	"os"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/stdout/stdouttrace"
	"go.opentelemetry.io/otel/propagation"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/sdk/resource"
	semconv "go.opentelemetry.io/otel/semconv/v1.24.0"
	"go.opentelemetry.io/otel/trace"
)

var (
	// tracerProvider holds the globally configured TracerProvider.
	tracerProvider *sdktrace.TracerProvider

	// defaultTracer is the default tracer instance used by Span().
	defaultTracer trace.Tracer
)

// InitTracing initialises the global OpenTelemetry tracer provider.
//
// If otlpEndpoint is non-nil, an OTLP exporter is configured (best-effort;
// falls back to stdout on error). If otlpEndpoint is nil, a ConsoleSpanExporter
// is used for development.
//
// The returned TracerProvider should be shut down on application exit:
//
//	provider := telemetry.InitTracing("my-service", &endpoint)
//	defer provider.Shutdown(context.Background())
func InitTracing(serviceName string, otlpEndpoint *string) *sdktrace.TracerProvider {
	res := resource.NewWithAttributes(
		semconv.SchemaURL,
		semconv.ServiceName(serviceName),
		semconv.ServiceVersion("1.0.0"),
	)

	var exporter sdktrace.SpanExporter
	var err error

	if otlpEndpoint != nil && *otlpEndpoint != "" {
		// Attempt to create OTLP exporter. If it fails, fall back to stdout.
		exporter, err = newOTLPExporter(*otlpEndpoint)
		if err != nil {
			fmt.Fprintf(os.Stderr, "telemetry: OTLP exporter failed (%v), falling back to stdout\n", err)
			exporter, _ = stdouttrace.New(stdouttrace.WithPrettyPrint())
		}
	} else {
		exporter, _ = stdouttrace.New(stdouttrace.WithPrettyPrint())
	}

	provider := sdktrace.NewTracerProvider(
		sdktrace.WithResource(res),
		sdktrace.WithBatcher(exporter),
		sdktrace.WithSampler(sdktrace.AlwaysSample()),
	)

	otel.SetTracerProvider(provider)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))

	tracerProvider = provider
	defaultTracer = provider.Tracer(serviceName)

	return provider
}

// newOTLPExporter creates an OTLP trace exporter. This is a placeholder that
// attempts to use the gRPC exporter. Users should import
// go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc in their
// main package for compile-time safety.
func newOTLPExporter(endpoint string) (sdktrace.SpanExporter, error) {
	return nil, fmt.Errorf("OTLP exporter not compiled in; import otlptracegrpc in your main package or use stdout exporter")
}

// GetTracer returns a named Tracer from the global provider.
func GetTracer(name string) trace.Tracer {
	if tracerProvider == nil {
		return otel.Tracer(name)
	}
	return tracerProvider.Tracer(name)
}

// Span creates a new span, executes fn within it, and handles error recording.
// The span is automatically ended when fn returns.
//
// Usage:
//
//	err := telemetry.Span(ctx, "process_image", func(ctx context.Context) error {
//	    return processImage(ctx, data)
//	})
func Span(ctx context.Context, name string, fn func(context.Context) error) error {
	t := defaultTracer
	if t == nil {
		t = otel.Tracer("default")
	}
	ctx, span := t.Start(ctx, name)
	defer span.End()

	err := fn(ctx)
	if err != nil {
		span.RecordError(err)
		span.SetAttributes(attribute.Bool("error", true))
	}
	return err
}

// SpanWithAttributes creates a new span with pre-set attributes, executes fn,
// and handles error recording.
func SpanWithAttributes(
	ctx context.Context,
	name string,
	attrs []attribute.KeyValue,
	fn func(context.Context) error,
) error {
	t := defaultTracer
	if t == nil {
		t = otel.Tracer("default")
	}
	ctx, span := t.Start(ctx, name)
	defer span.End()

	span.SetAttributes(attrs...)

	err := fn(ctx)
	if err != nil {
		span.RecordError(err)
		span.SetAttributes(attribute.Bool("error", true))
	}
	return err
}

// TraceID extracts the trace ID from the current span in ctx as a hex string.
// Returns an empty string if no span is active.
func TraceID(ctx context.Context) string {
	span := trace.SpanFromContext(ctx)
	if !span.SpanContext().IsValid() {
		return ""
	}
	return span.SpanContext().TraceID().String()
}

// SpanID extracts the span ID from the current span in ctx as a hex string.
// Returns an empty string if no span is active.
func SpanID(ctx context.Context) string {
	span := trace.SpanFromContext(ctx)
	if !span.SpanContext().IsValid() {
		return ""
	}
	return span.SpanContext().SpanID().String()
}

// InjectTraceContext injects the current span context from ctx into the
// provided headers map using the W3C TraceContext propagator.
func InjectTraceContext(ctx context.Context, headers map[string]string) {
	if headers == nil {
		return
	}
	propagator := propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	)
	carrier := make(propagation.MapCarrier)
	propagator.Inject(ctx, carrier)
	for k, v := range carrier {
		headers[k] = v
	}
}

// ExtractTraceContext extracts trace context from incoming headers and
// returns an updated context.Context carrying the extracted span context.
func ExtractTraceContext(ctx context.Context, headers map[string]string) context.Context {
	propagator := propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	)
	return propagator.Extract(ctx, propagation.MapCarrier(headers))
}

// ShutdownTracing gracefully flushes and shuts down the tracer provider.
func ShutdownTracing(ctx context.Context) error {
	if tracerProvider == nil {
		return nil
	}
	return tracerProvider.Shutdown(ctx)
}
