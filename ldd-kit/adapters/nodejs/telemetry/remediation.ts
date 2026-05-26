/**
 * Failure analysis and remediation suggestion engine.
 *
 * Parses structured logs, identifies recurring error patterns, correlates
 * related incidents by trace context, and produces actionable remediation
 * suggestions with confidence scores.
 *
 * @module telemetry/remediation
 */

// ---------------------------------------------------------------------------
// Data types
// ---------------------------------------------------------------------------

/** A single remediation suggestion produced by FailureAnalyzer. */
export interface RemediationSuggestion {
  /** Short title of the identified issue. */
  title: string;
  /** Detailed description of the problem. */
  description: string;
  /** Confidence score from 0.0 to 1.0. */
  confidence: number;
  /** Ordered list of suggested remediation actions. */
  suggested_actions: string[];
}

/** A cluster of related error events sharing a trace or temporal window. */
export interface CorrelatedIncident {
  /** Unique incident identifier. */
  incident_id: string;
  /** Trace ID if events are trace-correlated. */
  trace_id: string | null;
  /** Earliest event timestamp. */
  start_time: Date;
  /** Latest event timestamp. */
  end_time: Date;
  /** Number of events in the cluster. */
  event_count: number;
  /** Detected error type patterns. */
  error_types: string[];
  /** Most likely root cause pattern. */
  root_cause_pattern: string | null;
  /** List of affected services. */
  affected_services: string[];
}

/** A structured log entry for analysis. */
export interface LogEntry {
  /** Log message content. */
  message?: string;
  /** Event name (structlog style). */
  event?: string;
  /** Error message text. */
  error_message?: string;
  /** Log level (debug, info, warning, error, critical). */
  level?: string;
  /** Log level alias (structlog style). */
  log_level?: string;
  /** ISO 8601 timestamp. */
  timestamp?: string;
  /** Trace identifier for correlation. */
  trace_id?: string;
  /** Service name. */
  service?: string;
  /** Service name alias. */
  service_name?: string;
  /** Additional arbitrary fields. */
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Failure Analyzer
// ---------------------------------------------------------------------------

/**
 * Analyse structured log entries and produce remediation suggestions.
 *
 * The analyser uses regex-based pattern matching to recognise common
 * failure modes (GPU OOM, timeouts, connection errors, etc.), groups
 * correlated events by *trace_id* and time window, and returns ranked
 * suggestions.
 *
 * @example
 * ```ts
 * const analyzer = new FailureAnalyzer();
 * const suggestions = analyzer.analyze(logEntries);
 * console.log(suggestions[0]?.title); // "GPU Out-of-Memory Errors"
 * ```
 */
export class FailureAnalyzer {
  /** Regex patterns for common error signatures. */
  private readonly patterns: Record<string, RegExp> = {
    gpu_oom: /(?i)(cuda out of memory|gpu oom|torch\.cuda\.OutOfMemory|out of memory.*gpu|nvml.*memory)/,
    timeout: /(?i)(timeout|timed out|deadline exceeded|context deadline|read timeout|connect timeout|request timeout)/,
    connection_error: /(?i)(connection refused|connection reset|connection closed|broken pipe|no route to host|dns.*fail|network.*unreachable|cannot connect|connection.*error)/,
    rate_limit: /(?i)(rate limit|too many requests|429|throttle|quota exceeded|limit exceeded|capacity exceeded)/,
    auth_failure: /(?i)(unauthorized|authentication.*fail|403|401|invalid.*token|token.*expired|credentials.*invalid|access denied|forbidden)/,
    database_error: /(?i)(database.*error|sql.*error|psycopg|sqlite|pymongo|connection.*pool|lock.*timeout|deadlock|constraint.*fail)/,
    vision_model_error: /(?i)(inference.*fail|model.*load|onnx.*error|tensorrt|inference.*timeout|model.*timeout|vision.*error|yolo.*error|detectron|tesseract.*error)/,
    memory_pressure: /(?i)(memory.*exhausted|oom killed|killed process|memory.*pressure|swap.*full)/,
    dependency_unavailable: /(?i)(service.*unavailable|503|dependency.*fail|health.*check.*fail|circuit breaker|upstream.*unavailable)/,
  };

  /**
   * Match a log message against known error patterns.
   *
   * @param message - Log message text.
   * @returns The pattern key (e.g. `"gpu_oom"`) or `null`.
   */
  private detectErrorType(message: string): string | null {
    for (const [key, pattern] of Object.entries(this.patterns)) {
      if (pattern.test(message)) {
        return key;
      }
    }
    return null;
  }

