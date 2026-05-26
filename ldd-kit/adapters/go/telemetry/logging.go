// Package telemetry provides structured logging, distributed tracing, Prometheus
// metrics, HTTP middleware, and failure-analysis utilities for Go services.
//
// The logging sub-package uses rs/zerolog for high-performance JSON logging
// with context propagation via context.Context.
package telemetry

import (
	"context"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/rs/zerolog"
)

// logCtxKey is the private key type used to store *zerolog.Logger in context.
type logCtxKey struct{}

var (
	// rootLogger is the globally configured root logger.
	rootLogger zerolog.Logger

	// loggers holds per-name cached logger instances.
	loggers = make(map[string]*zerolog.Logger)

	// loggersMu protects the loggers map.
	loggersMu sync.RWMutex

	// configureOnce ensures global configuration happens exactly once.
	configureOnce sync.Once
)

func init() {
	// Default to JSON output until ConfigureLogging is called.
	rootLogger = zerolog.New(os.Stdout).With().Timestamp().Logger()
}

// ConfigureLogging sets up zerolog with the specified format and level.
//
// format: "json" for production JSON output, "console" for human-readable
// colored output (development).
// level: one of "debug", "info", "warn", "error", "fatal", "panic".
//
// This function is safe to call multiple times; only the first call has effect.
func ConfigureLogging(format string, level string) {
	configureOnce.Do(func() {
		configure(format, level)
	})
}

// configure performs the actual logger setup.
func configure(format string, level string) {
	lvl := parseLevel(level)

	var baseLogger zerolog.Logger

	switch strings.ToLower(format) {
	case "console":
		output := zerolog.ConsoleWriter{
			Out:        os.Stdout,
			TimeFormat: time.RFC3339,
			NoColor:    false,
		}
		baseLogger = zerolog.New(output).Level(lvl).With().Timestamp().Caller().Logger()
	case "json":
		fallthrough
	default:
		baseLogger = zerolog.New(os.Stdout).Level(lvl).With().Timestamp().Logger()
	}

	rootLogger = baseLogger
	zerolog.DefaultContextLogger = &rootLogger

	// Reset per-name cache so new loggers pick up the configuration.
	loggersMu.Lock()
	loggers = make(map[string]*zerolog.Logger)
	loggersMu.Unlock()
}

// parseLevel converts a string level to zerolog.Level.
func parseLevel(level string) zerolog.Level {
	switch strings.ToLower(level) {
	case "debug":
		return zerolog.DebugLevel
	case "info":
		return zerolog.InfoLevel
	case "warn", "warning":
		return zerolog.WarnLevel
	case "error":
		return zerolog.ErrorLevel
	case "fatal":
		return zerolog.FatalLevel
	case "panic":
		return zerolog.PanicLevel
	default:
		return zerolog.InfoLevel
	}
}

// GetLogger returns a named *zerolog.Logger. The name is added as the
// "component" field. Loggers are cached per name.
func GetLogger(name string) *zerolog.Logger {
	loggersMu.RLock()
	l, ok := loggers[name]
	loggersMu.RUnlock()
	if ok {
		return l
	}

	loggersMu.Lock()
	defer loggersMu.Unlock()

	// Double-check after acquiring write lock.
	if l, ok := loggers[name]; ok {
		return l
	}

	logger := rootLogger.With().Str("component", name).Logger()
	loggers[name] = &logger
	return &logger
}

// LoggerFromContext extracts a *zerolog.Logger from ctx. If none is stored,
// the global root logger is returned.
func LoggerFromContext(ctx context.Context) *zerolog.Logger {
	if l, ok := ctx.Value(logCtxKey{}).(*zerolog.Logger); ok && l != nil {
		return l
	}
	return &rootLogger
}

// WithContext returns a context that carries the provided logger.
func WithContext(ctx context.Context, logger *zerolog.Logger) context.Context {
	return context.WithValue(ctx, logCtxKey{}, logger)
}

