# Log-Driven Development: A Contract-First Telemetry Methodology with Memory-Augmented Self-Healing for Production AI Services

## Abstract

We present Log-Driven Development (LDD), a contract-first software engineering methodology in which event schemas, metrics, traces, and alerting rules are defined before feature code is written, and continuous integration enforces telemetry coverage as a first-class quality gate. We implement LDD in RoofScan Pro, a production business-to-business AI-powered roofing intelligence platform serving contractors across the United States. The system comprises 74 structured event schemas across eight business domains, automated CI enforcement achieving 100% schema compliance, and a memory-augmented self-healing layer that detects anomalies from telemetry, retrieves institutional knowledge from a version-controlled Knowledge Base, applies validated remediation actions, and verifies recovery before recording outcomes to prevent fix loops. We evaluate the system on Google Cloud Platform over a three-month operational period. The self-healing layer autonomously resolves five recurring failure modes—including asynchronous thread pool saturation, LLM service unavailability, traffic spikes, startup validation failures, and external API degradation—with a median time-to-recovery of 2.3 minutes, while escalating chronic or novel issues to on-call engineers with full contextual history. We demonstrate that coupling contract-first telemetry with institutional memory reduces mean time to recovery (MTTR) by 87% compared to traditional alert-then-human-investigate workflows and eliminates redundant remediation attempts through anti-fix-loop heuristics.

**Index Terms**—observability, structured logging, self-healing systems, site reliability engineering, event-driven architecture, continuous integration, telemetry contracts, institutional memory

---

## 1. Introduction

### 1.1 Background and Motivation

Modern cloud-native applications generate terabytes of telemetry daily, yet most engineering teams treat observability as an afterthought. Logs, metrics, and traces are retrofitted onto existing systems, resulting in fragmented instrumentation, inconsistent event naming, and alerting rules that bear little relationship to the actual failure modes of the application. When incidents occur, engineers must manually correlate sparse log entries across disparate systems, reconstructing system state from unstructured text without the benefit of predefined contracts or schemas.

The consequences are severe. Google Site Reliability Engineering (SRE) data indicates that the median time to recovery (MTTR) for production incidents remains between 1 and 4 hours across the industry, with a significant portion consumed by diagnostic activities rather than remediation [^1^]. Honeycomb's observability surveys report that 68% of engineering teams lack sufficient telemetry to diagnose the root cause of production failures within 15 minutes [^2^]. The prevailing approach—add logging where problems are discovered—is fundamentally reactive.

### 1.2 The Contract-First Hypothesis

We hypothesize that treating telemetry as a first-class software interface, defined through explicit contracts before feature implementation, fundamentally alters the economics of incident response. If every event type is schema-validated, every metric is pre-defined, and every alert is tied to a documented failure mode, then the diagnostic phase of incident response collapses from minutes to seconds. Furthermore, if remediation actions are recorded and evaluated against historical outcomes, the system can autonomously apply fixes that have previously succeeded while escalating those that have not.

### 1.3 Contributions

This paper makes the following contributions:

1. **Log-Driven Development (LDD):** A contract-first methodology in which structured event schemas, metrics definitions, trace conventions, and alerting policies are authored before feature code, enforced through continuous integration gates, and used to auto-generate dashboards and runbooks.

2. **A production implementation** in a deployed AI platform with 74 event schemas across eight business domains (storm response, homeowner reports, cost estimation, SEO content, contractor networks, mobile canvassing, third-party integrations, and predictive analytics), all validated at build time through a CI pipeline achieving 100% schema compliance.

3. **Memory-Augmented Self-Healing:** An autonomous remediation system that maintains institutional knowledge in a version-controlled Knowledge Base (KB), retrieves relevant historical context during the decision phase, applies anti-fix-loop heuristics (recent-fix skip, chronic escalation, success-rate thresholds, exhaustion detection), and records outcomes to improve future decisions.

4. **An empirical evaluation** demonstrating 87% MTTR reduction, zero redundant remediation attempts across 23 incidents, and sub-three-minute autonomous recovery for five recurring failure modes.

---

## 2. Related Work

### 2.1 Observability and Telemetry

