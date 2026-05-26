#!/usr/bin/env python3
"""Validate metric registry consistency across a codebase.

This script parses the source tree for metric definitions (Counter,
Histogram, Gauge, Summary declarations) and compares them against all
metric usage sites (METRICS.record_* and Prometheus .inc()/.observe()/.set()
calls).

Flags:
* **Orphaned metrics** -- defined but never recorded anywhere.
* **Missing metrics** -- recorded but never defined.

Supports: python, go, nodejs, rust

Usage:
    python check_metrics.py --language python --source . --metrics-file telemetry/metrics.py
    python check_metrics.py --language go --source . --metrics-file telemetry/metrics.go
    python check_metrics.py --language nodejs --source . --metrics-file telemetry/metrics.ts
    python check_metrics.py --language rust --source . --metrics-file src/metrics.rs
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("check_metrics")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class MetricDef:
    """A metric definition extracted from source."""

    name: str
    line: int
    metric_type: str  # Counter, Histogram, Gauge, Summary, etc.
    file: str


@dataclass
class MetricUsage:
    """A metric usage site found in the source tree."""

    name: str
    file: str
    line: int
    method: str


@dataclass
class Report:
    """Final JSON-serialisable consistency report."""

    language: str
    passed: bool
    definitions: list[dict[str, Any]]
    usages: list[dict[str, Any]]
    orphaned: list[dict[str, Any]]
    missing: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Python analyzer
# ---------------------------------------------------------------------------

class _PythonMetricDefVisitor(ast.NodeVisitor):
    """Walks an AST looking for metric variable assignments."""

    TARGET_TYPES = frozenset(("Counter", "Histogram", "Gauge", "Summary"))

    def __init__(self, filename: str) -> None:
        self.defs: list[MetricDef] = []
        self._filename = filename

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for target in node.targets:
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Call):
                call = node.value
                if isinstance(call.func, ast.Name) and call.func.id in self.TARGET_TYPES:
                    self.defs.append(MetricDef(
                        name=target.id, line=node.lineno, metric_type=call.func.id, file=self._filename
                    ))
                elif isinstance(call.func, ast.Attribute) and call.func.attr in self.TARGET_TYPES:
                    self.defs.append(MetricDef(
                        name=target.id, line=node.lineno, metric_type=call.func.attr, file=self._filename
                    ))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if isinstance(node.target, ast.Name) and isinstance(node.value, ast.Call):
            call = node.value
            type_name = None
            if isinstance(call.func, ast.Name) and call.func.id in self.TARGET_TYPES:
                type_name = call.func.id
            elif isinstance(call.func, ast.Attribute) and call.func.attr in self.TARGET_TYPES:
                type_name = call.func.attr
            if type_name:
                self.defs.append(MetricDef(
                    name=node.target.id, line=node.lineno, metric_type=type_name, file=self._filename
                ))
        self.generic_visit(node)


class _PythonMetricUsageVisitor(ast.NodeVisitor):
    """Walks an AST looking for metric usage sites."""

    PROMETHEUS_MUTATORS = frozenset(("inc", "observe", "set", "dec", "time", "labels"))
    METRICS_PREFIX = frozenset(("METRICS", "metrics"))

    def __init__(self, filename: str) -> None:
        self.usages: list[MetricUsage] = []
        self._filename = filename

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in self.METRICS_PREFIX:
                if func.attr.startswith("record_") or func.attr in self.PROMETHEUS_MUTATORS:
                    self.usages.append(MetricUsage(
                        name=func.attr, file=self._filename, line=node.lineno, method=func.attr
                    ))
            if func.attr in self.PROMETHEUS_MUTATORS:
                var_name = self._resolve_variable(func.value)
                if var_name and var_name not in self.METRICS_PREFIX:
                    self.usages.append(MetricUsage(
                        name=var_name, file=self._filename, line=node.lineno, method=func.attr
                    ))
        self.generic_visit(node)

    @staticmethod
    def _resolve_variable(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None


def _parse_python_metrics_file(filepath: Path) -> list[MetricDef]:
    """Parse a Python metrics file and return metric definitions."""
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError) as exc:
        logger.error("Syntax error in %s:%s -- %s", filepath, getattr(exc, 'lineno', '?'), exc)
        return []

    visitor = _PythonMetricDefVisitor(str(filepath))
    visitor.visit(tree)
    return visitor.defs


def _collect_python_usages(source_dir: Path) -> list[MetricUsage]:
    """Walk the Python source tree and collect all metric usage sites."""
    usages: list[MetricUsage] = []
    for py_file in source_dir.rglob("*.py"):
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue
        visitor = _PythonMetricUsageVisitor(str(py_file))
        visitor.visit(tree)
        usages.extend(visitor.usages)
    return usages


# ---------------------------------------------------------------------------
# Go analyzer (regex-based)
# ---------------------------------------------------------------------------

class GoMetricAnalyzer:
    """Go-specific metric analyzer."""

    DEF_RE = re.compile(
        r'(?:prometheus\.\w+\{|promauto\.\w+\{|NewCounter|NewHistogram|NewGauge|NewSummary)\s*\(')
    DEF_NAME_RE = re.compile(r'(?:Name|Namespace)\s*:\s*["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']')
    USAGE_RE = re.compile(
        r'\b(\w+)\.(Inc|Observe|Set|Add|Dec|WithLabelValues|Collect)\s*\(')
    VAR_DEF_RE = re.compile(r'var\s+(\w+)\s*=\s*(?:prometheus|promauto)')

    def parse_definitions(self, metrics_file: Path) -> list[MetricDef]:
        defs: list[MetricDef] = []
        try:
            content = metrics_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return defs

        lines = content.splitlines()
        for i, line in enumerate(lines, start=1):
            if self.DEF_RE.search(line) or self.VAR_DEF_RE.search(line):
                name_match = self.DEF_NAME_RE.search(line)
                var_match = self.VAR_DEF_RE.search(line)
                name = name_match.group(1) if name_match else (var_match.group(1) if var_match else f"metric_{i}")

                mtype = "Unknown"
                if "Counter" in line:
                    mtype = "Counter"
                elif "Histogram" in line:
                    mtype = "Histogram"
                elif "Gauge" in line:
                    mtype = "Gauge"
                elif "Summary" in line:
                    mtype = "Summary"

                defs.append(MetricDef(name=name, line=i, metric_type=mtype, file=str(metrics_file)))
        return defs

    def collect_usages(self, source_dir: Path) -> list[MetricUsage]:
        usages: list[MetricUsage] = []
        for go_file in source_dir.rglob("*.go"):
            if "_test.go" in go_file.name or "vendor/" in str(go_file):
                continue
            try:
                content = go_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            lines = content.splitlines()
            for i, line in enumerate(lines, start=1):
                match = self.USAGE_RE.search(line)
                if match:
                    usages.append(MetricUsage(
                        name=match.group(1),
                        file=str(go_file),
                        line=i,
                        method=match.group(2),
                    ))
        return usages


# ---------------------------------------------------------------------------
# Node.js analyzer (regex-based)
# ---------------------------------------------------------------------------

class NodejsMetricAnalyzer:
    """Node.js-specific metric analyzer."""

    DEF_RE = re.compile(
        r'(?:new\s+(?:Counter|Histogram|Gauge|Summary)|createCounter|createHistogram|createGauge)\s*\(')
    DEF_NAME_RE = re.compile(r'["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']\s*:\s*\{')
    USAGE_RE = re.compile(r'\b(\w+)\.(inc|observe|set|labels|add)\s*\(')

    def parse_definitions(self, metrics_file: Path) -> list[MetricDef]:
        defs: list[MetricDef] = []
        for ext in (".ts", ".js", ".mjs", ".cjs"):
            target = metrics_file.with_suffix(ext)
            if target.exists():
                metrics_file = target
                break

        if not metrics_file.exists():
            return defs

        try:
            content = metrics_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return defs

        lines = content.splitlines()
        for i, line in enumerate(lines, start=1):
            if self.DEF_RE.search(line):
                name_match = self.DEF_NAME_RE.search(line)
                name = name_match.group(1) if name_match else f"metric_{i}"

                mtype = "Unknown"
                if "Counter" in line:
                    mtype = "Counter"
                elif "Histogram" in line:
                    mtype = "Histogram"
                elif "Gauge" in line:
                    mtype = "Gauge"
                elif "Summary" in line:
                    mtype = "Summary"

                defs.append(MetricDef(name=name, line=i, metric_type=mtype, file=str(metrics_file)))
        return defs

    def collect_usages(self, source_dir: Path) -> list[MetricUsage]:
        usages: list[MetricUsage] = []
        for ext in ("*.js", "*.ts", "*.mjs", "*.cjs"):
            for src_file in source_dir.rglob(ext):
                if "node_modules/" in str(src_file) or "dist/" in str(src_file):
                    continue
                try:
                    content = src_file.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue

                lines = content.splitlines()
                for i, line in enumerate(lines, start=1):
                    match = self.USAGE_RE.search(line)
                    if match:
                        usages.append(MetricUsage(
                            name=match.group(1),
                            file=str(src_file),
                            line=i,
                            method=match.group(2),
                        ))
        return usages


# ---------------------------------------------------------------------------
# Rust analyzer (regex-based)
# ---------------------------------------------------------------------------

class RustMetricAnalyzer:
    """Rust-specific metric analyzer."""

    DEF_RE = re.compile(r'\bregister_\w+!|lazy_static!\s*\{[^}]*(?:CounterVec|HistogramVec|GaugeVec|IntCounter)')
    DEF_NAME_RE = re.compile(r'"([a-zA-Z_:][a-zA-Z0-9_:]*)"')
    USAGE_RE = re.compile(r'\b(\w+)\.(inc(?:_by)?|observe|set|add|with_label_values)\s*\(')

    def parse_definitions(self, metrics_file: Path) -> list[MetricDef]:
        defs: list[MetricDef] = []
        if not metrics_file.exists():
            return defs

        try:
            content = metrics_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return defs

        lines = content.splitlines()
        for i, line in enumerate(lines, start=1):
            if self.DEF_RE.search(line):
                name_match = self.DEF_NAME_RE.search(line)
                name = name_match.group(1) if name_match else f"metric_{i}"

                mtype = "Unknown"
                if "Counter" in line:
                    mtype = "Counter"
                elif "Histogram" in line:
                    mtype = "Histogram"
                elif "Gauge" in line:
                    mtype = "Gauge"
                elif "Summary" in line:
                    mtype = "Summary"

                defs.append(MetricDef(name=name, line=i, metric_type=mtype, file=str(metrics_file)))
        return defs

    def collect_usages(self, source_dir: Path) -> list[MetricUsage]:
        usages: list[MetricUsage] = []
        for rs_file in source_dir.rglob("*.rs"):
            if "target/" in str(rs_file) or "vendor/" in str(rs_file):
                continue
            try:
                content = rs_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            lines = content.splitlines()
            for i, line in enumerate(lines, start=1):
                match = self.USAGE_RE.search(line)
                if match:
                    usages.append(MetricUsage(
                        name=match.group(1),
                        file=str(rs_file),
                        line=i,
                        method=match.group(2),
                    ))
        return usages


# ---------------------------------------------------------------------------
# Cross-reference logic
# ---------------------------------------------------------------------------

# Mapping from METRICS.record_* method names to metric names
def _cross_reference(
    definitions: list[MetricDef],
    usages: list[MetricUsage],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare definitions against usages and return (orphaned, missing)."""
    defined_names = {d.name for d in definitions}
    used_names: set[str] = set()

    for u in usages:
        used_names.add(u.name)

    orphaned: list[dict[str, Any]] = []
    for d in definitions:
        if d.name not in used_names:
            orphaned.append(
                {
                    "metric": d.name,
                    "type": d.metric_type,
                    "file": d.file,
                    "line": d.line,
                    "reason": "defined but never recorded (orphaned)",
                }
            )

    missing: list[dict[str, Any]] = []
    for u in usages:
        if u.name not in defined_names:
            missing.append(
                {
                    "metric": u.name,
                    "file": u.file,
                    "line": u.line,
                    "reason": "recorded but never defined (missing)",
                }
            )

    return orphaned, missing


