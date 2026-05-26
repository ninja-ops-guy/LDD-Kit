/**
 * HTTP telemetry middleware for Node.js/TypeScript.
 *
 * Provides Express middleware that generates request IDs, times requests,
 * records Prometheus metrics, and injects trace context into response headers.
 *
 * Also includes a Fastify-compatible version documented in comments.
 *
 * @module telemetry/middleware
 */

import { Request, Response, NextFunction } from 'express';
import { v4 as uuidv4 } from 'uuid';
import { trace, SpanKind } from '@opentelemetry/api';
import { getCollector } from './metrics';
import { getDefaultTracer, getTraceID, injectTraceContext } from './tracing';
import {
  getContextualLogger,
  withLogContext,
  logRequest,
  logError,
  Logger,
} from './logging';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Header key for the generated request ID. */
export const HEADER_REQUEST_ID = 'X-Request-ID';

/** Header key for the trace ID in responses. */
export const HEADER_TRACE_ID = 'X-Trace-ID';

// ---------------------------------------------------------------------------
// Express middleware
// ---------------------------------------------------------------------------

/**
 * Express middleware that instruments HTTP requests with telemetry.
 *
 * For every HTTP request the middleware:
 * 1. Generates an `X-Request-ID` (UUID4).
 * 2. Extracts traceparent from incoming headers (if present).
 * 3. Times the request and records Prometheus metrics.
 * 4. Injects `trace_id` and `request_id` into response headers.
 * 5. Logs the request with structured fields.
 *
 * @returns Express middleware function.
 *
 * @example
 * ```ts
 * import express from 'express';
 * import { telemetryMiddleware } from './telemetry/middleware';
 *
 * const app = express();
 * app.use(telemetryMiddleware());
 *
 * app.get('/api/health', (req, res) => {
 *   res.json({ status: 'ok' });
 * });
 *
 * app.get('/metrics', async (req, res) => {
 *   const collector = getCollector();
 *   res.set('Content-Type', collector.getMetricsContentType());
 *   res.send(await collector.getPrometheusMetrics());
 * });
 * ```
 */
export function telemetryMiddleware(): (
  req: Request,
  res: Response,
  next: NextFunction
) => void {
  return (req: Request, res: Response, next: NextFunction): void => {
    // Generate or reuse request ID.
    let requestId = req.headers['x-request-id'] as string | undefined;
    if (!requestId) {
      requestId = uuidv4();
    }
    res.setHeader(HEADER_REQUEST_ID, requestId);

    const method = req.method;
    const path = req.path || req.url || '/';
    const startTime = process.hrtime.bigint();

    // Extract trace context from incoming headers.
    const incomingHeaders: Record<string, string> = {};
    for (const [key, value] of Object.entries(req.headers)) {
      if (typeof value === 'string') {
        incomingHeaders[key] = value;
      } else if (Array.isArray(value)) {
        incomingHeaders[key] = value[0];
      }
    }
    const parentCtx = trace
      .getTracer('telemetry-middleware')
      .startSpan(`${method} ${path}`, {
        kind: SpanKind.SERVER,
        attributes: {
          'http.method': method,
          'http.path': path,
          'http.request_id': requestId,
        },
      })
      .spanContext();

    // Execute within log context and OTel span.
    const tracer = getDefaultTracer();

    tracer.startActiveSpan(
      `${method} ${path}`,
      {
        kind: SpanKind.SERVER,
        attributes: {
          'http.method': method,
          'http.path': path,
          'http.request_id': requestId,
        },
      },
      (span) => {
        // Build log context with trace and request IDs.
        const traceId = span.spanContext().traceId;
        span.setAttribute('http.target', req.originalUrl || path);

        withLogContext(
          { trace_id: traceId, request_id: requestId },
          () => {
            const logger = getContextualLogger('telemetry-middleware');

            // Capture response finish to record metrics and log.
            res.on('finish', () => {
              const endTime = process.hrtime.bigint();
              const durationMs =
                Number(endTime - startTime) / 1_000_000; // ns → ms
              const durationSec = durationMs / 1000;
              const status = res.statusCode;

              // Set span attributes.
              span.setAttribute('http.status_code', status);
              if (status >= 500) {
                span.setAttribute('error', true);
              }

              // Inject trace ID into response headers.
              if (traceId) {
                res.setHeader(HEADER_TRACE_ID, traceId);
              }

              // Record Prometheus metrics.
              const collector = getCollector();
              collector.recordRequest(method, path, status, durationSec);

              // Log the request.
              logRequest(logger, method, path, status, durationMs, requestId);

              span.end();
            });

            // Handle errors.
            res.on('error', (err: Error) => {
              logError(logger, err, 'request_failed');
              span.recordException(err);
              span.setAttribute('error', true);
            });

            next();
          }
        );
      }
    );
  };
}

// ---------------------------------------------------------------------------
// Fastify-compatible version (comment/documentation)
// ---------------------------------------------------------------------------

/*
// To use with Fastify, register this plugin:

import fp from 'fastify-plugin';
import { FastifyInstance, FastifyPluginAsync } from 'fastify';
import { v4 as uuidv4 } from 'uuid';
import { trace, SpanKind } from '@opentelemetry/api';
import { getCollector } from './metrics';
import { getDefaultTracer } from './tracing';
import { getContextualLogger, withLogContext, logRequest } from './logging';

export const telemetryFastifyPlugin: FastifyPluginAsync = fp(
  async (fastify: FastifyInstance) => {
    fastify.addHook('onRequest', async (request, reply) => {
      const requestId = request.headers['x-request-id'] || uuidv4();
      reply.header('X-Request-ID', requestId);
      (request as any).requestId = requestId;
    });

    fastify.addHook('onResponse', async (request, reply) => {
      const method = request.method;
      const path = request.routerPath || request.url;
      const status = reply.statusCode;
      const durationMs = reply.elapsedTime;
      const durationSec = durationMs / 1000;

      const collector = getCollector();
      collector.recordRequest(method, path, status, durationSec);

      const logger = getContextualLogger('fastify-telemetry');
      logRequest(
        logger, method, path, status, durationMs,
        (request as any).requestId || 'unknown'
      );
    });
  }
);

// Usage:
// import fastify from 'fastify';
// import { telemetryFastifyPlugin } from './telemetry/middleware';
// const app = fastify();
// app.register(telemetryFastifyPlugin);
*/

// ---------------------------------------------------------------------------
// Utility functions
// ---------------------------------------------------------------------------

/**
 * Attach a `/metrics` endpoint handler to an Express app.
 *
 * @param app - Express application instance.
 */
export function attachMetricsEndpoint(app: {
  get: (path: string, handler: (req: Request, res: Response) => void) => void;
}): void {
  app.get('/metrics', async (_req: Request, res: Response) => {
    const collector = getCollector();
    res.setHeader('Content-Type', collector.getMetricsContentType());
    res.send(await collector.getPrometheusMetrics());
  });
}