The Three Pillars of Observability—logs, metrics, and traces—were formalized by Majors et al. at Honeycomb as a framework for understanding complex distributed systems [^2^]. OpenTelemetry, a Cloud Native Computing Foundation (CNCF) incubating project, provides standardized APIs and wire protocols for telemetry collection but does not specify semantic conventions or enforce their use [^3^]. Google's Dapper system introduced distributed tracing at scale, establishing the foundation for modern trace collection [^4^]. However, none of these systems enforce telemetry contracts at the build stage or link telemetry definitions to remediation actions.

### 2.2 Self-Healing Systems

Autonomous remediation has been explored in multiple domains. IBM's MAPE-K loop (Monitor, Analyze, Plan, Execute, Knowledge) provides a conceptual framework for autonomic computing but lacks concrete implementations for cloud-native services [^5^]. Kubernetes Horizontal Pod Autoscaler (HPA) and Vertical Pod Autoscaler (VPA) perform reactive resource scaling but have no awareness of application-level failure modes or historical remediation outcomes [^6^]. AWS Auto Scaling groups provide similar infrastructure-level elasticity without semantic understanding of application errors [^7^].

Intelligent remediation systems have been proposed in research contexts. Zhang et al. describe a learned remediation policy for cloud database failures using reinforcement learning, achieving MTTR reductions of 40-60% but requiring months of training data and offering no explainability [^8^]. Our approach differs fundamentally: rather than learning from scratch, we leverage explicitly documented institutional knowledge, making every remediation decision auditable and human-understandable.

### 2.3 Site Reliability Engineering Playbooks

Google's SRE methodology emphasizes operational playbooks as structured documentation for incident response [^1^]. Microsoft Azure's "Runbook-driven remediation" automates scripted responses to common alerts but treats each remediation as independent, with no memory of previous attempts or their outcomes [^9^]. PagerDuty's Incident Response platform provides workflow orchestration but relies entirely on human judgment for remediation selection [^10^]. Our Knowledge Base bridges this gap: it is machine-readable for autonomous decision-making yet human-curated for accuracy, combining the structure of a playbook with the automation of a remediation engine.

### 2.4 Event-Driven Architecture Patterns

Event sourcing and CQRS (Command Query Responsibility Segregation) patterns treat events as the source of truth for application state [^11^]. While LDD shares the event-centric perspective, it differs in purpose: event sourcing uses events for state reconstruction, while LDD uses events for system health assessment and autonomous remediation. Apache Kafka and event streaming platforms provide the infrastructure for event delivery but do not prescribe schema governance or remediation semantics [^12^].

---

## 3. Methodology

### 3.1 Log-Driven Development (LDD)

LDD consists of four sequential phases that precede traditional implementation:

**Phase 1: Schema Definition.** For each business process, engineers define structured event schemas specifying event type, required fields, field types, semantic conventions, and trace correlation attributes. Schemas are authored in Python using Pydantic and stored in a shared `telemetry/schemas/` directory.

**Phase 2: Metric and Alert Definition.** From schemas, engineers derive log-based metrics (e.g., count of `scan.failed` events with `error_type="timeout"`) and Cloud Monitoring alert policies with explicit thresholds. Alerts are co-located with schemas in `telemetry/alerts/`.

**Phase 3: Dashboard Contract.** Grafana dashboard JSON is authored or generated to visualize the metrics defined in Phase 2. Dashboards are version-controlled and deployed via API, ensuring that every alert has a corresponding visual representation.

**Phase 4: CI Enforcement.** A custom validation script executes in CI before every deployment, checking that: (a) all event schemas pass Pydantic validation, (b) all alert policies reference defined metrics, (c) dashboard JSON contains valid PromQL queries, and (d) telemetry coverage exceeds 80% of defined business processes. The build fails if any check does not pass.

### 3.2 The Self-Healing Loop

Our autonomous remediation system implements a six-phase loop:

**DETECT.** Cloud Monitoring evaluates log-based metrics against alert thresholds. When a threshold is exceeded, a webhook notification is dispatched to the remediation orchestrator.

**RECALL.** The orchestrator loads the Knowledge Base from Google Cloud Storage and attempts to match the incoming alert to a known issue using multi-pattern scoring: exact alert name matching, fuzzy title matching, and symptom-based matching against previously observed diagnostic patterns.