  /**
   * Analyse a list of structured log entries and produce suggestions.
   *
   * @param logs - Structured log entries (e.g. from pino JSON output).
   * @returns Ranked list of remediation suggestions.
   */
  analyze(logs: LogEntry[]): RemediationSuggestion[] {
    const suggestions: RemediationSuggestion[] = [];
    if (!logs || logs.length === 0) {
      return suggestions;
    }

    // ---- Pattern frequency analysis --------------------------------------
    const errorCounts: Record<string, number> = {};
    const errorLogs: Record<string, LogEntry[]> = {};

    for (const entry of logs) {
      const msg = this.extractMessage(entry);
      const level = this.extractLevel(entry);
      if (level === 'ERROR' || level === 'CRITICAL' || level === 'EXCEPTION') {
        const errType = this.detectErrorType(msg);
        if (errType) {
          errorCounts[errType] = (errorCounts[errType] || 0) + 1;
          if (!errorLogs[errType]) errorLogs[errType] = [];
          errorLogs[errType].push(entry);
        }
      }
    }

    // ---- Generate suggestions based on detected patterns ------------------
    const sortedErrors = Object.entries(errorCounts).sort((a, b) => b[1] - a[1]);
    for (const [errType, count] of sortedErrors) {
      const suggestion = this.suggestForPattern(errType, count, errorLogs[errType]);
      if (suggestion) {
        suggestions.push(suggestion);
      }
    }

    // ---- Spike detection --------------------------------------------------
    if (logs.length >= 2) {
      const timestamps = this.extractTimestamps(logs);
      if (timestamps.length >= 2) {
        timestamps.sort((a, b) => a.getTime() - b.getTime());
        const timeWindow =
          (timestamps[timestamps.length - 1].getTime() - timestamps[0].getTime()) / 1000;
        if (timeWindow > 0) {
          const totalErrors = Object.values(errorCounts).reduce((a, b) => a + b, 0);
          const errorRate = totalErrors / Math.max(timeWindow / 60, 1);
          if (errorRate > 10) {
            suggestions.push({
              title: 'Error Rate Spike Detected',
              description: `Detected ${errorRate.toFixed(1)} errors per minute over ${Math.round(timeWindow)} seconds. Consider scaling or circuit-breaker activation.`,
              confidence: Math.min(0.95, errorRate / 50),
              suggested_actions: [
                'Check auto-scaling configuration',
                'Verify circuit breaker thresholds',
                'Review downstream service health',
                'Consider temporary traffic reduction',
              ],
            });
          }
        }
      }
    }

    // ---- Repeated error detection -----------------------------------------
    for (const [errType, count] of Object.entries(errorCounts)) {
      if (count >= 5) {
        const label = errType.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
        suggestions.push({
          title: `Recurring ${label} Errors`,
          description: `${count} occurrences of '${errType}' detected. This suggests a persistent issue requiring attention.`,
          confidence: Math.min(0.9, 0.5 + count / 20),
          suggested_actions: [
            'Review service configuration',
            'Check resource allocation',
            'Verify downstream dependency health',
            'Consider rolling back recent deployments',
          ],
        });
      }
    }

    return suggestions;
  }