// LogContext is a zerolog-specific context wrapper that manages trace_id,
// tenant_id, user_id, and other contextual fields via context.Context.
//
// Usage:
//
//	lc := telemetry.NewLogContext(ctx)
//	ctx = lc.WithTraceID("abc123").WithTenantID("tenant-1").Context()
//	telemetry.LoggerFromContext(ctx).Info().Msg("processing request")
type LogContext struct {
	ctx context.Context
}

// NewLogContext creates a new LogContext wrapping the provided context.
func NewLogContext(ctx context.Context) *LogContext {
	return &LogContext{ctx: ctx}
}

// Context returns the underlying context.Context, carrying any bound fields.
func (lc *LogContext) Context() context.Context {
	return lc.ctx
}

// WithTraceID binds trace_id into the logging context.
func (lc *LogContext) WithTraceID(traceID string) *LogContext {
	logger := LoggerFromContext(lc.ctx).With().Str("trace_id", traceID).Logger()
	lc.ctx = WithContext(lc.ctx, &logger)
	return lc
}

// WithSpanID binds span_id into the logging context.
func (lc *LogContext) WithSpanID(spanID string) *LogContext {
	logger := LoggerFromContext(lc.ctx).With().Str("span_id", spanID).Logger()
	lc.ctx = WithContext(lc.ctx, &logger)
	return lc
}

// WithTenantID binds tenant_id into the logging context.
func (lc *LogContext) WithTenantID(tenantID string) *LogContext {
	logger := LoggerFromContext(lc.ctx).With().Str("tenant_id", tenantID).Logger()
	lc.ctx = WithContext(lc.ctx, &logger)
	return lc
}

// WithUserID binds user_id into the logging context.
func (lc *LogContext) WithUserID(userID string) *LogContext {
	logger := LoggerFromContext(lc.ctx).With().Str("user_id", userID).Logger()
	lc.ctx = WithContext(lc.ctx, &logger)
	return lc
}

// WithField binds a single custom field into the logging context.
func (lc *LogContext) WithField(key string, value interface{}) *LogContext {
	logger := LoggerFromContext(lc.ctx).With().Interface(key, value).Logger()
	lc.ctx = WithContext(lc.ctx, &logger)
	return lc
}

// WithFields binds multiple custom fields into the logging context.
func (lc *LogContext) WithFields(fields map[string]interface{}) *LogContext {
	logger := LoggerFromContext(lc.ctx).With()
	for k, v := range fields {
		logger = logger.Interface(k, v)
	}
	l := logger.Logger()
	lc.ctx = WithContext(lc.ctx, &l)
	return lc
}

// Info returns an info-level event for the context-bound logger.
func Info(ctx context.Context) *zerolog.Event {
	return LoggerFromContext(ctx).Info()
}

// Debug returns a debug-level event for the context-bound logger.
func Debug(ctx context.Context) *zerolog.Event {
	return LoggerFromContext(ctx).Debug()
}

// Warn returns a warn-level event for the context-bound logger.
func Warn(ctx context.Context) *zerolog.Event {
	return LoggerFromContext(ctx).Warn()
}

// Error returns an error-level event for the context-bound logger.
func Error(ctx context.Context) *zerolog.Event {
	return LoggerFromContext(ctx).Error()
}

// Fatal returns a fatal-level event for the context-bound logger.
func Fatal(ctx context.Context) *zerolog.Event {
	return LoggerFromContext(ctx).Fatal()
}

// LogRequest logs an HTTP request in a consistent structured format.
func LogRequest(ctx context.Context, method, path string, status int, durationMs float64, requestID string) {
	LoggerFromContext(ctx).Info().
		Str("method", method).
		Str("path", path).
		Int("status", status).
		Float64("duration_ms", durationMs).
		Str("request_id", requestID).
		Msg("request_completed")
}

// LogError logs an error in a consistent structured format.
func LogError(ctx context.Context, err error, msg string) {
	LoggerFromContext(ctx).Error().
		Err(err).
		Str("error_type", fmt.Sprintf("%T", err)).
		Msg(msg)
}