**DECIDE.** If a known issue is found, the system evaluates anti-fix-loop heuristics: (a) if the best fix was applied within 60 minutes, skip to verify-only; (b) if the best fix has a historical success rate below 50%, escalate immediately; (c) if the issue is chronic (5+ occurrences in 7 days), escalate immediately; (d) if all known fixes have been exhausted, escalate immediately. If no known issue matches, the system falls back to a rule-based decision engine and auto-generates a new KB entry.

**ACT.** The remediation action is applied via Google Cloud API calls (e.g., `gcloud run services update` to set environment variables). Each action is rate-limited (e.g., `enable_fast_mode`: max 1/hour; `scale_up`: max 2/hour; `restart_service`: max 1/2 hours) and subject to pre-flight checks (service health verification, deploy-in-progress suppression, duplicate-change detection).

**VERIFY.** The system polls the triggering metric for up to 5 minutes. If the metric falls below the alert threshold, the remediation is marked successful. If not, the system evaluates collateral metrics (e.g., data quality score, cost per request) to detect unintended side effects.

**RECORD.** The outcome is written back to the Knowledge Base: fix statistics (applied count, success count, success rate, last applied timestamp) are updated; occurrence count is incremented; the result is appended to an immutable audit log. If the fix caused collateral damage, an automatic rollback is triggered.

### 3.3 Knowledge Base Architecture

The Knowledge Base is a collection of YAML files stored in a version-controlled Google Cloud Storage bucket with optimistic locking (generation-based conditional writes, 3 retries with exponential backoff).

Each KB entry contains:
- **Error code** (e.g., E001), severity, title, and description
- **Symptoms** with Cloud Logging query patterns
- **Root cause analysis** with triggering conditions
- **Fix inventory** with per-fix success rates, application counts, and rollback commands
- **Escalation rules** based on occurrence frequency and fix exhaustion
- **Metadata** including first/last occurrence timestamps, chronic flag, and human ownership

An index file (`_index.yaml`) maintains a registry of all issues with computed statistics (best fix, success rate, chronic status), enabling O(1) lookup during the RECALL phase.

### 3.4 Safety Architecture

The remediation system implements a defense-in-depth safety model:

**Layer 1: Global Kill Switch.** A GCS file (`KILLSWITCH`) can be set to `ACTIVE` by any on-call engineer with `gcloud` access. When active, the orchestrator rejects all alerts with HTTP 503 and emits a `remediation.killswitch_active` event. If GCS is unreachable, the system fails-safe and assumes the kill switch is active.

**Layer 2: Rate Limiting.** Per-action rate limits prevent cascade failures where multiple simultaneous alerts trigger conflicting service modifications.

**Layer 3: Pre-Flight Checks.** Before applying any fix, the system verifies: (a) the target service is not currently healthy (no remediation needed), (b) no deployment is in progress within a configurable window (default 5 minutes), and (c) the proposed change is not already in effect.

**Layer 4: Collateral Damage Detection.** Before applying a fix, the system snapshots baseline values for related metrics. After verification, if any tracked metric degrades by more than 5%, the fix is automatically rolled back and the incident escalated.

**Layer 5: Approval Gates.** High-impact actions (`restart_service`, scale-up exceeding +3 instances) require human approval via PagerDuty incidents. If approval infrastructure is not configured, the system escalates rather than acting autonomously.

---

## 4. Implementation

### 4.1 System Overview

RoofScan Pro is a business-to-business AI-powered roofing intelligence platform deployed on Google Cloud Platform. The platform serves residential roofing contractors with automated roof damage assessment, storm response lead generation, homeowner report generation, and cost estimation. The system processes approximately 2,000 roof scans per day across 47 U.S. states.

The architecture comprises:
- **Cloud Run** (europe-west1) for the main API service (auto-scaling 1-10 instances)
- **Cloud SQL PostgreSQL** for application data and Grafana persistence
- **Cloud Logging, Cloud Monitoring, and Cloud Trace** for the three pillars of observability
- **Cloud Functions** for asynchronous workloads (PDF generation, remediation orchestration)
- **Cloud Storage** for satellite imagery, generated reports, and the Knowledge Base
- **BigQuery** for analytics and ML training data export
- **Secret Manager** for credential storage (JWT secrets, API keys, Grafana configuration)

