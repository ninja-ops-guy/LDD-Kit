#!/usr/bin/env python3
"""
LDD-Kit Adaptation Script

Reads config.yaml and generates a complete, project-specific LDD pipeline.

Usage:
    python core/scripts/adapt.py --config config.yaml --output ./my-service-telemetry/
    python core/scripts/adapt.py --config config.yaml --output ./my-service-telemetry/ --language python
    python core/scripts/adapt.py --config config.yaml --dry-run

Requirements:
    pip install jinja2 pyyaml

Exit codes:
    0 - adaptation successful
    1 - configuration error or generation failure
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("ldd-kit-adapt")

# ---------------------------------------------------------------------------
# Optional dependency handling
# ---------------------------------------------------------------------------

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

try:
    from jinja2 import Environment, BaseLoader
except ImportError:
    Environment = None  # type: ignore[misc,assignment]
    BaseLoader = None  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ServiceConfig:
    """Parsed service configuration."""
    name: str
    language: str
    framework: str
    platform: str

    @property
    def metric_prefix(self) -> str:
        """Convert service name to snake_case metric prefix."""
        return self.name.replace("-", "_").lower()

    @property
    def dashboard_uid_prefix(self) -> str:
        """Convert service name to kebab-case dashboard UID prefix."""
        return self.name.lower()


@dataclass
class FeatureFlags:
    """Parsed feature flags."""
    ai_inference: bool = False
    multi_tenant: bool = False
    auth: bool = False
    async_workflows: bool = False


@dataclass
class ObservabilityConfig:
    """Parsed observability configuration."""
    structured_logging: bool = True
    distributed_tracing: bool = True
    prometheus_metrics: bool = True
    grafana_dashboards: bool = True
    dashboard_format: str = "json"
    alert_rules: bool = True
    ci_enforcement: bool = True


@dataclass
class EventField:
    """An event field definition."""
    name: str
    field_type: str
    required: bool = True
    pii: bool = False


@dataclass
class EventDef:
    """An event definition."""
    name: str
    domain: str
    fields: list[EventField] = field(default_factory=list)


@dataclass
class AdaptationReport:
    """Summary report of the adaptation process."""
    service_name: str
    language: str
    framework: str
    files_generated: int
    files_skipped: int
    output_dir: str
    features: list[str]
    generated_files: list[str]
    passed: bool = True


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML config file."""
    if yaml is None:
        logger.error("PyYAML is required. Install it: pip install pyyaml")
        sys.exit(1)

    content = path.read_text(encoding="utf-8")
    return yaml.safe_load(content)


def parse_config(config_path: Path) -> tuple[ServiceConfig, FeatureFlags, ObservabilityConfig, list[EventDef]]:
    """Parse the configuration file and return structured config objects."""
    raw = _load_yaml(config_path)

    service_raw = raw.get("service", {})
    service = ServiceConfig(
        name=service_raw.get("name", "my-service"),
        language=service_raw.get("language", "python"),
        framework=service_raw.get("framework", "fastapi"),
        platform=service_raw.get("platform", "gcp"),
    )

    features_raw = raw.get("features", {})
    features = FeatureFlags(
        ai_inference=features_raw.get("ai_inference", False),
        multi_tenant=features_raw.get("multi_tenant", False),
        auth=features_raw.get("auth", False),
        async_workflows=features_raw.get("async_workflows", False),
    )

    obs_raw = raw.get("observability", {})
    # Handle grafana_dashboards as either a boolean or a dict with enabled/format
    dashboard_format = "json"
    grafana_raw = obs_raw.get("grafana_dashboards", True)
    if isinstance(grafana_raw, dict):
        grafana_dashboards = grafana_raw.get("enabled", True)
        dashboard_format = grafana_raw.get("format", "json")
    else:
        grafana_dashboards = bool(grafana_raw)

    observability = ObservabilityConfig(
        structured_logging=obs_raw.get("structured_logging", True),
        distributed_tracing=obs_raw.get("distributed_tracing", True),
        prometheus_metrics=obs_raw.get("prometheus_metrics", True),
        grafana_dashboards=grafana_dashboards,
        dashboard_format=dashboard_format,
        alert_rules=obs_raw.get("alert_rules", True),
        ci_enforcement=obs_raw.get("ci_enforcement", True),
    )

    events: list[EventDef] = []
    for domain_group in raw.get("events", []):
        domain = domain_group.get("domain", "unknown")
        for evt in domain_group.get("events", []):
            if isinstance(evt, str):
                events.append(EventDef(name=evt, domain=domain))
            elif isinstance(evt, dict):
                event_def = EventDef(name=evt["name"], domain=domain)
                for fname, fspec in evt.get("fields", {}).items():
                    if isinstance(fspec, dict):
                        event_def.fields.append(EventField(
                            name=fname,
                            field_type=fspec.get("type", "string"),
                            required=fspec.get("required", False),
                            pii=fspec.get("pii", False),
                        ))
                    else:
                        event_def.fields.append(EventField(
                            name=fname, field_type="string", required=False
                        ))
                events.append(event_def)

    return service, features, observability, events