  /**
   * Group related error events by trace_id and time window.
   *
   * @param events - Structured error events, each optionally containing a
   *   `trace_id` and `timestamp`.
   * @returns Grouped incident clusters sorted by severity (event count).
   */
  correlate(events: LogEntry[]): CorrelatedIncident[] {
    if (!events || events.length === 0) {
      return [];
    }

    // Index events by trace_id
    const byTrace: Record<string, LogEntry[]> = {};
    const noTrace: LogEntry[] = [];

    for (const evt of events) {
      const tid = evt.trace_id;
      if (tid && typeof tid === 'string') {
        if (!byTrace[tid]) byTrace[tid] = [];
        byTrace[tid].push(evt);
      } else {
        noTrace.push(evt);
      }
    }

    const incidents: CorrelatedIncident[] = [];
    const now = new Date();

    // ---- Group by trace_id -----------------------------------------------
    for (const [traceId, evts] of Object.entries(byTrace)) {
      const incident = this.buildIncident(traceId, evts);
      if (incident) {
        incidents.push(incident);
      }
    }

    // ---- Group no-trace events by time window (5 min) --------------------
    if (noTrace.length > 0) {
      noTrace.sort((a, b) => {
        const ta = this.parseTimestamp(a.timestamp);
        const tb = this.parseTimestamp(b.timestamp);
        return ta.getTime() - tb.getTime();
      });

      const windowMs = 5 * 60 * 1000;
      let currentGroup: LogEntry[] = [];

      for (const evt of noTrace) {
        const ts = this.parseTimestamp(evt.timestamp);

        if (currentGroup.length === 0) {
          currentGroup.push(evt);
          continue;
        }

        const lastTs = this.parseTimestamp(currentGroup[currentGroup.length - 1].timestamp);
        if (ts.getTime() - lastTs.getTime() <= windowMs) {
          currentGroup.push(evt);
        } else {
          const inc = this.buildIncident(null, currentGroup);
          if (inc) {
            incidents.push(inc);
          }
          currentGroup = [evt];
        }
      }

      if (currentGroup.length > 0) {
        const inc = this.buildIncident(null, currentGroup);
        if (inc) {
          incidents.push(inc);
        }
      }
    }

    // Sort by event count descending
    incidents.sort((a, b) => b.event_count - a.event_count);
    return incidents;
  }

  // -------------------------------------------------------------------------
  // Private helpers
  // -------------------------------------------------------------------------

  /** Build a CorrelatedIncident from a group of events. */
  private buildIncident(
    traceId: string | null,
    events: LogEntry[]
  ): CorrelatedIncident | null {
    if (!events || events.length === 0) {
      return null;
    }

    const timestamps: Date[] = [];
    const errorTypes: string[] = [];
    const services: string[] = [];
    const seenErrors = new Set<string>();
    const seenServices = new Set<string>();

    for (const evt of events) {
      const ts = this.parseTimestamp(evt.timestamp);
      if (!isNaN(ts.getTime())) {
        timestamps.push(ts);
      }

      const msg = this.extractMessage(evt);
      const err = this.detectErrorType(msg);
      if (err && !seenErrors.has(err)) {
        seenErrors.add(err);
        errorTypes.push(err);
      }

      const svc = evt.service || evt.service_name;
      if (typeof svc === 'string' && !seenServices.has(svc)) {
        seenServices.add(svc);
        services.push(svc);
      }
    }

    let startTime: Date;
    let endTime: Date;
    if (timestamps.length > 0) {
      startTime = new Date(Math.min(...timestamps.map((t) => t.getTime())));
      endTime = new Date(Math.max(...timestamps.map((t) => t.getTime())));
    } else {
      startTime = new Date();
      endTime = new Date();
    }

    const rootCause = errorTypes.length > 0 ? errorTypes[0] : null;

    let incId: string;
    if (traceId) {
      incId = `inc-${traceId.slice(0, 12)}-${events.length}`;
    } else {
      const ts = startTime.toISOString().replace(/[-:T.Z]/g, '').slice(0, 14);
      incId = `inc-${ts}-${events.length}`;
    }

    return {
      incident_id: incId,
      trace_id: traceId,
      start_time: startTime,
      end_time: endTime,
      event_count: events.length,
      error_types: errorTypes,
      root_cause_pattern: rootCause,
      affected_services: services,
    };
  }