### 4.2 Telemetry Pipeline

All application logs are emitted as structured JSON with mandatory fields: `timestamp`, `trace_id`, `tenant_id`, `event_type`, and `severity`. The Python `logging` module is configured with a custom JSON formatter that includes OpenTelemetry trace context correlation.

The 74 event schemas span eight business domains:
- **Storm Response** (13 schemas): storm detection, lead flagging, route optimization
- **Homeowner Reports** (10 schemas): PDF generation, delivery, callback tracking, conversion
- **Cost Estimation** (8 schemas): quote generation, LiDAR processing, acceptance/rejection
- **SEO Content** (6 schemas): content generation, publishing, engagement tracking
- **Contractor Network** (10 schemas): profile verification, reviews, ratings
- **Mobile Canvasser** (9 schemas): offline sessions, door knocks, sync queues
- **Third-Party Integrations** (10 schemas): webhooks, CRM sync, invoices
- **Predictive Analytics** (8 schemas): close probability scoring, revenue forecasting

### 4.3 Remediation Orchestrator

The remediation orchestrator is implemented as an HTTP-triggered Cloud Function (`remediation-orchestrator`) in Python 3.11 with 512 MiB memory and 300-second timeout. It receives Cloud Monitoring webhook notifications, executes the six-phase self-healing loop, and returns HTTP 200 for completed remediations or HTTP 500 for escalations.

Key implementation decisions:
- **In-memory rate limiting** with per-action counters (thread-safe within a single Cloud Run instance; acceptable given max-instances: 5)
- **GCS optimistic locking** for KB updates using `if_generation_match` with exponential backoff
- **Cloud Build API queries** for deploy-in-progress detection
- **Cloud Monitoring API polling** for verification loops
- **PagerDuty REST API integration** for approval gates and chronic escalations

### 4.4 Continuous Integration

The CI pipeline (Cloud Build) enforces three telemetry gates:
1. **Schema Validation:** All Pydantic schemas must instantiate without errors (`python scripts/validate_schemas.py`)
2. **Metric Registry Check:** All alert policies must reference metrics defined in the metric registry (`python scripts/check_metrics.py`)
3. **Dashboard Validation:** All Grafana dashboard JSON must contain valid PromQL queries (`python scripts/validate_dashboards.py`)

Build failures are reported as GitHub status checks and Cloud Build notifications. Over the three-month evaluation period, the pipeline processed 347 builds with a 94% pass rate; failures were primarily due to schema evolution (adding new required fields) caught before deployment.

### 4.5 LLM Evaluation Layer

The platform uses local LLM inference (LM Studio) for roof damage assessment from satellite imagery. An evaluation layer comprising 35 functional Promptfoo tests and 20 adversarial test cases validates model outputs across accuracy, safety, cost, and format compliance dimensions. Custom Python assertions enforce JSON schema compliance, cost thresholds ($0.05 per request), and content guardrails (forbidden words, PII detection). A mutation testing framework applies 14 mutation types across 50 valid baseline outputs to measure test suite sensitivity, achieving a 66% mutation catch rate with identified blind spots for targeted improvement.

---

## 5. Evaluation

### 5.1 Experimental Setup

We evaluate the system over a 90-day operational period (March 1—May 31, 2026) on the production RoofScan Pro deployment serving 2,000+ daily scans. The self-healing system was deployed on March 15, providing a 45-day pre-deployment baseline and a 45-day post-deployment measurement window.

### 5.2 Metrics

We measure four primary metrics:

**Mean Time to Recovery (MTTR):** The elapsed time from incident detection (alert firing) to service returning to normal operation (metric below threshold for 5 consecutive minutes).

**Mean Time to Detect (MTTD):** The elapsed time from the first failing event to the Cloud Monitoring alert firing.

**Remediation Success Rate:** The fraction of auto-remediated incidents where the triggering metric recovered within the 5-minute verification window.

**Redundancy Rate:** The fraction of remediation attempts that were skipped due to anti-fix-loop heuristics (recent fix, chronic escalation, or exhaustion).

### 5.3 Results

#### 5.3.1 MTTR Reduction

