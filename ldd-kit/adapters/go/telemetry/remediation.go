// Package telemetry provides failure analysis and remediation suggestion
// capabilities for Go services.
//
// This file contains the FailureAnalyzer that pattern-matches structured
// logs against known error signatures and produces actionable remediation
// suggestions with confidence scores.
package telemetry

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

// RemediationSuggestion is a single actionable suggestion produced by
// FailureAnalyzer.
type RemediationSuggestion struct {
	Title            string   `json:"title"`
	Description      string   `json:"description"`
	Confidence       float64  `json:"confidence"`
	SuggestedActions []string `json:"suggested_actions"`
}

// CorrelatedIncident is a cluster of related error events sharing a trace
// identifier or temporal window.
type CorrelatedIncident struct {
	IncidentID       string    `json:"incident_id"`
	TraceID          *string   `json:"trace_id,omitempty"`
	StartTime        time.Time `json:"start_time"`
	EndTime          time.Time `json:"end_time"`
	EventCount       int       `json:"event_count"`
	ErrorTypes       []string  `json:"error_types"`
	RootCausePattern *string   `json:"root_cause_pattern,omitempty"`
	AffectedServices []string  `json:"affected_services"`
}

// FailureAnalyzer analyses structured log entries and produces remediation
// suggestions. It uses regex-based pattern matching to recognise common
// failure modes (GPU OOM, timeouts, connection errors, etc.), groups
// correlated events by trace_id and time window, and returns ranked
// suggestions.
type FailureAnalyzer struct {
	patterns map[string]*regexp.Regexp
}

var (
	analyzerInstance *FailureAnalyzer
	analyzerOnce     sync.Once
)

// GetFailureAnalyzer returns the global FailureAnalyzer singleton.
func GetFailureAnalyzer() *FailureAnalyzer {
	analyzerOnce.Do(func() {
		analyzerInstance = NewFailureAnalyzer()
	})
	return analyzerInstance
}

// NewFailureAnalyzer creates a new FailureAnalyzer with the default
// compiled error patterns.
func NewFailureAnalyzer() *FailureAnalyzer {
	return &FailureAnalyzer{
		patterns: map[string]*regexp.Regexp{
			"gpu_oom":                regexp.MustCompile(`(?i)(cuda out of memory|gpu oom|torch\.cuda\.OutOfMemory|out of memory.*gpu|nvml.*memory)`),
			"timeout":                regexp.MustCompile(`(?i)(timeout|timed out|deadline exceeded|context deadline|read timeout|connect timeout|request timeout)`),
			"connection_error":       regexp.MustCompile(`(?i)(connection refused|connection reset|connection closed|broken pipe|no route to host|dns.*fail|network.*unreachable|cannot connect|connection.*error)`),
			"rate_limit":             regexp.MustCompile(`(?i)(rate limit|too many requests|429|throttle|quota exceeded|limit exceeded|capacity exceeded)`),
			"auth_failure":           regexp.MustCompile(`(?i)(unauthorized|authentication.*fail|403|401|invalid.*token|token.*expired|credentials.*invalid|access denied|forbidden)`),
			"database_error":         regexp.MustCompile(`(?i)(database.*error|sql.*error|psycopg|sqlite|pymongo|connection.*pool|lock.*timeout|deadlock|constraint.*fail)`),
			"vision_model_error":     regexp.MustCompile(`(?i)(inference.*fail|model.*load|onnx.*error|tensorrt|inference.*timeout|model.*timeout|vision.*error|yolo.*error|detectron|tesseract.*error)`),
			"memory_pressure":        regexp.MustCompile(`(?i)(memory.*exhausted|oom killed|killed process|memory.*pressure|swap.*full)`),
			"dependency_unavailable": regexp.MustCompile(`(?i)(service.*unavailable|503|dependency.*fail|health.*check.*fail|circuit breaker|upstream.*unavailable)`),
		},
	}
}

// detectErrorType matches a log message against known error patterns.
// Returns the pattern key (e.g. "gpu_oom") or empty string.
func (fa *FailureAnalyzer) detectErrorType(message string) string {
	for key, pattern := range fa.patterns {
		if pattern.MatchString(message) {
			return key
		}
	}
	return ""
}