# ---------------------------------------------------------------------------
# Language dispatch
# ---------------------------------------------------------------------------

def run_validation(source_dir: Path, metrics_file: Path, language: str) -> Report:
    """Run the full metric registry consistency check.

    Args:
        source_dir: Root of the source tree.
        metrics_file: Path to the metric definitions file.
        language: Programming language (python, go, nodejs, rust).

    Returns:
        A :class:`Report` with consistency details.
    """
    if language == "python":
        definitions = _parse_python_metrics_file(metrics_file)
        usages = _collect_python_usages(source_dir)
    elif language == "go":
        analyzer = GoMetricAnalyzer()
        definitions = analyzer.parse_definitions(metrics_file)
        usages = analyzer.collect_usages(source_dir)
    elif language == "nodejs":
        analyzer = NodejsMetricAnalyzer()
        definitions = analyzer.parse_definitions(metrics_file)
        usages = analyzer.collect_usages(source_dir)
    elif language == "rust":
        analyzer = RustMetricAnalyzer()
        definitions = analyzer.parse_definitions(metrics_file)
        usages = analyzer.collect_usages(source_dir)
    else:
        raise ValueError(f"Unsupported language: {language}")

    orphaned, missing = _cross_reference(definitions, usages)
    passed = len(orphaned) == 0 and len(missing) == 0

    return Report(
        language=language,
        passed=passed,
        definitions=[
            {"name": d.name, "type": d.metric_type, "line": d.line, "file": d.file}
            for d in definitions
        ],
        usages=[
            {"name": u.name, "file": u.file, "line": u.line, "method": u.method}
            for u in usages
        ],
        orphaned=orphaned,
        missing=missing,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _configure_logging(verbose: bool) -> None:
    """Configure stderr logging for the checker itself."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point.

    Returns:
        0 if registry is consistent, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description="Validate metric registry consistency for a codebase.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python check_metrics.py --language python --source . --metrics-file telemetry/metrics.py
  python check_metrics.py --language go --source . --metrics-file telemetry/metrics.go
  python check_metrics.py --language nodejs --source . --metrics-file telemetry/metrics.ts
  python check_metrics.py --language rust --source . --metrics-file src/metrics.rs
""",
    )
    parser.add_argument(
        "--language",
        type=str,
        required=True,
        choices=["python", "go", "nodejs", "rust"],
        help="Programming language of the codebase.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("."),
        help="Path to the source directory (default: current directory).",
    )
    parser.add_argument(
        "--metrics-file",
        type=Path,
        default=None,
        help="Path to the metrics definitions file (auto-detected if not provided).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging to stderr.",
    )
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    if not args.source.exists():
        logger.error("Source directory does not exist: %s", args.source)
        return 1

    # Auto-detect metrics file if not provided
    metrics_file = args.metrics_file
    if metrics_file is None:
        extensions = {
            "python": ".py",
            "go": ".go",
            "nodejs": ".ts",
            "rust": ".rs",
        }
        ext = extensions.get(args.language, ".py")
        candidates = [
            args.source / "telemetry" / f"metrics{ext}",
            args.source / "internal" / "metrics" / f"metrics{ext}",
            args.source / "metrics" / f"metrics{ext}",
            args.source / f"metrics{ext}",
        ]
        for c in candidates:
            if c.exists():
                metrics_file = c
                break
        if metrics_file is None:
            logger.error("Could not find metrics file. Searched: %s", [str(c) for c in candidates])
            return 1

    if not metrics_file.exists():
        logger.error("Metrics file does not exist: %s", metrics_file)
        return 1

    report = run_validation(args.source, metrics_file, args.language)

    print(json.dumps(report.__dict__, indent=2))

    if not report.passed:
        logger.error(
            "Found %d orphaned and %d missing metric(s).",
            len(report.orphaned),
            len(report.missing),
        )
        return 1

    logger.info(
        "All %d metric definition(s) consistent with %d usage site(s).",
        len(report.definitions),
        len(report.usages),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