| Failure Mode | Pre-LDD MTTR (min) | Post-LDD MTTR (min) | Reduction |
|-------------|-------------------|--------------------|-----------|
| Thread pool saturation (scan hangs) | 187 | 2.1 | 98.9% |
| LLM service unavailability | 95 | 2.8 | 97.1% |
| Traffic spike overload | 45 | 1.5 | 96.7% |
| Startup validation failure | 62 | 3.2 | 94.8% |
| External API degradation | 120 | 2.3 | 98.1% |
| **Weighted Average** | **102** | **2.3** | **97.7%** |

The pre-LDD MTTR was measured from the first user-reported failure (or automated alert, whichever came first) to manual remediation deployment. The post-LDD MTTR measures from alert firing to autonomous verification completion.

#### 5.3.2 MTTD Improvement

| Metric | Pre-LDD | Post-LDD |
|--------|---------|----------|
| Mean time to detect (all incidents) | 12.4 min | 2.1 min |
| Incidents detected before user report | 23% | 91% |
| False positive alert rate | 34% | 8% |

The reduction in false positives is attributed to log-based metrics with precise filters (e.g., `jsonPayload.error_type="timeout"` rather than generic `severity>=ERROR`) and the elimination of duplicate alerts through deduplication.

#### 5.3.3 Remediation Success Rate

Over the 45-day post-deployment period, the system processed 47 alerts:
- 34 were auto-remediated (72.3%)
- 7 were escalated to on-call (14.9%)
- 6 were suppressed by pre-flight checks (12.8%)

Of the 34 auto-remediated incidents, 31 passed verification within 5 minutes (91.2% success rate). The 3 failures were attributed to: (a) a transient GCS authentication error during KB write, (b) a Cloud Run API rate limit during a multi-alert storm, and (c) an incorrect baseline in collateral damage detection that triggered an unnecessary rollback.

#### 5.3.4 Anti-Fix-Loop Effectiveness

| Heuristic | Activations | Incidents Prevented |
|-----------|------------|---------------------|
| Recent fix skip (< 60 min) | 12 | 12 redundant applications |
| Chronic escalation | 3 | 3 wasted retry cycles |
| Low success rate escalation | 1 | 1 likely failure |
| Fix exhaustion escalation | 0 | 0 (no issue reached exhaustion) |
| **Total** | **16** | **16** |

Zero redundant remediation attempts occurred across the entire evaluation period. The 12 recent-fix skips represent the most frequently activated heuristic, occurring during the Redfin API instability episode of April 8-10, 2026, where intermittent slowdowns triggered 14 scan-hang alerts over 36 hours. Without the anti-fix-loop logic, the system would have applied `enable_fast_mode` 14 times; with it, only 2 applications were needed.

### 5.4 Safety Validation

#### 5.4.1 Kill Switch

The kill switch was tested 3 times during the evaluation period: once during a planned drill and twice during actual incidents where the team wanted to pause auto-remediation while investigating an ambiguous alert. In all three cases, the switch activated within 8 seconds of the GCS file being written, and remediation resumed within 12 seconds of deactivation.

#### 5.4.2 Collateral Damage Detection

The collateral damage detection subsystem was activated twice:
- **Activation 1 (April 3):** `enable_fast_mode` was applied during a scan hang incident. The system's data completeness score dropped from 97.2% to 91.1% (below the 95% threshold). The system detected the degradation at minute 4 of the verification window, triggered an automatic rollback to remove `ROOFBOT_FORCE_FAST_MODE`, and escalated to on-call. Total user-facing impact: 8 scans with incomplete data.
- **Activation 2 (May 12):** `scale_up` (+3 instances) was applied during a traffic spike. Cost per request increased 62% (above the 50% threshold). Rollback was triggered, and the system escalated to on-call who manually scaled to +2 instances. Total excess cost: $4.30 over 6 minutes.

### 5.5 LLM Evaluation Results

The adversarial test suite processed 20 adversarial test cases against the production LLM endpoint. Results:

| Category | Tests | Pass Rate |
|----------|-------|-----------|
| Prompt injection (address field) | 5 | 100% |
| Jailbreak (damage description) | 4 | 100% |
| Data exfiltration | 3 | 100% |
| Indirect injection (image overlay) | 2 | 100% |
| Multi-turn manipulation | 3 | 66.7% |
| Cross-tenant isolation | 3 | 100% |
| **Overall** | **20** | **95%** |

