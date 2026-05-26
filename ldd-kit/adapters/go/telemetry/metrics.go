// Package telemetry provides Prometheus metrics collection for Go services.
//
// This file defines the MetricsCollector singleton with counters, histograms,
// and gauges following the RoofBot/generic observability specification.
package telemetry

import (
	"fmt"
	"net/http"
	"sync"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	// Global Prometheus metric definitions.

	requestsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "service_requests_total",
			Help: "Total HTTP requests received",
		},
		[]string{"method", "path", "status"},
	)

	requestDurationSeconds = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "service_request_duration_seconds",
			Help:    "HTTP request latency in seconds",
			Buckets: []float64{0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0},
		},
		[]string{"method", "path"},
	)

	customDurationSeconds = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "service_custom_duration_seconds",
			Help:    "Custom operation duration in seconds (e.g. scan, processing)",
			Buckets: []float64{1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0},
		},
		[]string{"operation"},
	)

	eventsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "service_events_total",
			Help: "Total events processed",
		},
		[]string{"event_type"},
	)

	inferenceDurationSeconds = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "service_inference_duration_seconds",
			Help:    "Model inference latency in seconds",
			Buckets: prometheus.DefBuckets,
		},
		[]string{"model"},
	)

	apiCallsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "service_api_calls_total",
			Help: "Total outbound API calls",
		},
		[]string{"service", "status"},
	)

	activeOperations = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{
			Name: "service_active_operations",
			Help: "Number of operations currently in progress",
		},
		[]string{"operation_type"},
	)
)

func init() {
	// Register all metrics with the default Prometheus registry.
	prometheus.MustRegister(
		requestsTotal,
		requestDurationSeconds,
		customDurationSeconds,
		eventsTotal,
		inferenceDurationSeconds,
		apiCallsTotal,
		activeOperations,
	)
}

// MetricsCollector is a singleton facade for updating Prometheus metrics.
//
// All methods are safe for concurrent use.
type MetricsCollector struct{}

var (
	collectorInstance *MetricsCollector
	collectorOnce     sync.Once
)

// GetCollector returns the global MetricsCollector singleton.
func GetCollector() *MetricsCollector {
	collectorOnce.Do(func() {
		collectorInstance = &MetricsCollector{}
	})
	return collectorInstance
}

// RecordRequest records an HTTP request with its latency.
//
// Parameters:
//   - method: HTTP verb (GET, POST, etc.)
//   - path: Request path (e.g. "/api/v1/scans")
//   - status: HTTP status code
//   - duration: Request duration in seconds
func (mc *MetricsCollector) RecordRequest(method, path string, status int, duration float64) {
	statusStr := fmt.Sprintf("%d", status)
	requestsTotal.WithLabelValues(method, path, statusStr).Inc()
	requestDurationSeconds.WithLabelValues(method, path).Observe(duration)
}

// RecordCustomDuration records the duration of a custom operation.
//
// Use this for domain-specific operations like "roof_scan",
// "image_processing", "report_generation", etc.
func (mc *MetricsCollector) RecordCustomDuration(operation string, duration float64) {
	customDurationSeconds.WithLabelValues(operation).Observe(duration)
}

// RecordEvent increments a named event counter.
//
// eventType examples: "lead_generated", "scan_completed", "user_signup".
func (mc *MetricsCollector) RecordEvent(eventType string) {
	eventsTotal.WithLabelValues(eventType).Inc()
}

// RecordInference records model inference latency.
//
// Parameters:
//   - model: Model identifier / version
//   - duration: Inference duration in seconds
func (mc *MetricsCollector) RecordInference(model string, duration float64) {
	inferenceDurationSeconds.WithLabelValues(model).Observe(duration)
}

// RecordAPICall records an outbound API call.
//
// Parameters:
//   - service: External service name
//   - status: HTTP status code returned by the service
func (mc *MetricsCollector) RecordAPICall(service string, status int) {
	apiCallsTotal.WithLabelValues(service, fmt.Sprintf("%d", status)).Inc()
}

// SetGauge sets a gauge value for a specific operation type.
//
// operationType examples: "active_scans", "active_connections", "queue_depth".
func (mc *MetricsCollector) SetGauge(operationType string, value float64) {
	activeOperations.WithLabelValues(operationType).Set(value)
}

// IncGauge increments a gauge by 1.
func (mc *MetricsCollector) IncGauge(operationType string) {
	activeOperations.WithLabelValues(operationType).Inc()
}

// DecGauge decrements a gauge by 1 (will not go below 0).
func (mc *MetricsCollector) DecGauge(operationType string) {
	activeOperations.WithLabelValues(operationType).Dec()
}

// GetPrometheusMetrics returns the current Prometheus metrics snapshot
// in text exposition format.
func (mc *MetricsCollector) GetPrometheusMetrics() (string, error) {
	gatherers := prometheus.DefaultGatherer
	mf, err := gatherers.Gather()
	if err != nil {
		return "", fmt.Errorf("gather metrics: %w", err)
	}

	var buf string
	for _, f := range mf {
		buf += fmt.Sprintf("# HELP %s %s\n", *f.Name, *f.Help)
		buf += fmt.Sprintf("# TYPE %s %s\n", *f.Name, f.GetType())
		for _, m := range f.GetMetric() {
			buf += fmt.Sprintf("%s", *f.Name)
			labels := ""
			for _, l := range m.GetLabel() {
				if labels != "" {
					labels += ","
				}
				labels += fmt.Sprintf(`%s="%s"`, *l.Name, *l.Value)
			}
			if labels != "" {
				buf += fmt.Sprintf("{%s}", labels)
			}
			if m.GetCounter() != nil {
				buf += fmt.Sprintf(" %v\n", m.GetCounter().GetValue())
			} else if m.GetGauge() != nil {
				buf += fmt.Sprintf(" %v\n", m.GetGauge().GetValue())
			} else if m.GetHistogram() != nil {
				h := m.GetHistogram()
				buf += fmt.Sprintf("_count %v\n", h.GetSampleCount())
				buf += fmt.Sprintf("%s_sum", *f.Name)
				if labels != "" {
					buf += fmt.Sprintf("{%s}", labels)
				}
				buf += fmt.Sprintf(" %v\n", h.GetSampleSum())
				for _, b := range h.GetBucket() {
					buf += fmt.Sprintf("%s_bucket{le=\"%v\"} %v\n", *f.Name, b.GetUpperBound(), b.GetCumulativeCount())
				}
			}
		}
	}
	return buf, nil
}

// PrometheusHandler returns an http.Handler that serves the /metrics endpoint.
// Mount it with:
//
//	http.Handle("/metrics", telemetry.PrometheusHandler())
func PrometheusHandler() http.Handler {
	return promhttp.Handler()
}