# ---------------------------------------------------------------------------
# Jinja2 templating
# ---------------------------------------------------------------------------

def _get_jinja_env() -> Environment:
    """Get or create a Jinja2 environment."""
    if Environment is None:
        logger.error("Jinja2 is required. Install it: pip install jinja2")
        sys.exit(1)
    return Environment(loader=BaseLoader())


def render_template(template_content: str, context: dict[str, Any]) -> str:
    """Render a Jinja2 template string with the given context."""
    env = _get_jinja_env()
    template = env.from_string(template_content)
    return template.render(**context)


def _to_snake_case(name: str) -> str:
    """Convert kebab-case or camelCase to snake_case."""
    return name.replace("-", "_").lower()


def _to_camel_case(snake_str: str) -> str:
    """Convert snake_case to CamelCase."""
    components = snake_str.split("_")
    return "".join(x.capitalize() for x in components)


def _to_kebab_case(name: str) -> str:
    """Convert snake_case to kebab-case."""
    return name.replace("_", "-").lower()


def build_context(
    service: ServiceConfig,
    features: FeatureFlags,
    observability: ObservabilityConfig,
) -> dict[str, Any]:
    """Build the Jinja2 context for template rendering."""
    return {
        "SERVICE_NAME": service.name,
        "METRIC_PREFIX": service.metric_prefix,
        "DASHBOARD_UID_PREFIX": service.dashboard_uid_prefix,
        "language": service.language,
        "framework": service.framework,
        "platform": service.platform,
        "ai_inference": features.ai_inference,
        "multi_tenant": features.multi_tenant,
        "auth": features.auth,
        "async_workflows": features.async_workflows,
    }


# ---------------------------------------------------------------------------
# Template file discovery
# ---------------------------------------------------------------------------

def discover_templates(core_dir: Path) -> list[Path]:
    """Discover all .tmpl files in the core directory."""
    templates: list[Path] = []
    for root, _dirs, files in os.walk(core_dir):
        for fname in files:
            if fname.endswith(".tmpl"):
                templates.append(Path(root) / fname)
    return sorted(templates)


# ---------------------------------------------------------------------------
# Output path computation
# ---------------------------------------------------------------------------

def compute_output_path(
    template_path: Path,
    output_dir: Path,
    core_dir: Path,
    context: dict[str, Any],
) -> Path:
    """Compute the output path for a template, stripping .tmpl extension."""
    # Compute relative path from core/ directory
    rel = template_path.relative_to(core_dir)
    # Strip .tmpl
    name = rel.name.replace(".tmpl", "")
    # Substitute placeholders in directory name
    rel_parts = list(rel.parent.parts)
    substituted_parts = [p.replace("service-", f"{context['DASHBOARD_UID_PREFIX']}-").replace("service_", f"{context['METRIC_PREFIX']}_") for p in rel_parts]

    # Also substitute in filename
    name = name.replace("service-", f"{context['DASHBOARD_UID_PREFIX']}-")
    name = name.replace("service_", f"{context['METRIC_PREFIX']}_")
    name = name.replace("{{ SERVICE_NAME }}", context["SERVICE_NAME"])
    name = name.replace("{{ METRIC_PREFIX }}", context["METRIC_PREFIX"])

    return output_dir / Path(*substituted_parts) / name


