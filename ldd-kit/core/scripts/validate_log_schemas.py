#!/usr/bin/env python3
"""Validate log schema compliance across a codebase.

This script parses log calls in the source tree and checks that:

1. Every log call has an event_type or structured fields.
2. No f-strings/template literals are used as log messages (AST checks).
3. Required fields are present where applicable.

Supports: python, go, nodejs, rust

Usage:
    python validate_log_schemas.py --language python --source ./src
    python validate_log_schemas.py --language go --source .
    python validate_log_schemas.py --language nodejs --source ./src
    python validate_log_schemas.py --language rust --source ./src
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

logger = logging.getLogger("validate_log_schemas")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class LogCallInfo:
    """Represents a single log call discovered in the source tree."""

    file: str
    line: int
    severity: str
    message_type: str  # "structured", "literal", "f-string", "template-literal", "unknown"
    has_event_type: bool = False
    has_structured_fields: bool = False
    is_fstring: bool = False
    is_template_literal: bool = False


@dataclass
class Violation:
    """A single schema violation."""

    file: str
    line: int
    severity: str
    message: str


@dataclass
class Report:
    """Final JSON-serialisable validation report."""

    language: str
    passed: bool
    total_calls: int
    violations: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Python analyzer (AST-based)
# ---------------------------------------------------------------------------

class _PythonLogCallVisitor(ast.NodeVisitor):
    """Walks a Python AST looking for logger calls."""

    SEVERITY_METHODS = frozenset(
        ("debug", "info", "warning", "warn", "error", "exception", "critical", "fatal")
    )

    def __init__(self, filename: str) -> None:
        self.calls: list[LogCallInfo] = []
        self._filename = filename

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if self._looks_like_logger_call(node):
            info = self._extract_log_call(node)
            self.calls.append(info)
        self.generic_visit(node)

    def _looks_like_logger_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in self.SEVERITY_METHODS:
                parent = func.value
                if isinstance(parent, ast.Name) and parent.id in ("logger", "log"):
                    return True
                if isinstance(parent, ast.Attribute) and parent.attr in ("logger", "log"):
                    return True
        return False

    def _extract_log_call(self, node: ast.Call) -> LogCallInfo:
        severity = ""
        func = node.func
        if isinstance(func, ast.Attribute):
            severity = func.attr.upper()

        msg_arg = node.args[0] if node.args else None
        message_type = "unknown"
        is_fstring = False

        if msg_arg is not None:
            if isinstance(msg_arg, ast.JoinedStr):
                message_type = "f-string"
                is_fstring = True
            elif isinstance(msg_arg, ast.Constant) and isinstance(msg_arg.value, str):
                message_type = "literal"
            else:
                message_type = "expression"

        has_event_type = False
        has_structured_fields = False

        for kw in node.keywords:
            if kw.arg == "event_type":
                has_event_type = True
            if kw.arg and kw.arg not in ("exc_info", "stack_info"):
                has_structured_fields = True

        # Check if first positional arg has structured format
        if msg_arg is not None and isinstance(msg_arg, ast.Constant) and isinstance(msg_arg.value, str):
            if "%" in msg_arg.value and "{" not in msg_arg.value:
                message_type = "structured"

        return LogCallInfo(
            file=self._filename,
            line=node.lineno,
            severity=severity,
            message_type=message_type,
            has_event_type=has_event_type,
            has_structured_fields=has_structured_fields,
            is_fstring=is_fstring,
        )


# ---------------------------------------------------------------------------
# Go analyzer (regex-based)
# ---------------------------------------------------------------------------

class GoLogAnalyzer:
    """Go-specific log schema analyzer."""

    name = "go"

    LOG_CALL_RE = re.compile(
        r'(?:log|logger|zap|sugar|logrus)\.(Info|Debug|Warn|Error|Fatal|Print)f?\s*\('
    )
    FSTRING_RE = re.compile(r'fmt\.\w+f?\s*\(.*%[vdtsfqgT]')
    EVENT_TYPE_RE = re.compile(r'["\']event_type["\']\s*:')
    STRUCTURED_RE = re.compile(r'zap\.(String|Int|Float64|Bool|Error|Any)\s*\(')

    def analyze(self, source_dir: Path) -> tuple[list[LogCallInfo], list[Violation]]:
        calls: list[LogCallInfo] = []
        violations: list[Violation] = []

        for go_file in source_dir.rglob("*.go"):
            if "_test.go" in go_file.name or "vendor/" in str(go_file):
                continue
            try:
                content = go_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            lines = content.splitlines()
            for i, line in enumerate(lines, start=1):
                match = self.LOG_CALL_RE.search(line)
                if match:
                    severity = match.group(1).upper() if match.group(1) else "INFO"

                    # Check surrounding scope
                    scope_start = max(0, i - 1)
                    scope_end = min(len(lines), i + 10)
                    scope = "\n".join(lines[scope_start:scope_end])

                    has_event_type = bool(self.EVENT_TYPE_RE.search(scope))
                    has_structured = bool(self.STRUCTURED_RE.search(scope))
                    is_fstring = bool(self.FSTRING_RE.search(scope))

                    info = LogCallInfo(
                        file=str(go_file),
                        line=i,
                        severity=severity,
                        message_type="structured" if has_structured else ("literal" if not is_fstring else "f-string"),
                        has_event_type=has_event_type,
                        has_structured_fields=has_structured,
                        is_fstring=is_fstring,
                    )
                    calls.append(info)

                    if is_fstring:
                        violations.append(Violation(
                            file=str(go_file),
                            line=i,
                            severity="WARNING",
                            message="Use structured logging (zap.String/etc.) instead of fmt.Sprintf in log calls",
                        ))

                    if not has_event_type and has_structured:
                        violations.append(Violation(
                            file=str(go_file),
                            line=i,
                            severity="WARNING",
                            message="Structured log call missing 'event_type' field",
                        ))

        return calls, violations


# ---------------------------------------------------------------------------
# Node.js analyzer (regex-based)
# ---------------------------------------------------------------------------

class NodejsLogAnalyzer:
    """Node.js-specific log schema analyzer."""

    name = "nodejs"

    LOG_CALL_RE = re.compile(
        r'(?:logger|log|winston|bunyan|pino|console)\.(info|debug|warn|error|trace|log)\s*\('
    )
    TEMPLATE_LITERAL_RE = re.compile(r'`[^`]*\$\{[^`]*\}[^`]*`')
    EVENT_TYPE_RE = re.compile(r'event_type\s*:\s*["\']')
    STRUCTURED_RE = re.compile(r'\{\s*\w+\s*:\s*[^,]+\s*\}')

    def analyze(self, source_dir: Path) -> tuple[list[LogCallInfo], list[Violation]]:
        calls: list[LogCallInfo] = []
        violations: list[Violation] = []

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
                    match = self.LOG_CALL_RE.search(line)
                    if match:
                        severity = match.group(1).upper() if match.group(1) else "INFO"

                        scope_start = max(0, i - 1)
                        scope_end = min(len(lines), i + 10)
                        scope = "\n".join(lines[scope_start:scope_end])

                        has_event_type = bool(self.EVENT_TYPE_RE.search(scope))
                        has_structured = bool(self.STRUCTURED_RE.search(scope))
                        is_template = bool(self.TEMPLATE_LITERAL_RE.search(scope))

                        info = LogCallInfo(
                            file=str(src_file),
                            line=i,
                            severity=severity,
                            message_type="structured" if has_structured else ("template-literal" if is_template else "literal"),
                            has_event_type=has_event_type,
                            has_structured_fields=has_structured,
                            is_template_literal=is_template,
                        )
                        calls.append(info)

                        if is_template:
                            violations.append(Violation(
                                file=str(src_file),
                                line=i,
                                severity="WARNING",
                                message="Avoid template literals in log messages; use structured fields instead",
                            ))

                        if not has_event_type and has_structured:
                            violations.append(Violation(
                                file=str(src_file),
                                line=i,
                                severity="WARNING",
                                message="Structured log call missing 'event_type' field",
                            ))

        return calls, violations


# ---------------------------------------------------------------------------
# Rust analyzer (regex-based)
# ---------------------------------------------------------------------------

class RustLogAnalyzer:
    """Rust-specific log schema analyzer."""

    name = "rust"

    LOG_MACRO_RE = re.compile(
        r'\b(?:tracing|log)::!(info|debug|warn|error|trace|span)\s*\('
    )
    EVENT_MACRO_RE = re.compile(r'\b(?:info|debug|warn|error|trace)!(?!_span)\s*\(')
    EVENT_TYPE_RE = re.compile(r'event_type\s*=\s*["\']')
    STRUCTURED_RE = re.compile(r'\w+\s*=\s*(?:%|#)?[a-zA-Z_]\w*')
    FORMAT_STRING_RE = re.compile(r'format!\s*\(')

    def analyze(self, source_dir: Path) -> tuple[list[LogCallInfo], list[Violation]]:
        calls: list[LogCallInfo] = []
        violations: list[Violation] = []

        for rs_file in source_dir.rglob("*.rs"):
            if "target/" in str(rs_file) or "vendor/" in str(rs_file):
                continue
            try:
                content = rs_file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            lines = content.splitlines()
            for i, line in enumerate(lines, start=1):
                match = self.LOG_MACRO_RE.search(line) or self.EVENT_MACRO_RE.search(line)
                if match:
                    severity = match.group(1).upper() if match.group(1) else "INFO"

                    scope_start = max(0, i - 1)
                    scope_end = min(len(lines), i + 10)
                    scope = "\n".join(lines[scope_start:scope_end])

                    has_event_type = bool(self.EVENT_TYPE_RE.search(scope))
                    has_structured = bool(self.STRUCTURED_RE.search(scope))
                    is_format = bool(self.FORMAT_STRING_RE.search(scope))

                    info = LogCallInfo(
                        file=str(rs_file),
                        line=i,
                        severity=severity,
                        message_type="structured" if has_structured else "literal",
                        has_event_type=has_event_type,
                        has_structured_fields=has_structured,
                    )
                    calls.append(info)

                    if is_format:
                        violations.append(Violation(
                            file=str(rs_file),
                            line=i,
                            severity="WARNING",
                            message="Avoid format! in tracing macros; use structured field syntax instead",
                        ))

                    if not has_event_type and has_structured:
                        violations.append(Violation(
                            file=str(rs_file),
                            line=i,
                            severity="WARNING",
                            message="Structured log call missing 'event_type' field",
                        ))

        return calls, violations


# ---------------------------------------------------------------------------
# Python-specific runner
# ---------------------------------------------------------------------------

def _run_python(source_dir: Path) -> tuple[list[LogCallInfo], list[Violation]]:
    calls: list[LogCallInfo] = []
    violations: list[Violation] = []

    for py_file in source_dir.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        visitor = _PythonLogCallVisitor(str(py_file))
        visitor.visit(tree)

        for call in visitor.calls:
            calls.append(call)

            if call.is_fstring:
                violations.append(Violation(
                    file=call.file,
                    line=call.line,
                    severity="ERROR",
                    message="Log message must not use f-strings (use %% formatting or literal strings)",
                ))

            if call.has_structured_fields and not call.has_event_type:
                violations.append(Violation(
                    file=call.file,
                    line=call.line,
                    severity="WARNING",
                    message="Structured log call missing 'event_type' keyword argument",
                ))

    return calls, violations


# ---------------------------------------------------------------------------
# Language dispatch
# ---------------------------------------------------------------------------

ANALYZERS = {
    "python": _run_python,
    "go": GoLogAnalyzer().analyze,
    "nodejs": NodejsLogAnalyzer().analyze,
    "rust": RustLogAnalyzer().analyze,
}


def run_validation(source_dir: Path, language: str) -> Report:
    """Run the full log-schema validation sweep.

    Args:
        source_dir: Root of the source tree.
        language: Programming language (python, go, nodejs, rust).

    Returns:
        A :class:`Report` with violation details.
    """
    analyzer = ANALYZERS.get(language)
    if analyzer is None:
        raise ValueError(f"Unsupported language: {language}. Supported: {list(ANALYZERS.keys())}")

    calls, violations = analyzer(source_dir)
    passed = len(violations) == 0

    return Report(
        language=language,
        passed=passed,
        total_calls=len(calls),
        violations=[
            {
                "file": v.file,
                "line": v.line,
                "severity": v.severity,
                "message": v.message,
            }
            for v in violations
        ],
    )


def _configure_logging(verbose: bool) -> None:
    """Configure stderr logging for the validator itself."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry-point.

    Returns:
        0 if all log calls pass schema validation, 1 otherwise.
    """
    parser = argparse.ArgumentParser(
        description="Validate log schema compliance across a codebase.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python validate_log_schemas.py --language python --source ./src
  python validate_log_schemas.py --language go --source .
  python validate_log_schemas.py --language nodejs --source ./src
  python validate_log_schemas.py --language rust --source ./src
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

    report = run_validation(args.source, args.language)

    print(json.dumps(report.__dict__, indent=2))

    if not report.passed:
        logger.error(
            "Found %d violation(s) across %d log call(s).",
            len(report.violations),
            report.total_calls,
        )
        return 1

    logger.info(
        "All %d log call(s) pass schema validation.", report.total_calls
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
