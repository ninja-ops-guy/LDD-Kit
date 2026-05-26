# LDD-Kit: Generic Log-Driven Development Framework

## Vision
Drop-in observability infrastructure that adapts to any software project in <1 hour. Language-agnostic core with language-specific adapters.

## Architecture

```
ldd-kit/
  README.md                          # Quick start and adaptation guide
  config.yaml                        # Service configuration (name, language, features)
  core/                              # Generic templates (language-agnostic)
    event-schemas/
      base.json                      # JSON Schema for BaseEvent
    dashboards/
      service-overview.json.tmpl     # Grafana dashboard template
      service-pipeline.json.tmpl
      service-ai-inference.json.tmpl
    alerts/
      latency.yml.tmpl
      error-rate.yml.tmpl
      resource-exhaustion.yml.tmpl
    prompts/
      ldd-template.md.tmpl           # Kimi prompt template
      telemetry-agent.md             # Subagent spec
    scripts/
      validate_telemetry.py          # Generic AST validator
      validate_log_schemas.py        # Generic schema validator
      validate_dashboards.py         # Generic dashboard validator
      check_metrics.py               # Generic metric registry checker
      adapt.py                       # ONE-SCRIPT adaptation tool
    ci/
      github-actions.yml.tmpl
      gitlab-ci.yml.tmpl
    terraform/
      monitoring.tf                  # Generic GCP monitoring
      logging.tf
      trace.tf
      variables.tf
      main.tf
      outputs.tf
  adapters/                          # Language-specific implementations
    python/
      telemetry/
        __init__.py
        logging.py
        tracing.py
        metrics.py
        middleware.py
        remediation.py
      requirements.txt
    go/
      telemetry/
        logging.go
        tracing.go
        metrics.go
        middleware.go
      go.mod.additions
    nodejs/
      telemetry/
        index.ts
        logging.ts
        tracing.ts
        metrics.ts
        middleware.ts
      package.json.additions
    rust/
      telemetry/
        lib.rs
        logging.rs
        tracing.rs
        metrics.rs
        middleware.rs
      Cargo.toml.additions
  examples/
    python-fastapi/                  # What we built for RoofBot
    go-gin/                          # Example Go service
    nodejs-express/                  # Example Node.js service
    rust-axum/                       # Example Rust service
```

## Core Principles

1. **Config-driven:** One `config.yaml` drives all adaptation
2. **Language adapters:** Core is templates; adapters are real implementations
3. **Script-driven setup:** `python core/scripts/adapt.py --config config.yaml` generates the project
4. **Zero-runtime-dependency fallbacks:** Every adapter works without external backends (console/dev mode)
5. **CI-first:** Enforcement scripts are language-aware but generic in structure

## config.yaml Format

```yaml
service:
  name: my-service
  language: python          # python | go | nodejs | rust
  framework: fastapi        # fastapi | flask | gin | echo | express | axum | actix
  platform: gcp             # gcp | aws | azure | baremetal

features:
  ai_inference: true        # Enable AI-specific dashboards/metrics
  multi_tenant: true        # Enable tenant context propagation
  auth: true                # Enable auth event schemas

observability:
  structured_logging: true
  distributed_tracing: true
  prometheus_metrics: true
  grafana_dashboards: true
  alert_rules: true

events:
  # User-defined event schema list
  - domain: user
    events:
      - name: user.created
        fields:
          user_id: { type: string, required: true }
          email: { type: string, required: true }
      - name: user.deleted
  - domain: payment
    events:
      - name: payment.processed
      - name: payment.failed
```