# ---------------------------------------------------------------------------
# Event schema generators
# ---------------------------------------------------------------------------

def generate_event_schemas(
    events: list[EventDef],
    language: str,
    output_dir: Path,
    context: dict[str, Any],
) -> list[str]:
    """Generate event schema files for the configured language.

    Returns:
        List of generated file paths.
    """
    generated: list[str] = []
    schemas_dir = output_dir / "telemetry" / "schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)

    # Group events by domain
    by_domain: dict[str, list[EventDef]] = {}
    for evt in events:
        by_domain.setdefault(evt.domain, []).append(evt)

    if language == "python":
        generated.extend(_gen_python_schemas(by_domain, schemas_dir, context))
    elif language == "go":
        generated.extend(_gen_go_schemas(by_domain, schemas_dir, context))
    elif language == "nodejs":
        generated.extend(_gen_nodejs_schemas(by_domain, schemas_dir, context))
    elif language == "rust":
        generated.extend(_gen_rust_schemas(by_domain, schemas_dir, context))

    return generated


def _gen_python_schemas(
    by_domain: dict[str, list[EventDef]],
    schemas_dir: Path,
    context: dict[str, Any],
) -> list[str]:
    """Generate Pydantic models for Python."""
    generated: list[str] = []

    init_lines: list[str] = [
        '"""Auto-generated event schemas."""',
        "",
    ]

    for domain, evts in by_domain.items():
        filename = f"{domain}.py"
        filepath = schemas_dir / filename
        lines: list[str] = [
            f'"""Auto-generated event schemas for {domain}."""',
            "",
            "from __future__ import annotations",
            "from datetime import datetime",
            "from typing import Optional",
            "from pydantic import BaseModel, Field",
            "",
        ]

        for evt in evts:
            class_name = _to_camel_case(evt.name.replace(".", "_"))
            lines.append(f"class {class_name}(BaseModel):")
            lines.append(f'    """Schema for event: {evt.name}"""')
            lines.append("")
            lines.append('    event_type: str = Field(default="{}")'.format(evt.name))
            lines.append('    timestamp: Optional[datetime] = None')
            lines.append('    trace_id: Optional[str] = None')
            lines.append('    span_id: Optional[str] = None')

            if context.get("multi_tenant"):
                lines.append('    tenant_id: Optional[str] = None')

            for f in evt.fields:
                py_type = {"string": "str", "integer": "int", "boolean": "bool", "number": "float", "float": "float"}.get(f.field_type, "str")
                optional = "" if f.required else "Optional["
                optional_close = "" if f.required else "] = None"
                field_def = f"    {f.name}: {optional}{py_type}{optional_close}"
                if f.pii:
                    field_def += f'  # PII - handle with care'
                lines.append(field_def)

            lines.append("")
            init_lines.append(f"from .{domain} import {class_name}")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        generated.append(str(filepath))

    init_file = schemas_dir / "__init__.py"
    init_file.write_text("\n".join(init_lines), encoding="utf-8")
    generated.append(str(init_file))

    return generated


def _gen_go_schemas(
    by_domain: dict[str, list[EventDef]],
    schemas_dir: Path,
    context: dict[str, Any],
) -> list[str]:
    """Generate Go structs."""
    generated: list[str] = []

    for domain, evts in by_domain.items():
        filename = f"{domain}.go"
        filepath = schemas_dir / filename
        lines: list[str] = [
            f"// Auto-generated event schemas for {domain}",
            f"package schemas",
            "",
            "import (",
            '    "time"',
            ")",
            "",
        ]

        for evt in evts:
            struct_name = _to_camel_case(evt.name.replace(".", "_"))
            lines.append(f"// {struct_name} represents the event: {evt.name}")
            lines.append(f"type {struct_name} struct {{")
            lines.append(f'    EventType  string    `json:"event_type"`')
            lines.append(f'    Timestamp  time.Time `json:"timestamp"`')
            lines.append(f'    TraceID    string    `json:"trace_id,omitempty"`')
            lines.append(f'    SpanID     string    `json:"span_id,omitempty"`')
            if context.get("multi_tenant"):
                lines.append(f'    TenantID   string    `json:"tenant_id,omitempty"`')
            for f in evt.fields:
                go_type = {"string": "string", "integer": "int64", "boolean": "bool", "number": "float64", "float": "float64"}.get(f.field_type, "string")
                json_tag = f.name
                if not f.required:
                    go_type = f"*{go_type}"
                    json_tag += ",omitempty"
                comment = "  // PII" if f.pii else ""
                lines.append(f'    {_to_camel_case(f.name)} {go_type} `json:"{json_tag}"`{comment}')
            lines.append("}")
            lines.append("")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        generated.append(str(filepath))

    return generated


