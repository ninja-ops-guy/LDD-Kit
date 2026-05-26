/**
 * Structured logging module using `pino` — the fastest Node.js JSON logger.
 *
 * Provides JSON/console log rendering, async context propagation via
 * AsyncLocalStorage, and a withLogContext helper for binding trace/tenant/user
 * identifiers.
 *
 * @module telemetry/logging
 */

import pino, { Logger as PinoLogger } from 'pino';
import { AsyncLocalStorage } from 'async_hooks';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Supported log formats. */
export type LogFormat = 'json' | 'console';

/** Supported log levels. */
export type LogLevel = 'debug' | 'info' | 'warn' | 'error' | 'fatal' | 'trace';

/** Logger instance exported from this module. */
export type Logger = PinoLogger;

// ---------------------------------------------------------------------------
// Internal state
// ---------------------------------------------------------------------------

/** Global root logger instance. */
let rootLogger: PinoLogger = pino({ level: 'info' });

/** Per-name logger cache. */
const namedLoggers = new Map<string, PinoLogger>();

/** AsyncLocalStorage for log context propagation across async boundaries. */
const logContextStorage = new AsyncLocalStorage<Record<string, unknown>>();

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/**
 * Configure the global pino logger with the specified format and level.
 *
 * @param format - `"json"` for production (machine-readable), `"console"` for
 *   development (human-readable with colors).
 * @param level - Log level threshold. One of: `debug`, `info`, `warn`, `error`,
 *   `fatal`, `trace`.
 *
 * @example
 * ```ts
 * configureLogging('json', 'info');
 * ```
 */
export function configureLogging(format: LogFormat, level: LogLevel): void {
  const options: pino.LoggerOptions = {
    level,
    base: { pid: process.pid, hostname: require('os').hostname() },
  };

  if (format === 'console') {
    options.transport = {
      target: 'pino-pretty',
      options: {
        colorize: true,
        translateTime: 'SYS:standard',
        ignore: 'pid,hostname',
      },
    };
  }

  rootLogger = pino(options);

  // Clear named logger cache so new fetches pick up the config.
  namedLoggers.clear();
}

// ---------------------------------------------------------------------------
// Logger factory
// ---------------------------------------------------------------------------

/**
 * Return a named pino Logger. The name is added as the `component` field on
 * every log line.
 *
 * @param name - Logger / module name (e.g. `__filename`).
 * @returns A pino Logger bound with the component name.
 *
 * @example
 * ```ts
 * const logger = getLogger('scan-service');
 * logger.info({ scanId: '123' }, 'scan started');
 * ```
 */
export function getLogger(name: string): PinoLogger {
  const cached = namedLoggers.get(name);
  if (cached) return cached;

  const logger = rootLogger.child({ component: name });
  namedLoggers.set(name, logger);
  return logger;
}

// ---------------------------------------------------------------------------
// Async context propagation
// ---------------------------------------------------------------------------

/**
 * Get the current log context from AsyncLocalStorage.
 * Returns an empty object if no context is active.
 */
function getCurrentContext(): Record<string, unknown> {
  return logContextStorage.getStore() ?? {};
}

/**
 * Execute `fn` within a new async context that carries the provided log
 * fields. Any loggers created or used inside `fn` will automatically merge
 * these fields into every log entry.
 *
 * @param context - Key/value pairs to bind to the async context.
 * @param fn - Function to execute within the context.
 * @returns The return value of `fn`.
 *
 * @example
 * ```ts
 * const result = await withLogContext(
 *   { trace_id: 'abc', tenant_id: 't1' },
 *   async () => {
 *     getLogger('worker').info('processing'); // includes trace_id, tenant_id
 *     return await doWork();
 *   }
 * );
 * ```
 */
export function withLogContext<T>(
  context: Record<string, unknown>,
  fn: () => T
): T {
  const parent = getCurrentContext();
  const merged = { ...parent, ...context };
  return logContextStorage.run(merged, fn);
}

/**
 * Wrap an async function so that it always executes within the current log
 * context, preserving context across promise boundaries.
 *
 * @param fn - Async function to wrap.
 * @returns A new function that preserves log context.
 */
export function withLogContextAsync<TArgs extends unknown[], TReturn>(
  fn: (...args: TArgs) => Promise<TReturn>
): (...args: TArgs) => Promise<TReturn> {
  const snapshot = getCurrentContext();
  return async (...args: TArgs): Promise<TReturn> => {
    return logContextStorage.run(snapshot, () => fn(...args));
  };
}

// ---------------------------------------------------------------------------
// Context-aware logger
// ---------------------------------------------------------------------------

/**
 * Return a logger that automatically includes all fields from the current
 * async log context. This is the preferred way to get a logger inside request
 * handlers or background jobs.
 *
 * @param name - Logger / module name.
 * @returns A pino Logger with async context fields merged in.
 */
export function getContextualLogger(name: string): PinoLogger {
  const base = getLogger(name);
  const context = getCurrentContext();

  if (Object.keys(context).length === 0) {
    return base;
  }

  return base.child(context);
}

// ---------------------------------------------------------------------------
// Convenience exports
// ---------------------------------------------------------------------------

/** The global root logger for direct use. */
export { rootLogger };

/**
 * Log an HTTP request in a consistent structured format.
 *
 * @param logger - pino Logger instance.
 * @param method - HTTP verb.
 * @param path - Request path.
 * @param status - HTTP status code.
 * @param durationMs - Request duration in milliseconds.
 * @param requestId - Unique request identifier.
 */
export function logRequest(
  logger: PinoLogger,
  method: string,
  path: string,
  status: number,
  durationMs: number,
  requestId: string
): void {
  logger.info(
    { method, path, status, duration_ms: durationMs, request_id: requestId },
    'request_completed'
  );
}

/**
 * Log an error in a consistent structured format.
 *
 * @param logger - pino Logger instance.
 * @param err - Error object.
 * @param msg - Human-readable message.
 */
export function logError(logger: PinoLogger, err: unknown, msg: string): void {
  const errorType = err instanceof Error ? err.constructor.name : typeof err;
  const errorMessage = err instanceof Error ? err.message : String(err);

  logger.error(
    { error_type: errorType, error_message: errorMessage, err },
    msg
  );
}