  /** Return a RemediationSuggestion for a recognised pattern. */
  private suggestForPattern(
    pattern: string,
    count: number,
    _logs: LogEntry[]
  ): RemediationSuggestion | null {
    const suggestionsMap: Record<string, RemediationSuggestion> = {
      gpu_oom: {
        title: 'GPU Out-of-Memory Errors',
        description: `${count} GPU OOM error(s) detected. The vision model is exhausting GPU memory during inference.`,
        confidence: Math.min(0.95, 0.6 + count / 10),
        suggested_actions: [
          'Reduce batch size for vision inference',
          'Enable model quantization (FP16 / INT8)',
          'Scale GPU nodes horizontally',
          'Implement request queueing with back-pressure',
          'Consider model sharding for large inputs',
        ],
      },
      timeout: {
        title: 'Request Timeout Errors',
        description: `${count} timeout error(s) detected. Downstream services or inference is taking longer than expected.`,
        confidence: Math.min(0.9, 0.5 + count / 15),
        suggested_actions: [
          'Increase timeout thresholds temporarily',
          'Review slow query / endpoint performance',
          'Enable async processing for long-running operations',
          'Add caching for frequently accessed data',
          'Scale affected service replicas',
        ],
      },
      connection_error: {
        title: 'Connection Errors',
        description: `${count} connection error(s) detected. Network issues or downstream service unavailability.`,
        confidence: Math.min(0.85, 0.5 + count / 15),
        suggested_actions: [
          'Verify downstream service health endpoints',
          'Check DNS resolution and network policies',
          'Review connection pool configuration',
          'Enable retry with exponential backoff',
          'Check firewall / security group rules',
        ],
      },
      rate_limit: {
        title: 'Rate Limiting / Throttling',
        description: `${count} rate-limit error(s) detected. External API quotas are being exceeded.`,
        confidence: Math.min(0.88, 0.55 + count / 12),
        suggested_actions: [
          'Implement client-side rate limiting',
          'Add request caching to reduce API calls',
          'Request quota increase from provider',
          'Enable request batching',
          'Add circuit breaker for external APIs',
        ],
      },
      auth_failure: {
        title: 'Authentication Failures',
        description: `${count} authentication error(s) detected. Token expiry or credential misconfiguration.`,
        confidence: Math.min(0.82, 0.5 + count / 15),
        suggested_actions: [
          'Check token expiry and refresh logic',
          'Verify API key / credential configuration',
          'Review IAM policies and permissions',
          'Rotate credentials if compromised',
          'Audit recent permission changes',
        ],
      },
      database_error: {
        title: 'Database Errors',
        description: `${count} database error(s) detected. Connection pool exhaustion or query issues.`,
        confidence: Math.min(0.85, 0.5 + count / 15),
        suggested_actions: [
          'Check connection pool size and usage',
          'Review slow query log',
          'Verify database replication lag',
          'Add read replicas for query offload',
          'Check for lock contention and deadlocks',
        ],
      },
      vision_model_error: {
        title: 'Vision Model Inference Errors',
        description: `${count} vision model error(s) detected. Model loading or inference pipeline failure.`,
        confidence: Math.min(0.88, 0.55 + count / 12),
        suggested_actions: [
          'Verify model artifact availability in storage',
          'Check model version compatibility',
          'Validate input image format and size',
          'Restart inference worker pods',
          'Roll back to previous model version',
        ],
      },
      memory_pressure: {
        title: 'System Memory Pressure',
        description: `${count} memory pressure event(s) detected. Host-level memory exhaustion detected.`,
        confidence: Math.min(0.85, 0.5 + count / 12),
        suggested_actions: [
          'Increase container memory limits',
          'Add memory-based Horizontal Pod Autoscaler',
          'Review memory leaks in application code',
          'Enable swap or increase node memory',
          'Restart affected services to reclaim memory',
        ],
      },
      dependency_unavailable: {
        title: 'Dependency Service Unavailable',
        description: `${count} dependency unavailable error(s) detected. Required downstream service is not reachable.`,
        confidence: Math.min(0.87, 0.52 + count / 14),
        suggested_actions: [
          'Check health status of all downstream services',
          'Verify service discovery / registry configuration',
          'Review recent deployment changes',
          'Enable graceful degradation mode',
          'Scale downstream service replicas',
        ],
      },
    };

    return suggestionsMap[pattern] || null;
  }

  /** Extract the message string from a log entry. */
  private extractMessage(entry: LogEntry): string {
    return (
      entry.event ||
      entry.message ||
      entry.error_message ||
      ''
    );
  }

  /** Extract and normalize the log level from a log entry. */
  private extractLevel(entry: LogEntry): string {
    const level = entry.log_level || entry.level || '';
    return level.toUpperCase();
  }

  /** Parse an ISO 8601 timestamp string into a Date. */
  private parseTimestamp(ts: string | undefined): Date {
    if (!ts) return new Date();
    const d = new Date(ts);
    return isNaN(d.getTime()) ? new Date() : d;
  }

  /** Extract all valid timestamps from log entries. */
  private extractTimestamps(logs: LogEntry[]): Date[] {
    return logs
      .map((l) => this.parseTimestamp(l.timestamp))
      .filter((d) => !isNaN(d.getTime()));
  }
}

// ---------------------------------------------------------------------------
// Singleton helper
// ---------------------------------------------------------------------------

let globalAnalyzer: FailureAnalyzer | null = null;

/**
 * Return the global FailureAnalyzer singleton.
 */
export function getFailureAnalyzer(): FailureAnalyzer {
  if (!globalAnalyzer) {
    globalAnalyzer = new FailureAnalyzer();
  }
  return globalAnalyzer;
}