def _gen_nodejs_schemas(
    by_domain: dict[str, list[EventDef]],
    schemas_dir: Path,
    context: dict[str, Any],
) -> list[str]:
    """Generate TypeScript interfaces."""
    generated: list[str] = []

    for domain, evts in by_domain.items():
        filename = f"{domain}.ts"
        filepath = schemas_dir / filename
        lines: list[str] = [
            f"// Auto-generated event schemas for {domain}",
            "",
        ]

        for evt in evts:
            iface_name = _to_camel_case(evt.name.replace(".", "_"))
            lines.append(f"export interface {iface_name} {{")
            lines.append(f'    event_type: "{evt.name}";')
            lines.append(f'    timestamp?: string;')
            lines.append(f'    trace_id?: string;')
            lines.append(f'    span_id?: string;')
            if context.get("multi_tenant"):
                lines.append(f'    tenant_id?: string;')
            for f in evt.fields:
                ts_type = {"string": "string", "integer": "number", "boolean": "boolean", "number": "number", "float": "number"}.get(f.field_type, "string")
                optional = "" if f.required else "?"
                comment = "  // PII - handle with care" if f.pii else ""
                lines.append(f'    {f.name}{optional}: {ts_type};{comment}')
            lines.append("}")
            lines.append("")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        generated.append(str(filepath))

    return generated


def _gen_rust_schemas(
    by_domain: dict[str, list[EventDef]],
    schemas_dir: Path,
    context: dict[str, Any],
) -> list[str]:
    """Generate Rust structs."""
    generated: list[str] = []

    for domain, evts in by_domain.items():
        filename = f"{domain}.rs"
        filepath = schemas_dir / filename
        lines: list[str] = [
            f"// Auto-generated event schemas for {domain}",
            "",
            "use chrono::{DateTime, Utc};",
            "use serde::{Deserialize, Serialize};",
            "",
        ]

        for evt in evts:
            struct_name = _to_camel_case(evt.name.replace(".", "_"))
            lines.append("#[derive(Debug, Clone, Serialize, Deserialize)]")
            lines.append(f"pub struct {struct_name} {{")
            lines.append(f'    pub event_type: String,')
            lines.append(f'    pub timestamp: Option<DateTime<Utc>>,')
            lines.append(f'    pub trace_id: Option<String>,')
            lines.append(f'    pub span_id: Option<String>,')
            if context.get("multi_tenant"):
                lines.append(f'    pub tenant_id: Option<String>,')
            for f in evt.fields:
                rust_type = {"string": "String", "integer": "i64", "boolean": "bool", "number": "f64", "float": "f64"}.get(f.field_type, "String")
                optional = "" if f.required else "Option<"
                optional_close = "" if f.required else ">"
                attr = f'#[serde(rename = "{f.name}")]' if f.name != _to_snake_case(f.name) else ""
                if attr:
                    lines.append(f"    {attr}")
                comment = "  // PII - handle with care" if f.pii else ""
                lines.append(f'    pub {_to_snake_case(f.name)}: {optional}{rust_type}{optional_close},{comment}')
            lines.append("}")
            lines.append("")

        filepath.write_text("\n".join(lines), encoding="utf-8")
        generated.append(str(filepath))

    return generated


# ---------------------------------------------------------------------------
# CI workflow generator
# ---------------------------------------------------------------------------

