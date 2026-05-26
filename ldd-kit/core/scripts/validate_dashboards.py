#!/usr/bin/env python3
"""
Grafana Dashboard JSON Validator

Validates Grafana dashboard JSON files in a directory:
- Verifies valid JSON structure
- Verifies dashboard.uid exists and is unique
- Verifies dashboard.title exists
- Verifies each panel has: title, type, targets (with expr for prometheus)
- Verifies datasource references use "prometheus" or variable
- Verifies no duplicate panel IDs within a dashboard

Usage:
    python scripts/validate_dashboards.py --dir telemetry/dashboards/

Exit codes:
    0 - all dashboards valid
    1 - one or more validation issues found
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate Grafana dashboard JSON files"
    )
    parser.add_argument(
        "--dir",
        type=str,
        required=True,
        help="Directory containing dashboard JSON files to validate"
    )
    return parser.parse_args()


def validate_panel(panel: dict, dashboard_uid: str) -> list[str]:
    """Validate a single dashboard panel. Returns list of error messages."""
    errors = []
    panel_id = panel.get("id", "unknown")
    panel_title = panel.get("title", "untitled")

    # Check panel title
    if not panel.get("title"):
        errors.append(f"[{dashboard_uid}] Panel {panel_id}: missing 'title'")

    # Check panel type
    if not panel.get("type"):
        errors.append(f"[{dashboard_uid}] Panel '{panel_title}' (id={panel_id}): missing 'type'")

    # Check targets exist
    targets = panel.get("targets")
    if not targets:
        errors.append(f"[{dashboard_uid}] Panel '{panel_title}' (id={panel_id}): missing 'targets'")
    else:
        # Check each target has expr
        for idx, target in enumerate(targets):
            if not isinstance(target, dict):
                errors.append(
                    f"[{dashboard_uid}] Panel '{panel_title}' (id={panel_id}): "
                    f"target[{idx}] is not an object"
                )
                continue
            if not target.get("expr"):
                errors.append(
                    f"[{dashboard_uid}] Panel '{panel_title}' (id={panel_id}): "
                    f"target[{idx}] missing 'expr'"
                )

    # Check datasource references
    ds = panel.get("datasource")
    if ds and isinstance(ds, dict):
        ds_type = ds.get("type", "")
        ds_uid = ds.get("uid", "")
        valid_ds = (
            ds_type == "prometheus"
            or (isinstance(ds_uid, str) and ds_uid.startswith("${"))
        )
        if not valid_ds:
            errors.append(
                f"[{dashboard_uid}] Panel '{panel_title}' (id={panel_id}): "
                f"datasource uid '{ds_uid}' is not 'prometheus' or a variable"
            )
    elif ds and isinstance(ds, str):
        if ds not in ("prometheus", "${datasource}") and not ds.startswith("$"):
            errors.append(
                f"[{dashboard_uid}] Panel '{panel_title}' (id={panel_id}): "
                f"datasource '{ds}' should reference 'prometheus' or a variable"
            )

    # Also check target-level datasources
    if targets:
        for idx, target in enumerate(targets):
            if isinstance(target, dict) and "datasource" in target:
                tds = target["datasource"]
                if isinstance(tds, dict):
                    tds_uid = tds.get("uid", "")
                    tds_type = tds.get("type", "")
                    if tds_type != "prometheus" and not (
                        isinstance(tds_uid, str) and tds_uid.startswith("${")
                    ):
                        errors.append(
                            f"[{dashboard_uid}] Panel '{panel_title}' (id={panel_id}): "
                            f"target[{idx}] datasource should be 'prometheus' or a variable"
                        )

    return errors


def validate_dashboard(filepath: Path, seen_uids: dict[str, str]) -> dict[str, Any]:
    """Validate a single dashboard JSON file. Returns result dict."""
    result = {
        "file": str(filepath.name),
        "valid": False,
        "errors": [],
        "warnings": [],
        "info": {
            "uid": None,
            "title": None,
            "panel_count": 0,
        },
    }

    # Parse JSON
    try:
        content = filepath.read_text(encoding="utf-8")
        data = json.loads(content)
    except json.JSONDecodeError as e:
        result["errors"].append(f"Invalid JSON: {e}")
        return result
    except Exception as e:
        result["errors"].append(f"Failed to read file: {e}")
        return result

    # Extract dashboard object
    dashboard = data.get("dashboard", data)
    if not dashboard:
        result["errors"].append("Missing 'dashboard' key")
        return result

    # Check uid
    uid = dashboard.get("uid")
    if not uid:
        result["errors"].append("Missing 'dashboard.uid'")
    else:
        result["info"]["uid"] = uid
        if uid in seen_uids:
            result["errors"].append(
                f"Duplicate dashboard uid '{uid}' (also in {seen_uids[uid]})"
            )
        else:
            seen_uids[uid] = filepath.name

    # Check title
    title = dashboard.get("title")
    if not title:
        result["errors"].append("Missing 'dashboard.title'")
    else:
        result["info"]["title"] = title

    # Check schemaVersion
    schema_version = dashboard.get("schemaVersion")
    if schema_version is None:
        result["warnings"].append("Missing 'schemaVersion' - may be an old format")
    elif schema_version < 30:
        result["warnings"].append(
            f"schemaVersion {schema_version} is very old; Grafana 10+ recommends 36+"
        )

    # Validate panels
    panels = dashboard.get("panels", [])
    result["info"]["panel_count"] = len(panels)

    if not panels:
        result["errors"].append("Dashboard has no panels")

    seen_panel_ids: set[int] = set()
    for panel in panels:
        if not isinstance(panel, dict):
            result["errors"].append(f"Panel is not an object: {type(panel).__name__}")
            continue

        panel_id = panel.get("id")
        if panel_id is not None:
            if panel_id in seen_panel_ids:
                result["errors"].append(
                    f"Duplicate panel ID {panel_id} in dashboard"
                )
            seen_panel_ids.add(panel_id)

        panel_errors = validate_panel(panel, uid or "unknown")
        result["errors"].extend(panel_errors)

    result["valid"] = len(result["errors"]) == 0
    return result


def main() -> int:
    args = parse_args()
    dashboards_dir = Path(args.dir)

    if not dashboards_dir.is_dir():
        print(f"Error: Not a directory: {dashboards_dir}", file=sys.stderr)
        sys.exit(1)

    # Find all JSON files
    json_files = sorted(dashboards_dir.glob("*.json"))
    if not json_files:
        print(f"No .json files found in {dashboards_dir}", file=sys.stderr)
        sys.exit(1)

    seen_uids: dict[str, str] = {}
    results: list[dict[str, Any]] = []
    total_errors = 0
    total_warnings = 0
    total_panels = 0

    for filepath in json_files:
        result = validate_dashboard(filepath, seen_uids)
        results.append(result)
        if not result["valid"]:
            total_errors += len(result["errors"])
        total_warnings += len(result["warnings"])
        total_panels += result["info"]["panel_count"]

    # Build report
    report = {
        "valid": all(r["valid"] for r in results) and total_errors == 0,
        "summary": {
            "files_checked": len(json_files),
            "files_valid": sum(1 for r in results if r["valid"]),
            "files_invalid": sum(1 for r in results if not r["valid"]),
            "total_panels": total_panels,
            "total_errors": total_errors,
            "total_warnings": total_warnings,
        },
        "dashboards": results,
    }

    # Output JSON report to stdout
    print(json.dumps(report, indent=2))

    # Also print human-readable summary to stderr if there are issues
    if total_errors > 0:
        print("\n--- Validation Errors ---", file=sys.stderr)
        for result in results:
            if not result["valid"]:
                print(f"\n[FAIL] {result['file']}", file=sys.stderr)
                if result["info"]["uid"]:
                    print(f"  uid:    {result['info']['uid']}", file=sys.stderr)
                if result["info"]["title"]:
                    print(f"  title:  {result['info']['title']}", file=sys.stderr)
                for err in result["errors"]:
                    print(f"  ERROR: {err}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