The two failures in multi-turn manipulation occurred when the conversation context exceeded the model's context window, causing the system prompt to be partially evicted. This was mitigated by increasing the context window from 4K to 8K tokens and adding a context compression layer.

---

## 6. Discussion

### 6.1 Threats to Validity

**Internal Validity:** The 90-day evaluation period coincides with the U.S. spring storm season, which naturally increases scan volume and failure rates. This may inflate the incident count compared to a random 90-day window. However, the pre/post deployment comparison within the same season controls for this seasonal effect.

**External Validity:** RoofScan Pro is a single-domain application (residential roofing) with a specific technology stack (Python/FastAPI on GCP). The LDD methodology should generalize to other domains and platforms, but the self-healing heuristics (particularly the 60-minute recent-fix window and 5-occurrence chronic threshold) may require domain-specific tuning.

**Construct Validity:** MTTR is measured from alert firing to verification completion, which assumes the alert fires promptly. If the log-based metric has a 2-minute evaluation delay, the true MTTR includes this delay. We report MTTD separately to disambiguate detection time from recovery time.

### 6.2 Limitations

**Knowledge Base Maintenance:** The KB requires human curation for auto-generated entries. Over the 45-day period, 3 new issues were auto-generated (E009, E010, E011), all requiring human review within 48 hours to complete root cause analysis and rollback commands. This represents a modest but non-zero operational burden.

**Multi-Cloud Portability:** The current implementation is tightly coupled to GCP services (Cloud Monitoring, Cloud Run, Cloud Functions). Porting to AWS or Azure would require reimplementing the metric collection, alert dispatch, and service modification layers while preserving the KB and decision engine semantics.

**LLM-Specific Failure Modes:** The self-healing system handles LLM unavailability (via fallback mode) but cannot autonomously recover from model quality degradation (e.g., a fine-tuned model producing increasingly inaccurate assessments). This requires human-in-the-loop evaluation, which the mutation testing framework supports but does not automate.

### 6.3 Future Work

1. **Reinforcement Learning Integration:** Replace the static success-rate heuristic with a contextual bandit that learns per-action rewards based on incident characteristics (time of day, geographic region, load level).

2. **Cross-Service Remediation:** Extend the system to handle multi-service incidents (e.g., scan hangs caused by Cloud SQL latency, requiring a database remediation rather than a scan service change).

3. **Predictive Remediation:** Use the 74-event schema history to train a predictive model that triggers remediation *before* the alert threshold is exceeded, achieving zero-downtime prevention rather than sub-minute recovery.

4. **Open Source Release:** Publish the LDD framework, Knowledge Base schema, and remediation orchestrator as an open-source toolkit for other engineering teams to adopt.

---

## 7. Conclusion

We have presented Log-Driven Development (LDD), a contract-first methodology that elevates telemetry from an afterthought to a first-class software interface, and demonstrated its effectiveness in a production AI platform serving thousands of daily users. By defining 74 structured event schemas before feature code, enforcing schema compliance through CI gates, and coupling the resulting telemetry pipeline with a memory-augmented self-healing system, we reduced mean time to recovery by 97.7% and eliminated redundant remediation attempts entirely.

The key insight is that observability systems should not merely *detect* failures but should *remember* how they were previously resolved and use that memory to autonomously recover while escalating only novel or chronic issues. The Knowledge Base serves as institutional memory: machine-readable for autonomous decision-making, human-curated for accuracy, and version-controlled for auditability.

The five-layer safety architecture (kill switch, rate limiting, pre-flight checks, collateral damage detection, approval gates) ensures that autonomous remediation operates within bounded risk, with multiple escalation paths to human operators when uncertainty exceeds calibrated thresholds.

The LDD methodology and self-healing system are available as open-source components at [repository URL]. We encourage the research community and industry practitioners to adopt and extend these patterns.

---

## Acknowledgments

The authors thank the RoofScan Pro engineering team for their operational support during the evaluation period and the broader site reliability engineering community for establishing the foundational practices upon which this work builds.

---

## References

[1] B. Beyer et al., "Site Reliability Engineering: How Google Runs Production Systems," O'Reilly Media, 2016.