def generate_ci_workflow(
    service: ServiceConfig,
    observability: ObservabilityConfig,
    output_dir: Path,
    context: dict[str, Any],
) -> list[str]:
    """Generate CI workflow files."""
    generated: list[str] = []

    if not observability.ci_enforcement:
        return generated

    ci_dir = output_dir / ".github" / "workflows"
    ci_dir.mkdir(parents=True, exist_ok=True)

    lang = service.language
    install_steps = {
        "python": [
            "      - name: Install dependencies",
            "        run: pip install -r requirements.txt",
        ],
        "go": [
            "      - name: Download Go modules",
            "        run: go mod download",
        ],
        "nodejs": [
            "      - name: Install npm packages",
            "        run: npm ci",
        ],
        "rust": [
            "      - name: Build",
            "        run: cargo build",
        ],
    }

    workflow = [
        "name: Telemetry Validation",
        "",
        "on:",
        "  push:",
        "    branches: [main, master]",
        "  pull_request:",
        "    branches: [main, master]",
        "",
        "jobs:",
        "  telemetry-checks:",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - uses: actions/checkout@v4",
        f"      - name: Set up {lang}",
    ]

    if lang == "python":
        workflow.extend([
            "        uses: actions/setup-python@v5",
            "        with:",
            '          python-version: "3.11"',
        ])
    elif lang == "go":
        workflow.extend([
            "        uses: actions/setup-go@v5",
            "        with:",
            '          go-version: "1.22"',
        ])
    elif lang == "nodejs":
        workflow.extend([
            "        uses: actions/setup-node@v4",
            "        with:",
            '          node-version: "20"',
        ])
    elif lang == "rust":
        workflow.extend([
            "        uses: dtolnay/rust-action@stable",
        ])

    workflow.extend(install_steps.get(lang, []))
    workflow.extend([
        "",
        "      - name: Validate telemetry coverage",
        f"        run: python core/scripts/validate_telemetry.py --language {lang} --min-coverage 80",
        "",
        "      - name: Validate log schemas",
        f"        run: python core/scripts/validate_log_schemas.py --language {lang}",
        "",
        "      - name: Check metrics consistency",
        f"        run: python core/scripts/check_metrics.py --language {lang}",
        "",
        "      - name: Validate dashboards",
        "        run: python core/scripts/validate_dashboards.py --dir telemetry/dashboards/",
    ])

    workflow_path = ci_dir / "telemetry.yml"
    workflow_path.write_text("\n".join(workflow), encoding="utf-8")
    generated.append(str(workflow_path))

    return generated


# ---------------------------------------------------------------------------
# Main adaptation logic
# ---------------------------------------------------------------------------

