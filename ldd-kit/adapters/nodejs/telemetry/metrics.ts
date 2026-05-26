/**
 * Prometheus metrics collector for Node.js/TypeScript.
 *
 * Defines counters, histograms, and gauges using `prom-client` and exposes a
 * `MetricsCollector` singleton for use throughout the application.
 *
 * @module telemetry/metrics
 */

import {
  Registry,
  Counter,
  Histogram,
  Gauge,
  collectDefaultMetrics,
  register as globalRegister,
} from 'prom-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Labels for the requests_total counter. */
interface RequestLabels {
  method: string;
  path: string;
  status: string;
}

/** Labels for the request_duration_seconds histogram. */
interface DurationLabels {
  method: string;
  path: string;
}

/** Labels for the custom_duration_seconds histogram. */
interface CustomDurationLabels {
  operation: string;
}

/** Labels for the events_total counter. */
interface EventLabels {
  event_type: string;
}

/** Labels for the inference_duration_seconds histogram. */
interface InferenceLabels {
  model: string;
}

/** Labels for the api_calls_total counter. */
interface APICallLabels {
  service: string;
  status: string;
}

/** Labels for the active_operations gauge. */
interface GaugeLabels {
  operation_type: string;
}

// ---------------------------------------------------------------------------
// Metric definitions
// ---------------------------------------------------------------------------

/** Prometheus counter for total HTTP requests. */
const requestsTotal = new Counter<keyof RequestLabels>({
  name: 'service_requests_total',
  help: 'Total HTTP requests received',
  labelNames: ['method', 'path', 'status'],
  registers: [globalRegister],
});

/** Prometheus histogram for HTTP request latency. */
const requestDurationSeconds = new Histogram<keyof DurationLabels>({
  name: 'service_request_duration_seconds',
  help: 'HTTP request latency in seconds',
  labelNames: ['method', 'path'],
  buckets: [0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0],
  registers: [globalRegister],
});

/** Prometheus histogram for custom operation durations (scans, processing, etc.). */
const customDurationSeconds = new Histogram<keyof CustomDurationLabels>({
  name: 'service_custom_duration_seconds',
  help: 'Custom operation duration in seconds (e.g. scan, processing)',
  labelNames: ['operation'],
  buckets: [1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
  registers: [globalRegister],
});

/** Prometheus counter for total events processed. */
const eventsTotal = new Counter<keyof EventLabels>({
  name: 'service_events_total',
  help: 'Total events processed',
  labelNames: ['event_type'],
  registers: [globalRegister],
});

/** Prometheus histogram for model inference latency. */
const inferenceDurationSeconds = new Histogram<keyof InferenceLabels>({
  name: 'service_inference_duration_seconds',
  help: 'Model inference latency in seconds',
  labelNames: ['model'],
  registers: [globalRegister],
});

/** Prometheus counter for outbound API calls. */
const apiCallsTotal = new Counter<keyof APICallLabels>({
  name: 'service_api_calls_total',
  help: 'Total outbound API calls',
  labelNames: ['service', 'status'],
  registers: [globalRegister],
});

/** Prometheus gauge for active operations in progress. */
const activeOperations = new Gauge<keyof GaugeLabels>({
  name: 'service_active_operations',
  help: 'Number of operations currently in progress',
  labelNames: ['operation_type'],
  registers: [globalRegister],
});

// ---------------------------------------------------------------------------
// Collector singleton
// ---------------------------------------------------------------------------

/**
 * Singleton facade for updating Prometheus metrics.
 *
 * Thread-safe via the underlying prom-client atomic operations.
 *
 * @example
 * ```ts
 * const collector = getCollector();
 * collector.recordRequest('GET', '/api/scans', 200, 0.045);
 * ```
 */
export class MetricsCollector {
  private static instance: MetricsCollector | null = null;

  /** Private constructor — use `getCollector()`. */
  private constructor() {
    // Enable default Node.js metrics (event loop lag, memory, etc.)
    collectDefaultMetrics({ register: globalRegister });
  }

  /** Get or create the singleton instance. */
  static getInstance(): MetricsCollector {
    if (!MetricsCollector.instance) {
      MetricsCollector.instance = new MetricsCollector();
    }
    return MetricsCollector.instance;
  }

  // -- Recording methods ----------------------------------------------------

  /**
   * Record an HTTP request with its latency.
   *
   * @param method - HTTP verb (GET, POST, …).
   * @param path - Request path (e.g. `/api/v1/scans`).
   * @param status - HTTP status code.
   * @param duration - Request duration in seconds.
   */
  recordRequest(
    method: string,
    path: string,
    status: number,
    duration: number
  ): void {
    const statusStr = String(status);
    requestsTotal.inc({ method, path, status: statusStr });
    requestDurationSeconds.observe({ method, path }, duration);
  }

  /**
   * Record the duration of a custom domain-specific operation.
   *
   * @param operation - Operation name (e.g. `"roof_scan"`, `"image_processing"`).
   * @param duration - Duration in seconds.
   */
  recordCustomDuration(operation: string, duration: number): void {
    customDurationSeconds.observe({ operation }, duration);
  }

  /**
   * Record (increment) an event.
   *
   * @param eventType - Event type (e.g. `"lead_generated"`, `"scan_completed"`).
   */
  recordEvent(eventType: string): void {
    eventsTotal.inc({ event_type: eventType });
  }

  /**
   * Record model inference latency.
   *
   * @param model - Model identifier / version.
   * @param duration - Inference duration in seconds.
   */
  recordInference(model: string, duration: number): void {
    inferenceDurationSeconds.observe({ model }, duration);
  }

  /**
   * Record an outbound API call.
   *
   * @param service - External service name.
   * @param status - HTTP status code returned by the service.
   */
  recordAPICall(service: string, status: number): void {
    apiCallsTotal.inc({ service, status: String(status) });
  }

  /**
   * Set the current value of a gauge.
   *
   * @param operationType - Operation type (e.g. `"active_scans"`).
   * @param value - Gauge value.
   */
  setGauge(operationType: string, value: number): void {
    activeOperations.set({ operation_type: operationType }, value);
  }

  /**
   * Increment a gauge by 1.
   *
   * @param operationType - Operation type.
   */
  incGauge(operationType: string): void {
    activeOperations.inc({ operation_type: operationType });
  }

  /**
   * Decrement a gauge by 1.
   *
   * @param operationType - Operation type.
   */
  decGauge(operationType: string): void {
    activeOperations.dec({ operation_type: operationType });
  }

  // -- Export ---------------------------------------------------------------

  /**
   * Return the current Prometheus metrics snapshot as a string.
   *
   * @returns Text in Prometheus exposition format.
   */
  async getPrometheusMetrics(): Promise<string> {
    return globalRegister.metrics();
  }

  /**
   * Return the Content-Type for Prometheus metrics exposition.
   */
  getMetricsContentType(): string {
    return globalRegister.contentType;
  }
}

// ---------------------------------------------------------------------------
// Singleton getter
// ---------------------------------------------------------------------------

/**
 * Return the global MetricsCollector singleton.
 *
 * @returns The shared metrics collector instance.
 */
export function getCollector(): MetricsCollector {
  return MetricsCollector.getInstance();
}

/**
 * Return the Prometheus metrics as a string (convenience function).
 *
 * @returns Text in Prometheus exposition format.
 */
export async function getPrometheusMetrics(): Promise<string> {
  return globalRegister.metrics();
}

/**
 * Return the global prom-client Registry for advanced use cases.
 */
export function getRegistry(): Registry {
  return globalRegister;
}
