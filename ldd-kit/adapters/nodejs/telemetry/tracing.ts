/**
 * OpenTelemetry tracing helpers for Node.js/TypeScript.
 *
 * Initialises a `NodeSDK`, exposes a `span` async wrapper for automatic
 * function instrumentation, and provides trace-context propagation via W3C
 * TraceContext.
 *
 * @module telemetry/tracing
 */

import { NodeSDK } from '@opentelemetry/sdk-node';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { ConsoleSpanExporter } from '@opentelemetry/sdk-trace-base';
import {
  trace,
  Tracer,
  Span,
  SpanKind,
  SpanStatusCode,
  context as otelContext,
  propagation,
  Context as OtelContext,
} from '@opentelemetry/api';

// ---------------------------------------------------------------------------
// Internal state
// ---------------------------------------------------------------------------

/** The globally initialised NodeSDK instance. */
let sdkInstance: NodeSDK | null = null;

/** Default tracer name used when no specific tracer is requested. */
let defaultTracerName = 'telemetry-default';

// ---------------------------------------------------------------------------
// SDK initialisation
// ---------------------------------------------------------------------------

/**
 * Initialise the global OpenTelemetry NodeSDK.
 *
 * If `otlpEndpoint` is provided, an OTLP HTTP exporter is configured. If not,
 * a `ConsoleSpanExporter` is used for development.
 *
 * **IMPORTANT**: You **must** call `shutdownTracing()` before process exit to
 * flush pending spans.
 *
 * @param serviceName - Value for the `service.name` resource attribute.
 * @param otlpEndpoint - OTLP HTTP endpoint URL (e.g.
 *   `http://otel-collector:4318/v1/traces`). If omitted, spans are printed to
 *   stdout.
 * @returns The configured NodeSDK instance.
 *
 * @example
 * ```ts
 * const sdk = initTracing('my-service', 'http://otel-collector:4318/v1/traces');
 * process.on('SIGTERM', () => sdk.shutdown());
 * ```
 */
export function initTracing(
  serviceName: string,
  otlpEndpoint?: string
): NodeSDK {
  const exporter = otlpEndpoint
    ? new OTLPTraceExporter({ url: otlpEndpoint })
    : new ConsoleSpanExporter();

  sdkInstance = new NodeSDK({
    serviceName,
    traceExporter: exporter,
  });

  sdkInstance.start();
  defaultTracerName = serviceName;

  return sdkInstance;
}

/**
 * Gracefully shut down the tracer provider, flushing any pending spans.
 *
 * @returns Promise that resolves when shutdown is complete.
 */
export async function shutdownTracing(): Promise<void> {
  if (sdkInstance) {
    await sdkInstance.shutdown();
    sdkInstance = null;
  }
}

// ---------------------------------------------------------------------------
// Tracer factory
// ---------------------------------------------------------------------------

/**
 * Return a named `Tracer` from the global provider.
 *
 * @param name - Tracer name — conventionally `__filename` or module name.
 * @returns A tracer instance.
 */
export function getTracer(name: string): Tracer {
  return trace.getTracer(name);
}

/**
 * Return the default tracer for the service.
 */
export function getDefaultTracer(): Tracer {
  return trace.getTracer(defaultTracerName);
}

// ---------------------------------------------------------------------------
// Span wrapper
// ---------------------------------------------------------------------------

/**
 * Execute an async function within a new OpenTelemetry span.
 *
 * The span is automatically ended when `fn` completes (success or failure).
 * If `fn` throws, the error is recorded on the span and the span status is set
 * to ERROR before re-throwing.
 *
 * @param name - Span operation name.
 * @param fn - Async (or sync) function to execute within the span. Receives
 *   the created Span for adding attributes and events.
 * @param kind - Optional span kind (default: `SpanKind.INTERNAL`).
 * @returns A Promise resolving to `fn`'s return value.
 *
 * @example
 * ```ts
 * const result = await span('process_image', async (span) => {
 *   span.setAttribute('image.size', data.length);
 *   return await processImage(data);
 * });
 * ```
 */
export async function span<T>(
  name: string,
  fn: (span: Span) => T | Promise<T>,
  kind: SpanKind = SpanKind.INTERNAL
): Promise<T> {
  const tracer = getDefaultTracer();

  return tracer.startActiveSpan(name, { kind }, async (span) => {
    try {
      const result = await fn(span);
      span.setStatus({ code: SpanStatusCode.OK });
      return result;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : String(err);
      span.recordException(err instanceof Error ? err : new Error(errorMessage));
      span.setStatus({
        code: SpanStatusCode.ERROR,
        message: errorMessage,
      });
      throw err;
    } finally {
      span.end();
    }
  });
}

// ---------------------------------------------------------------------------
// Trace context propagation
// ---------------------------------------------------------------------------

/**
 * Inject the current span context into a headers object.
 *
 * Uses the W3C TraceContext propagator to serialise traceparent / tracestate.
 *
 * @param headers - Mutable headers object to inject into. Defaults to `{}`.
 * @returns The headers object with trace context injected.
 *
 * @example
 * ```ts
 * const headers = injectTraceContext({ 'content-type': 'application/json' });
 * await fetch('http://downstream/api', { headers });
 * ```
 */
export function injectTraceContext(
  headers: Record<string, string> = {}
): Record<string, string> {
  const setter = {
    set(carrier: Record<string, string>, key: string, value: string): void {
      carrier[key] = value;
    },
  };
  propagation.inject(otelContext.active(), headers, setter);
  return headers;
}

/**
 * Extract trace context from incoming headers and return an OpenTelemetry
 * Context carrying the extracted span.
 *
 * @param headers - Incoming headers containing traceparent.
 * @returns An OpenTelemetry Context with the extracted span, or the active
 *   context if extraction fails.
 */
export function extractTraceContext(headers: Record<string, string>): OtelContext {
  const getter = {
    keys(carrier: Record<string, string>): string[] {
      return Object.keys(carrier);
    },
    get(carrier: Record<string, string>, key: string): string | string[] | undefined {
      return carrier[key];
    },
  };
  return propagation.extract(otelContext.active(), headers, getter);
}

/**
 * Extract the trace ID from the current active span as a hex string.
 * Returns an empty string if no span is active.
 */
export function getTraceID(): string {
  const currentSpan = trace.getActiveSpan();
  if (!currentSpan || !currentSpan.spanContext().isValid) {
    return '';
  }
  return currentSpan.spanContext().traceId;
}

/**
 * Extract the span ID from the current active span as a hex string.
 * Returns an empty string if no span is active.
 */
export function getSpanID(): string {
  const currentSpan = trace.getActiveSpan();
  if (!currentSpan || !currentSpan.spanContext().isValid) {
    return '';
  }
  return currentSpan.spanContext().spanId;
}