def adapt(config_path: Path, output_dir: Path, language_override: str | None, dry_run: bool) -> AdaptationReport:
    """Run the full adaptation process.

    Args:
        config_path: Path to the config.yaml file.
        output_dir: Directory where adapted files will be written.
        language_override: Optional language override from CLI.
        dry_run: If True, do not write files, just report what would be done.

    Returns:
        An :class:`AdaptationReport` with details of the adaptation.
    """
    service, features, observability, events = parse_config(config_path)

    # Apply CLI language override
    if language_override:
        service.language = language_override

    context = build_context(service, features, observability)
    generated_files: list[str] = []
    skipped = 0

    # Determine template directories
    script_dir = Path(__file__).resolve().parent
    core_dir = script_dir.parent

    # Discover templates
    templates = discover_templates(core_dir)

    # Filter templates based on features
    active_templates: list[Path] = []
    for tmpl in templates:
        name = tmpl.name
        if "ai-inference" in name and not features.ai_inference:
            skipped += 1
            continue
        if "pipeline" in name and not features.async_workflows:
            skipped += 1
            continue
        if not observability.alert_rules and "alerts" in str(tmpl):
            skipped += 1
            continue
        if not observability.grafana_dashboards and "dashboards" in str(tmpl):
            skipped += 1
            continue
        active_templates.append(tmpl)

    logger.info(
        "Adapting for service '%s' (%s/%s) — %d templates active, %d skipped",
        service.name,
        service.language,
        service.framework,
        len(active_templates),
        skipped,
    )

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    # Render each template
    for tmpl_path in active_templates:
        template_content = tmpl_path.read_text(encoding="utf-8")
        rendered = render_template(template_content, context)
        out_path = compute_output_path(tmpl_path, output_dir, core_dir, context)

        if dry_run:
            logger.info("[DRY-RUN] Would write: %s", out_path)
            generated_files.append(str(out_path))
            continue

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        generated_files.append(str(out_path))
        logger.debug("Written: %s", out_path)

    # Generate event schemas
    if events:
        if dry_run:
            schema_dir = output_dir / "telemetry" / "schemas"
            logger.info("[DRY-RUN] Would generate event schemas in: %s", schema_dir)
            generated_files.append(str(schema_dir))
        else:
            schema_files = generate_event_schemas(events, service.language, output_dir, context)
            generated_files.extend(schema_files)
            logger.info("Generated %d event schema files", len(schema_files))

    # Generate CI workflow
    if observability.ci_enforcement:
        if dry_run:
            ci_path = output_dir / ".github" / "workflows" / "telemetry.yml"
            logger.info("[DRY-RUN] Would generate CI workflow: %s", ci_path)
            generated_files.append(str(ci_path))
        else:
            ci_files = generate_ci_workflow(service, observability, output_dir, context)
            generated_files.extend(ci_files)
            logger.info("Generated %d CI workflow files", len(ci_files))

    active_features = []
    if features.ai_inference:
        active_features.append("ai_inference")
    if features.multi_tenant:
        active_features.append("multi_tenant")
    if features.auth:
        active_features.append("auth")
    if features.async_workflows:
        active_features.append("async_workflows")
    if observability.structured_logging:
        active_features.append("structured_logging")
    if observability.distributed_tracing:
        active_features.append("distributed_tracing")
    if observability.prometheus_metrics:
        active_features.append("prometheus_metrics")
    if observability.grafana_dashboards:
        active_features.append("grafana_dashboards")
    if observability.alert_rules:
        active_features.append("alert_rules")
    if observability.ci_enforcement:
        active_features.append("ci_enforcement")

    return AdaptationReport(
        service_name=service.name,
        language=service.language,
        framework=service.framework,
        files_generated=len(generated_files),
        files_skipped=skipped,
        output_dir=str(output_dir),
        features=active_features,
        generated_files=generated_files,
        passed=True,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point.

    Returns:
        0 on success, 1 on failure.
    """
    parser = argparse.ArgumentParser(
        description="LDD-Kit Adaptation Script — generates a complete, project-specific LDD pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python adapt.py --config config.yaml --output ./my-service-telemetry/
  python adapt.py --config config.yaml --output ./out/ --language python
  python adapt.py --config config.yaml --dry-run

Requirements:
    pip install jinja2 pyyaml
""",
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to the config.yaml file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("./ldd-output"),
        help="Output directory for generated files (default: ./ldd-output).",
    )
    parser.add_argument(
        "--language",
        type=str,
        choices=["python", "go", "nodejs", "rust"],
        default=None,
        help="Override the language specified in config.yaml.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be generated without writing files.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging to stderr.",
    )
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    if not args.config.exists():
        logger.error("Config file does not exist: %s", args.config)
        return 1

    try:
        report = adapt(
            config_path=args.config,
            output_dir=args.output,
            language_override=args.language,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        logger.exception("Adaptation failed: %s", exc)
        return 1

    # Print summary report
    summary = {
        "service": report.service_name,
        "language": report.language,
        "framework": report.framework,
        "files_generated": report.files_generated,
        "files_skipped": report.files_skipped,
        "output_dir": report.output_dir,
        "features": report.features,
        "generated_files": report.generated_files,
        "mode": "dry-run" if args.dry_run else "live",
        "passed": report.passed,
    }

    print(json.dumps(summary, indent=2))

    if report.passed:
        mode_str = "(dry-run)" if args.dry_run else ""
        logger.info(
            "Generated %d files for service '%s' in %s/%s %s",
            report.files_generated,
            report.service_name,
            report.language,
            report.framework,
            mode_str,
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