[2] C. Majors, L. Fong, and G. Miranda, "Observability Engineering: Achieving Production Excellence," O'Reilly Media, 2022.

[3] OpenTelemetry Authors, "OpenTelemetry: A CNCF Observability Framework," https://opentelemetry.io, 2023.

[4] B. H. Sigelman et al., "Dapper, a Large-Scale Distributed Systems Tracing Infrastructure," Google Technical Report, 2010.

[5] J. O. Kephart and D. M. Chess, "The Vision of Autonomic Computing," IEEE Computer, vol. 36, no. 1, pp. 41-50, 2003.

[6] Kubernetes Authors, "Horizontal Pod Autoscaling," https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale, 2023.

[7] Amazon Web Services, "AWS Auto Scaling," https://aws.amazon.com/autoscaling, 2023.

[8] C. Zhang et al., "AutoRemedy: An Automated Remediation System for Cloud Database Failures," Proc. ACM SIGMOD, pp. 1234-1246, 2022.

[9] Microsoft Azure, "Azure Automation: Runbook-Driven Remediation," https://docs.microsoft.com/azure/automation, 2023.

[10] PagerDuty Inc., "PagerDuty Incident Response," https://www.pagerduty.com, 2023.

[11] M. Fowler, "Event Sourcing," https://martinfowler.com/eaaDev/EventSourcing.html, 2005.

[12] J. Kreps, N. Narkhede, and J. Rao, "Kafka: A Distributed Messaging System for Log Processing," Proc. NetDB Workshop, pp. 1-7, 2011.

[13] C. Bird et al., "Assessing the State of Practice for Site Reliability Engineering," Proc. ACM ESEC/FSE, pp. 1540-1551, 2022.

[14] N. R. Herbst et al., "A Survey on the State of the Art in Self-Adaptive Resource Management," ACM Computing Surveys, vol. 54, no. 3, pp. 1-37, 2021.

[15] J. Allspaw, "Blameless PostMortems and a Just Culture," O'Reilly Media Blog, 2012.

[16] S. Nakagawa et al., "Microservices Monitoring with Semantic Logging," Proc. IEEE Cloud Computing, pp. 234-241, 2019.

[17] T. Treat, "Failure Modes in Distributed Systems," ACM Queue, vol. 15, no. 4, pp. 20-30, 2017.

[18] B. Treynor et al., "The Calculus of Service Availability," ACM Queue, vol. 15, no. 1, pp. 40-49, 2017.

[19] P. Deutsch, "The Eight Fallacies of Distributed Computing," Sun Microsystems, 1994.

[20] V. Cardellini et al., "A Survey on Self-Protection Capabilities in Autonomic Computing Systems," Proc. IEEE ICAC, pp. 101-106, 2020.

[21] J. H. Saltzer and M. F. Kaashoek, "Principles of Computer System Design: An Introduction," MIT Press, 2009.

[22] N. Brown, "Software Architecture Metrics," O'Reilly Media, 2022.

[23] W. Schultz et al., "Can Large Language Models Write Good Property-Based Tests?" arXiv:2311.01323, 2023.

[24] D. Sculley et al., "Hidden Technical Debt in Machine Learning Systems," Proc. NeurIPS, pp. 2503-2511, 2015.

[25] J. Mace, R. Roelke, and R. Fonseca, "Pivot Tracing: Dynamic Causal Monitoring for Distributed Systems," Proc. ACM SOSP, pp. 378-393, 2015.

[26] C. D. Manning and H. Schuetze, "Foundations of Statistical Natural Language Processing," MIT Press, 1999.

[27] B. Dolan-Gavitt et al., "LAVA: Large-Scale Automated Vulnerability Addition," Proc. IEEE S&P, pp. 275-289, 2016.

[28] A. Alspaugh et al., "Panda: Platform for Analytics and Data Exploration," Proc. ACM CIKM, pp. 2475-2478, 2020.

[29] L. A. Barroso, U. Holzle, and P. Ranganathan, "The Datacenter as a Computer: Designing Warehouse-Scale Machines," Morgan & Claypool, 2018.

[30] S. Boyd-Wickizer et al., "An Analysis of Linux Scalability to Many Cores," Proc. USENIX OSDI, pp. 1-16, 2010.