// Analyze analyses a slice of structured log entries and produces
// remediation suggestions ranked by confidence.
//
// Each log entry should be a map with keys such as "event", "message",
// "error_message", "log_level", "level", and "timestamp".
func (fa *FailureAnalyzer) Analyze(logs []map[string]interface{}) []RemediationSuggestion {
	var suggestions []RemediationSuggestion
	if len(logs) == 0 {
		return suggestions
	}

	// Pattern frequency analysis.
	errorCounts := make(map[string]int)
	errorLogs := make(map[string][]map[string]interface{})

	for _, entry := range logs {
		msg := fa.extractMessage(entry)
		level := fa.extractLevel(entry)
		if level == "ERROR" || level == "CRITICAL" || level == "EXCEPTION" {
			errType := fa.detectErrorType(msg)
			if errType != "" {
				errorCounts[errType]++
				errorLogs[errType] = append(errorLogs[errType], entry)
			}
		}
	}

	// Generate suggestions based on detected patterns.
	for errType, count := range errorCounts {
		suggestion := fa.suggestForPattern(errType, count, errorLogs[errType])
		if suggestion != nil {
			suggestions = append(suggestions, *suggestion)
		}
	}

	// Spike detection.
	if len(logs) >= 2 {
		timestamps := fa.extractTimestamps(logs)
		if len(timestamps) >= 2 {
			sort.Slice(timestamps, func(i, j int) bool {
				return timestamps[i].Before(timestamps[j])
			})
			timeWindow := timestamps[len(timestamps)-1].Sub(timestamps[0]).Seconds()
			if timeWindow > 0 {
				errorRate := float64(sumValues(errorCounts)) / max(timeWindow/60.0, 1.0)
				if errorRate > 10 {
					suggestions = append(suggestions, RemediationSuggestion{
						Title:       "Error Rate Spike Detected",
						Description: fmt.Sprintf("Detected %.1f errors per minute over %.0f seconds. Consider scaling or circuit-breaker activation.", errorRate, timeWindow),
						Confidence:  min(0.95, errorRate/50.0),
						SuggestedActions: []string{
							"Check auto-scaling configuration",
							"Verify circuit breaker thresholds",
							"Review downstream service health",
							"Consider temporary traffic reduction",
						},
					})
				}
			}
		}
	}

	// Repeated error detection.
	for errType, count := range errorCounts {
		if count >= 5 {
			suggestions = append(suggestions, RemediationSuggestion{
				Title:       fmt.Sprintf("Recurring %s Errors", strings.ReplaceAll(errType, "_", " ")),
				Description: fmt.Sprintf("%d occurrences of '%s' detected. This suggests a persistent issue requiring attention.", count, errType),
				Confidence:  min(0.9, 0.5+float64(count)/20.0),
				SuggestedActions: []string{
					"Review service configuration",
					"Check resource allocation",
					"Verify downstream dependency health",
					"Consider rolling back recent deployments",
				},
			})
		}
	}

	return suggestions
}

// Correlate groups related error events by trace_id and time window (5 min).
//
// Each event should be a map optionally containing "trace_id" and "timestamp".
func (fa *FailureAnalyzer) Correlate(events []map[string]interface{}) []CorrelatedIncident {
	if len(events) == 0 {
		return nil
	}

	byTrace := make(map[string][]map[string]interface{})
	var noTrace []map[string]interface{}

	for _, evt := range events {
		tid := fa.extractString(evt, "trace_id")
		if tid != "" {
			byTrace[tid] = append(byTrace[tid], evt)
		} else {
			noTrace = append(noTrace, evt)
		}
	}

	var incidents []CorrelatedIncident
	now := time.Now().UTC()

	// Group by trace_id.
	for traceID, evts := range byTrace {
		inc := fa.buildIncident(&traceID, evts)
		if inc != nil {
			incidents = append(incidents, *inc)
		}
	}

	// Group no-trace events by time window (5 min).
	if len(noTrace) > 0 {
		sort.Slice(noTrace, func(i, j int) bool {
			ti := fa.extractTimestamp(noTrace[i], now)
			tj := fa.extractTimestamp(noTrace[j], now)
			return ti.Before(tj)
		})

		window := 5 * time.Minute
		var currentGroup []map[string]interface{}

		for _, evt := range noTrace {
			ts := fa.extractTimestamp(evt, now)

			if len(currentGroup) == 0 {
				currentGroup = append(currentGroup, evt)
				continue
			}

			lastTs := fa.extractTimestamp(currentGroup[len(currentGroup)-1], now)
			if ts.Sub(lastTs) <= window {
				currentGroup = append(currentGroup, evt)
			} else {
				inc := fa.buildIncident(nil, currentGroup)
				if inc != nil {
					incidents = append(incidents, *inc)
				}
				currentGroup = []map[string]interface{}{evt}
			}
		}

		if len(currentGroup) > 0 {
			inc := fa.buildIncident(nil, currentGroup)
			if inc != nil {
				incidents = append(incidents, *inc)
			}
		}
	}

	// Sort by event count descending.
	sort.Slice(incidents, func(i, j int) bool {
		return incidents[i].EventCount > incidents[j].EventCount
	})

	return incidents
}

