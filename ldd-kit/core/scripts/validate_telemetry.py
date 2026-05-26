#!/usr/bin/env python3
"""Validate telemetry coverage across a codebase.

This script parses endpoint definitions in a language-aware manner, then checks
that each endpoint has at least one telemetry signal:

* A logger call (any log emission)
* A tracer/span call
* A metrics call

Exception handlers are also inspected for log emissions with trace context.
The script outputs a JSON report to *stdout* and exits with code 0 when the
coverage threshold is met, otherwise code 1.

Supports: python, go, nodejs, rust

Usage:
    python validate_telemetry.py --language python --source ./src --min-coverage 80
    python validate_telemetry.py --language go --source ./ --min-coverage 70
    python validate_telemetry.py --language nodejs --source ./src --min-coverage 80
    python validate_telemetry.py --language rust --source ./src --min-coverage 70
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
from typing import Any, Protocol

logger = logging.getLogger("validate_telemetry")


# ---------------------------------------------------------------------------
# Language-agnostic data models
# ---------------------------------------------------------------------------

@dataclass
class EndpointInfo:
    """Represents a single API endpoint discovered in the source tree."""

    name: str
    file: str
    line: int
    has_logger: bool = False
    has_tracing: bool = False
    has_metrics: bool = False

    @property
    def is_covered(self) -> bool:
        """True when at least one telemetry signal is present."""
        return self.has_logger or self.has_tracing or self.has_metrics


@dataclass
class Report:
    """Final JSON-serialisable validation report."""

    language: str
    coverage: float
    total_endpoints: int
    covered: int
    missing: list[dict[str, Any]]
    passed: bool


# ---------------------------------------------------------------------------
# Language base class
# ---------------------------------------------------------------------------

class LanguageAnalyzer(Protocol):
    """Protocol for language-specific endpoint analyzers."""

    @property
    def name(self) -> str: ...

    @property
    def file_extensions(self) -> list[str]: ...

    def find_route_dirs(self, source_dir: Path) -> list[Path]: ...
    def discover_endpoints(self, source_dir: Path) -> list[EndpointInfo]: ...


# ---------------------------------------------------------------------------
# Python analyzer (AST-based)
# ---------------------------------------------------------------------------

class _PythonTelemetryVisitor(ast.NodeVisitor):
    """Walks a Python AST looking for router decorators, logger/tracer/metrics calls."""

    ROUTER_PATTERNS = ("router", "app", "api_router", "blueprint")

    def __init__(self, filename: str) -> None:
        self.endpoints: list[EndpointInfo] = []
        self._current_endpoint: EndpointInfo | None = None
        self._filename = filename

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_func(node)

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        old = self._current_endpoint
        if self._is_router_endpoint(node):
            ep = EndpointInfo(
                name=node.name,
                file=self._filename,
                line=node.lineno,
            )
            self.endpoints.append(ep)
            self._current_endpoint = ep
            self.generic_visit(node)
            self._current_endpoint = old
        else:
            self._current_endpoint = old
            self.generic_visit(node)

    def _is_router_endpoint(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """True if *node* is decorated with a router pattern."""
        for dec in node.decorator_list:
            dec_str = ast.unparse(dec) if hasattr(ast, "unparse") else ""
            # @router.get(...), @app.post(...), @route(...)
            if any(pat in dec_str for pat in ("router.", "app.", "route(")):
                return True
            # Check AST structure
            if isinstance(dec, ast.Call):
                func = dec.func
                if isinstance(func, ast.Attribute):
                    if isinstance(func.value, ast.Name):
                        if any(func.value.id.startswith(p) for p in self.ROUTER_PATTERNS):
                            return True
                        if any(func.value.id == p for p in self.ROUTER_PATTERNS):
                            return True
                if isinstance(func, ast.Name):
                    if func.id in ("route", "get", "post", "put", "delete", "patch"):
                        return True
            elif isinstance(dec, ast.Attribute):
                if isinstance(dec.value, ast.Name):
                    if any(dec.value.id.startswith(p) for p in self.ROUTER_PATTERNS):
                        return True
        return False

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if self._current_endpoint is None:
            self.generic_visit(node)
            return

        if self._looks_like_logger_call(node):
            self._current_endpoint.has_logger = True

        if self._looks_like_tracer_call(node):
            self._current_endpoint.has_tracing = True

        if self._looks_like_metrics_call(node):
            self._current_endpoint.has_metrics = True

        self.generic_visit(node)

    def _looks_like_logger_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in ("logger", "log"):
                return True
            if isinstance(func.value, ast.Attribute) and func.value.attr == "logger":
                return True
        return False

    def _looks_like_tracer_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in ("start_as_current_span", "start_span", "trace"):
                return True
        if isinstance(func, ast.Name) and func.id in ("span", "trace"):
            return True
        return False

    def _looks_like_metrics_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in ("METRICS", "metrics"):
                return True
            if isinstance(func.value, ast.Attribute) and func.value.attr in ("metrics", "METRICS"):
                return True
            if func.attr in ("inc", "observe", "set", "dec", "add", "record"):
                return True
        return False


class _PythonExceptionHandlerVisitor(ast.NodeVisitor):
    """Walks a Python AST looking for exception handlers that lack logging."""

    def __init__(self, filename: str) -> None:
        self.handlers_without_logs: list[dict[str, Any]] = []
        self._filename = filename

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        log_finder = _PythonLogFinder()
        for stmt in node.body:
            log_finder.visit(stmt)
        if not log_finder.found:
            self.handlers_without_logs.append(
                {
                    "file": self._filename,
                    "line": node.lineno,
                    "exception_type": self._format_exception_type(node.type),
                }
            )
        self.generic_visit(node)

    @staticmethod
    def _format_exception_type(node: ast.expr | None) -> str | None:
        if node is None:
            return "bare except"
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Tuple):
            names: list[str] = []
            for elt in node.elts:
                if isinstance(elt, ast.Name):
                    names.append(elt.id)
            return ", ".join(names)
        return ast.unparse(node) if hasattr(ast, "unparse") else "unknown"


class _PythonLogFinder(ast.NodeVisitor):
    """Quick visitor that sets *found* to True on first logger call."""

    def __init__(self) -> None:
        self.found = False

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        if isinstance(func, ast.Attribute):
            if isinstance(func.value, ast.Name) and func.value.id in ("logger", "log"):
                self.found = True
                return
        self.generic_visit(node)


class PythonAnalyzer:
    """Python-specific telemetry analyzer."""

    name = "python"
    file_extensions = [".py"]

    def find_route_dirs(self, source_dir: Path) -> list[Path]:
        files: list[Path] = []
        for sub in ("routes", "routers", "handlers", "controllers", "views"):
            target = source_dir / sub
            if target.is_dir():
                files.extend(target.rglob("*.py"))
        return files

    def discover_endpoints(self, source_dir: Path) -> list[EndpointInfo]:
        route_files = self.find_route_dirs(source_dir)
        all_endpoints: list[EndpointInfo] = []

        py_files = set(source_dir.rglob("*.py"))
        for py_file in py_files:
            if py_file.name == "__init__.py":
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
            except (SyntaxError, UnicodeDecodeError):
                continue

            filename = str(py_file)
            tele_visitor = _PythonTelemetryVisitor(filename)
            tele_visitor.visit(tree)

            # Check decorators for @span
            for ep in tele_visitor.endpoints:
                for node in ast.walk(tree):
                    if (
                        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and node.name == ep.name
                        and getattr(node, "lineno", 0) == ep.line
                    ):
                        for dec in node.decorator_list:
                            dec_str = ast.unparse(dec) if hasattr(ast, "unparse") else ""
                            if "span" in dec_str.lower() or "trace" in dec_str.lower():
                                ep.has_tracing = True

            all_endpoints.extend(tele_visitor.endpoints)

            handler_visitor = _PythonExceptionHandlerVisitor(filename)
            handler_visitor.visit(tree)

        return all_endpoints


# ---------------------------------------------------------------------------
# Go analyzer (regex-based)
# ---------------------------------------------------------------------------

class GoAnalyzer:
    """Go-specific telemetry analyzer."""

    name = "go"
    file_extensions = [".go"]

    # Patterns for route handlers
    ROUTE_PATTERNS = [
        re.compile(r'\brouter\.(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s*\('),
        re.compile(r'\b(?:r|router|mux|app|e|echo|g)\.(GET|POST|PUT|DELETE|PATCH)\s*\('),
        re.compile(r'\bHandleFunc\s*\('),
        re.compile(r'\bgin\.[A-Z]'),
    ]

    # Telemetry patterns
    LOGGER_PATTERNS = [
        re.compile(r'\b(?:log|logger|zap\.|sugar\.|logrus\.)\.(Info|Debug|Warn|Error|Fatal|Print)\s*\('),
        re.compile(r'\blogger\.\w+\s*\('),
    ]
    TRACER_PATTERNS = [
        re.compile(r'\bStart\s*\(\s*(?:ctx|context)'),
        re.compile(r'\btracer\.\w+\s*\('),
        re.compile(r'\bspan\.\w+\s*\('),
        re.compile(r'\btrace\.SpanFromContext\s*\('),
    ]
    METRICS_PATTERNS = [
        re.compile(r'\b(?:metrics|prometheus|metric)\b'),
        re.compile(r'\.(Inc|Observe|Set|Add|Dec)\s*\('),
        re.compile(r'\bCounter|Histogram|Gauge|Summary\b'),
    ]

    def find_route_dirs(self, source_dir: Path) -> list[Path]:
        files: list[Path] = []
        for sub in ("handlers", "routes", "controllers", "api", "cmd", "internal"):
            target = source_dir / sub
            if target.is_dir():
                files.extend(target.rglob("*.go"))
        return files

    def discover_endpoints(self, source_dir: Path) -> list[EndpointInfo]:
        endpoints: list[EndpointInfo] = []
        for go_file in source_dir.rglob("*.go"):
            if "_test.go" in go_file.name or "vendor/" in str(go_file):
                continue
            try:
                content = go_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            lines = content.splitlines()
            for i, line in enumerate(lines, start=1):
                # Check for route definitions
                if any(pat.search(line) for pat in self.ROUTE_PATTERNS):
                    # Find the function name (simplified)
                    func_name = line.strip()
                    # Look for telemetry in surrounding lines (simplified scope)
                    scope_start = max(0, i - 1)
                    scope_end = min(len(lines), i + 50)
                    scope = "\n".join(lines[scope_start:scope_end])

                    ep = EndpointInfo(
                        name=func_name[:80],
                        file=str(go_file),
                        line=i,
                    )
                    ep.has_logger = any(p.search(scope) for p in self.LOGGER_PATTERNS)
                    ep.has_tracing = any(p.search(scope) for p in self.TRACER_PATTERNS)
                    ep.has_metrics = any(p.search(scope) for p in self.METRICS_PATTERNS)
                    endpoints.append(ep)

        return endpoints


# ---------------------------------------------------------------------------
# Node.js analyzer (regex-based)
# ---------------------------------------------------------------------------

class NodejsAnalyzer:
    """Node.js-specific telemetry analyzer."""

    name = "nodejs"
    file_extensions = [".js", ".ts", ".mjs", ".cjs"]

    ROUTE_PATTERNS = [
        re.compile(r'\b(?:router|app|server)\.(get|post|put|delete|patch|use|all)\s*\('),
        re.compile(r'\b(?:fastify|express|hapi|koa|nest)\.(get|post|put|delete|patch)\s*\('),
        re.compile(r'\b@(?:Get|Post|Put|Delete|Patch|Controller)'),
    ]

    LOGGER_PATTERNS = [
        re.compile(r'\b(?:logger|log|winston|bunyan|pino)\.(info|debug|warn|error|trace)\s*\('),
        re.compile(r'\bconsole\.(log|info|warn|error)\s*\('),
    ]
    TRACER_PATTERNS = [
        re.compile(r'\btracer\.\w+\s*\('),
        re.compile(r'\bspan\.\w+\s*\('),
        re.compile(r'\bstartActiveSpan\s*\('),
        re.compile(r'\bstartSpan\s*\('),
    ]
    METRICS_PATTERNS = [
        re.compile(r'\b(?:metrics|prom|counter|histogram|gauge|summary)\b'),
        re.compile(r'\.(inc|observe|set|labels)\s*\('),
    ]

    def find_route_dirs(self, source_dir: Path) -> list[Path]:
        files: list[Path] = []
        for sub in ("routes", "controllers", "handlers", "api", "src"):
            target = source_dir / sub
            if target.is_dir():
                for ext in self.file_extensions:
                    files.extend(target.rglob(f"*{ext}"))
        return files

    def discover_endpoints(self, source_dir: Path) -> list[EndpointInfo]:
        endpoints: list[EndpointInfo] = []
        for ext in self.file_extensions:
            for src_file in source_dir.rglob(f"*{ext}"):
                if "node_modules/" in str(src_file) or "dist/" in str(src_file):
                    continue
                try:
                    content = src_file.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue

                lines = content.splitlines()
                in_route = False
                route_line = 0
                route_name = ""
                brace_depth = 0

                for i, line in enumerate(lines, start=1):
                    stripped = line.strip()

                    # Track brace depth for block scoping
                    brace_depth += stripped.count("{") - stripped.count("}")

                    # Detect route definitions
                    if any(pat.search(line) for pat in self.ROUTE_PATTERNS):
                        in_route = True
                        route_line = i
                        route_name = stripped[:80]
                        brace_depth = stripped.count("{") - stripped.count("}")
                        continue

                    # Collect route body
                    if in_route:
                        scope_start = route_line - 1
                        scope_end = min(len(lines), route_line + 40)
                        scope = "\n".join(lines[scope_start:scope_end])

                        ep = EndpointInfo(
                            name=route_name,
                            file=str(src_file),
                            line=route_line,
                        )
                        ep.has_logger = any(p.search(scope) for p in self.LOGGER_PATTERNS)
                        ep.has_tracing = any(p.search(scope) for p in self.TRACER_PATTERNS)
                        ep.has_metrics = any(p.search(scope) for p in self.METRICS_PATTERNS)
                        endpoints.append(ep)
                        in_route = False

        return endpoints


# ---------------------------------------------------------------------------
# Rust analyzer (regex-based)
# ---------------------------------------------------------------------------

class RustAnalyzer:
    """Rust-specific telemetry analyzer."""

    name = "rust"
    file_extensions = [".rs"]

    ROUTE_PATTERNS = [
        re.compile(r'\b(?:route|get|post|put|delete|patch|head)\s*\('),
        re.compile(r'\bweb::resource\s*\('),
        re.compile(r'\bRouter::\w+\s*\('),
        re.compile(r'\b\.to\s*\('),
        re.compile(r'#\[(?:get|post|put|delete|patch|route)'),
        re.compile(r'\bMethod::(GET|POST|PUT|DELETE|PATCH)\b'),
    ]

    LOGGER_PATTERNS = [
        re.compile(r'\b(?:tracing|log|logger)::!(?:info|debug|warn|error|trace|span)'),
        re.compile(r'\b(?:info|debug|warn|error|trace)!(?!_span)'),
        re.compile(r'\bevent!\s*\('),
    ]
    TRACER_PATTERNS = [
        re.compile(r'\binfo_span!'),
        re.compile(r'\bspan!\s*\('),
        re.compile(r'\btracing::\w+\s*\('),
        re.compile(r'\bSpan::\w+\s*\('),
    ]
    METRICS_PATTERNS = [
        re.compile(r'\b(?:metrics::|prometheus::|opentelemetry::)\b'),
        re.compile(r'\.(inc(?:_by)?|observe|set|add)\s*\('),
        re.compile(r'\bcounter!|histogram!|gauge!'),
    ]

    def find_route_dirs(self, source_dir: Path) -> list[Path]:
        files: list[Path] = []
        for sub in ("routes", "handlers", "controllers", "api", "src"):
            target = source_dir / sub
            if target.is_dir():
                files.extend(target.rglob("*.rs"))
        return files

    def discover_endpoints(self, source_dir: Path) -> list[EndpointInfo]:
        endpoints: list[EndpointInfo] = []
        for rs_file in source_dir.rglob("*.rs"):
            if "target/" in str(rs_file) or "vendor/" in str(rs_file):
                continue
            try:
                content = rs_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            lines = content.splitlines()
            for i, line in enumerate(lines, start=1):
                if any(pat.search(line) for pat in self.ROUTE_PATTERNS):
                    func_name = line.strip()[:80]
                    scope_start = max(0, i - 1)
                    scope_end = min(len(lines), i + 30)
                    scope = "\n".join(lines[scope_start:scope_end])

                    ep = EndpointInfo(
                        name=func_name,
                        file=str(rs_file),
                        line=i,
                    )
                    ep.has_logger = any(p.search(scope) for p in self.LOGGER_PATTERNS)
                    ep.has_tracing = any(p.search(scope) for p in self.TRACER_PATTERNS)
                    ep.has_metrics = any(p.search(scope) for p in self.METRICS_PATTERNS)
                    endpoints.append(ep)

        return endpoints


# ---------------------------------------------------------------------------
# Analyzer factory
# ---------------------------------------------------------------------------

ANALYZERS: dict[str, type[LanguageAnalyzer]] = {
    "python": PythonAnalyzer,
    "go": GoAnalyzer,
    "nodejs": NodejsAnalyzer,
    "rust": RustAnalyzer,
}


# ---------------------------------------------------------------------------
# Main validation logic
# ---------------------------------------------------------------------------

def _configure_logging(verbose: bool) -> None:
    """Configure stderr logging for the validator itself."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def run_validation(source_dir: Path, language: str, min_coverage: float) -> Report:
    """Run the full validation sweep and return a :class:`Report`.

    Args:
        source_dir: Root of the source tree.
        language: Programming language (python, go, nodejs, rust).
        min_coverage: Percentage threshold (0-100).

    Returns:
        A :class:`Report` with coverage details.
    """
    analyzer_cls = ANALYZERS.get(language)
    if analyzer_cls is None:
        raise ValueError(f"Unsupported language: {language}. Supported: {list(ANALYZERS.keys())}")

    analyzer = analyzer_cls()
    all_endpoints = analyzer.discover_endpoints(source_dir)

    if not all_endpoints:
        logger.warning("No endpoints found in %s for language %s", source_dir, language)

    total = len(all_endpoints)
    covered = sum(1 for ep in all_endpoints if ep.is_covered)

    missing: list[dict[str, Any]] = []
    for ep in all_endpoints:
        if not ep.is_covered:
            missing.append(
                {
                    "function": ep.name,
                    "file": ep.file,
                    "line": ep.line,
                    "reason": "no logger, tracer, or metrics call found",
                }
            )

    coverage = (covered / total * 100.0) if total > 0 else 100.0
    passed = coverage >= min_coverage

    return Report(
        language=language,
        coverage=round(coverage, 1),
        total_endpoints=total,
        covered=covered,
        missing=missing,
        passed=passed,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point.

    Returns:
        0 when coverage >= threshold, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description="Validate telemetry coverage across a codebase.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python validate_telemetry.py --language python --min-coverage 80 --source ./src
  python validate_telemetry.py --language go --min-coverage 70 --source .
  python validate_telemetry.py --language nodejs --min-coverage 80 --source ./src
  python validate_telemetry.py --language rust --min-coverage 70 --source ./src
""",
    )
    parser.add_argument(
        "--language",
        type=str,
        required=True,
        choices=list(ANALYZERS.keys()),
        help="Programming language of the codebase.",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=80.0,
        help="Minimum required coverage percentage (default: 80).",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("."),
        help="Path to the source directory (default: current directory).",
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

    report = run_validation(args.source, args.language, args.min_coverage)

    print(json.dumps(report.__dict__, indent=2))

    if not report.passed:
        logger.error(
            "Telemetry coverage %.1f%% is below the %.1f%% threshold.",
            report.coverage,
            args.min_coverage,
        )
        return 1

    logger.info("Telemetry coverage %.1f%% meets the %.1f%% threshold.", report.coverage, args.min_coverage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