// buildIncident constructs a CorrelatedIncident from a group of events.
func (fa *FailureAnalyzer) buildIncident(traceID *string, events []map[string]interface{}) *CorrelatedIncident {
	if len(events) == 0 {
		return nil
	}

	var timestamps []time.Time
	var errorTypes []string
	var services []string
	now := time.Now().UTC()

	seenErrors := make(map[string]bool)
	seenServices := make(map[string]bool)

	for _, evt := range events {
		ts := fa.extractTimestamp(evt, now)
		timestamps = append(timestamps, ts)

		msg := fa.extractMessage(evt)
		err := fa.detectErrorType(msg)
		if err != "" && !seenErrors[err] {
			seenErrors[err] = true
			errorTypes = append(errorTypes, err)
		}

		svc := fa.extractString(evt, "service")
		if svc == "" {
			svc = fa.extractString(evt, "service_name")
		}
		if svc != "" && !seenServices[svc] {
			seenServices[svc] = true
			services = append(services, svc)
		}
	}

	var startTime, endTime time.Time
	if len(timestamps) > 0 {
		startTime = timestamps[0]
		endTime = timestamps[0]
		for _, ts := range timestamps[1:] {
			if ts.Before(startTime) {
				startTime = ts
			}
			if ts.After(endTime) {
				endTime = ts
			}
		}
	} else {
		startTime = now
		endTime = now
	}

	var rootCause *string
	if len(errorTypes) > 0 {
		rc := errorTypes[0]
		rootCause = &rc
	}

	var incID string
	if traceID != nil && *traceID != "" {
		tidLen := len(*traceID)
		if tidLen > 12 {
			tidLen = 12
		}
		incID = fmt.Sprintf("inc-%s-%d", (*traceID)[:tidLen], len(events))
	} else {
		incID = fmt.Sprintf("inc-%s-%d", startTime.Format("20060102150405"), len(events))
	}

	return &CorrelatedIncident{
		IncidentID:       incID,
		TraceID:          traceID,
		StartTime:        startTime,
		EndTime:          endTime,
		EventCount:       len(events),
		ErrorTypes:       errorTypes,
		RootCausePattern: rootCause,
		AffectedServices: services,
	}
}

// suggestForPattern returns a RemediationSuggestion for a recognised pattern.
func (fa *FailureAnalyzer) suggestForPattern(pattern string, count int, logs []map[string]interface{}) *RemediationSuggestion {
	suggestionsMap := map[string]RemediationSuggestion{
		"gpu_oom": {
			Title:       "GPU Out-of-Memory Errors",
			Description: fmt.Sprintf("%d GPU OOM error(s) detected. The vision model is exhausting GPU memory during inference.", count),
			Confidence:  min(0.95, 0.6+float64(count)/10.0),
			SuggestedActions: []string{
				"Reduce batch size for vision inference",
				"Enable model quantization (FP16 / INT8)",
				"Scale GPU nodes horizontally",
				"Implement request queueing with back-pressure",
				"Consider model sharding for large inputs",
			},
		},
		"timeout": {
			Title:       "Request Timeout Errors",
			Description: fmt.Sprintf("%d timeout error(s) detected. Downstream services or inference is taking longer than expected.", count),
			Confidence:  min(0.9, 0.5+float64(count)/15.0),
			SuggestedActions: []string{
				"Increase timeout thresholds temporarily",
				"Review slow query / endpoint performance",
				"Enable async processing for long-running operations",
				"Add caching for frequently accessed data",
				"Scale affected service replicas",
			},
		},
		"connection_error": {
			Title:       "Connection Errors",
			Description: fmt.Sprintf("%d connection error(s) detected. Network issues or downstream service unavailability.", count),
			Confidence:  min(0.85, 0.5+float64(count)/15.0),
			SuggestedActions: []string{
				"Verify downstream service health endpoints",
				"Check DNS resolution and network policies",
				"Review connection pool configuration",
				"Enable retry with exponential backoff",
				"Check firewall / security group rules",
			},
		},
		"rate_limit": {
			Title:       "Rate Limiting / Throttling",
			Description: fmt.Sprintf("%d rate-limit error(s) detected. External API quotas are being exceeded.", count),
			Confidence:  min(0.88, 0.55+float64(count)/12.0),
			SuggestedActions: []string{
				"Implement client-side rate limiting",
				"Add request caching to reduce API calls",
				"Request quota increase from provider",
				"Enable request batching",
				"Add circuit breaker for external APIs",
			},
		},
		"auth_failure": {
			Title:       "Authentication Failures",
			Description: fmt.Sprintf("%d authentication error(s) detected. Token expiry or credential misconfiguration.", count),
			Confidence:  min(0.82, 0.5+float64(count)/15.0),
			SuggestedActions: []string{
				"Check token expiry and refresh logic",
				"Verify API key / credential configuration",
				"Review IAM policies and permissions",
				"Rotate credentials if compromised",
				"Audit recent permission changes",
			},
		},
		"database_error": {
			Title:       "Database Errors",
			Description: fmt.Sprintf("%d database error(s) detected. Connection pool exhaustion or query issues.", count),
			Confidence:  min(0.85, 0.5+float64(count)/15.0),
			SuggestedActions: []string{
				"Check connection pool size and usage",
				"Review slow query log",
				"Verify database replication lag",
				"Add read replicas for query offload",
				"Check for lock contention and deadlocks",
			},
		},
		"vision_model_error": {
			Title:       "Vision Model Inference Errors",
			Description: fmt.Sprintf("%d vision model error(s) detected. Model loading or inference pipeline failure.", count),
			Confidence:  min(0.88, 0.55+float64(count)/12.0),
			SuggestedActions: []string{
				"Verify model artifact availability in storage",
				"Check model version compatibility",
				"Validate input image format and size",
				"Restart inference worker pods",
				"Roll back to previous model version",
			},
		},
		"memory_pressure": {
			Title:       "System Memory Pressure",
			Description: fmt.Sprintf("%d memory pressure event(s) detected. Host-level memory exhaustion detected.", count),
			Confidence:  min(0.85, 0.5+float64(count)/12.0),
			SuggestedActions: []string{
				"Increase container memory limits",
				"Add memory-based Horizontal Pod Autoscaler",
				"Review memory leaks in application code",
				"Enable swap or increase node memory",
				"Restart affected services to reclaim memory",
			},
		},
		"dependency_unavailable": {
			Title:       "Dependency Service Unavailable",
			Description: fmt.Sprintf("%d dependency unavailable error(s) detected. Required downstream service is not reachable.", count),
			Confidence:  min(0.87, 0.52+float64(count)/14.0),
			SuggestedActions: []string{
				"Check health status of all downstream services",
				"Verify service discovery / registry configuration",
				"Review recent deployment changes",
				"Enable graceful degradation mode",
				"Scale downstream service replicas",
			},
		},
	}

	if s, ok := suggestionsMap[pattern]; ok {
		return &s
	}
	return nil
}

// extractMessage pulls the message string from a log entry.
func (fa *FailureAnalyzer) extractMessage(entry map[string]interface{}) string {
	for _, key := range []string{"event", "message", "error_message", "msg"} {
		if v, ok := entry[key]; ok {
			if s, ok := v.(string); ok {
				return s
			}
			return fmt.Sprintf("%v", v)
		}
	}
	return ""
}

// extractLevel pulls the log level from a log entry and normalizes it.
func (fa *FailureAnalyzer) extractLevel(entry map[string]interface{}) string {
	for _, key := range []string{"log_level", "level"} {
		if v, ok := entry[key]; ok {
			if s, ok := v.(string); ok {
				return strings.ToUpper(s)
			}
		}
	}
	return ""
}

// extractString pulls a string value from a log entry by key.
func (fa *FailureAnalyzer) extractString(entry map[string]interface{}, key string) string {
	if v, ok := entry[key]; ok {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return ""
}

// extractTimestamp parses a timestamp from a log entry.
func (fa *FailureAnalyzer) extractTimestamp(entry map[string]interface{}, fallback time.Time) time.Time {
	raw, ok := entry["timestamp"]
	if !ok {
		return fallback
	}

	switch v := raw.(type) {
	case string:
		s := strings.ReplaceAll(v, "Z", "+00:00")
		if t, err := time.Parse(time.RFC3339, s); err == nil {
			return t
		}
		if t, err := time.Parse(time.RFC3339Nano, s); err == nil {
			return t
		}
		if t, err := time.Parse(time.DateTime, s); err == nil {
			return t
		}
	case time.Time:
		return v
	}
	return fallback
}

// extractTimestamps collects all valid timestamps from log entries.
func (fa *FailureAnalyzer) extractTimestamps(logs []map[string]interface{}) []time.Time {
	var result []time.Time
	now := time.Now().UTC()
	for _, entry := range logs {
		if ts := fa.extractTimestamp(entry, now); ts != now {
			result = append(result, ts)
		}
	}
	return result
}

func sumValues(m map[string]int) int {
	sum := 0
	for _, v := range m {
		sum += v
	}
	return sum
}

func max(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func min(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
