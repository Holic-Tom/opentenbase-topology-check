"""Check the OpenTenBase node catalog through a coordinator."""

import argparse
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path


TOPOLOGY_SQL = """
SELECT COALESCE(
    json_agg(
        json_build_object('name', node_name::text, 'type', node_type::text)
        ORDER BY node_type, node_name
    ),
    '[]'::json
)
FROM pg_catalog.pgxc_node
"""

ROLE_NAMES = {"C": "coordinator", "D": "data_node", "G": "gtm"}
RUNTIME_STATES = ("running", "stopped", "unknown")
RUNTIME_NODE = re.compile(r"^\s*Node\s+(\S+?)\([^\r\n]+\)\s+is\s+(Running|Stopped|Unknown)\s*$", re.M)
RUNTIME_TOTALS = re.compile(r"^\s*\[Result\]\s+Total:\s*(\d+),\s*Running:\s*(\d+),\s*Stopped:\s*(\d+),\s*Unknown:\s*(\d+)\s*$", re.M)


def validate_rows(rows):
    if not isinstance(rows, list) or any(
        not isinstance(row, dict)
        or not isinstance(row.get("name"), str)
        or not isinstance(row.get("type"), str)
        for row in rows
    ):
        raise ValueError("Unexpected topology output")
    return rows


def read_topology():
    env = os.environ.copy()
    env["PGCLIENTENCODING"] = "UTF8"
    command = [
        "psql", "-X", "-w", "-q", "-A", "-t",
        "-v", "ON_ERROR_STOP=1", "-c", TOPOLOGY_SQL,
    ]
    result = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=True, timeout=15, env=env,
    )
    return validate_rows(json.loads(result.stdout))


def summarize_runtime(nodes):
    counts = Counter(node["state"] for node in nodes)
    return {
        "status": "warning" if counts["stopped"] or counts["unknown"] else "ok",
        "state_counts": {state: counts[state] for state in RUNTIME_STATES},
        "nodes": nodes,
    }


def parse_runtime(output):
    nodes = [
        {"name": name, "state": state.lower()}
        for name, state in RUNTIME_NODE.findall(output)
    ]
    totals = RUNTIME_TOTALS.search(output)
    report = summarize_runtime(nodes)
    expected = (len(nodes), *report["state_counts"].values())
    if not nodes or totals is None or tuple(map(int, totals.groups())) != expected:
        raise ValueError("Incomplete or inconsistent opentenbase_ctl status output")
    return report


def read_runtime(config_path):
    config_path = config_path.resolve()
    if not config_path.is_file():
        return {"status": "error", "error": "status_config_not_found"}
    try:
        result = subprocess.run(
            ["opentenbase_ctl", "status", "-c", str(config_path)],
            cwd=str(config_path.parent), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=True, timeout=25,
        )
        return parse_runtime(result.stdout.decode("utf-8", errors="replace"))
    except FileNotFoundError:
        error = "status_tool_not_found"
    except subprocess.TimeoutExpired:
        error = "status_timeout"
    except (OSError, subprocess.CalledProcessError):
        error = "status_command_failed"
    except ValueError:
        error = "invalid_status_output"
    return {"status": "error", "error": error}


def compare_baseline(report, baseline_path):
    baseline = json.loads(baseline_path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(baseline, dict)
        or baseline.get("source") != report["source"]
        or baseline.get("status") not in ("ok", "warning")
    ):
        raise ValueError("Baseline must be a successful report from the same mode")

    before = {(node["name"], node["type"]) for node in validate_rows(baseline.get("nodes"))}
    after = {(node["name"], node["type"]) for node in report["nodes"]}
    report["topology_changes"] = {
        "added": [{"name": name, "type": code} for name, code in sorted(after - before)],
        "removed": [{"name": name, "type": code} for name, code in sorted(before - after)],
    }
    if before != after:
        report["status"] = "warning"


def summarize(rows, source):
    nodes = [
        {**row, "role": ROLE_NAMES.get(row["type"], "unknown")}
        for row in rows
    ]
    counts = Counter(node["role"] for node in nodes)
    missing = [role for role in ("coordinator", "data_node") if not counts[role]]
    return {
        "source": source,
        "status": "warning" if missing else "ok",
        "coordinator_query": "succeeded" if source == "live" else "not_run",
        "role_counts": {role: counts[role] for role in (*ROLE_NAMES.values(), "unknown")},
        "missing_catalog_roles": missing,
        "nodes": nodes,
    }


def demo_rows(scenario):
    rows = [{"name": "cn_demo", "type": "C"}, {"name": "gtm_demo", "type": "G"}]
    if scenario != "missing-data-node":
        rows.insert(1, {"name": "dn_demo", "type": "D"})
    return rows


def add_runtime(report, runtime):
    report["runtime"] = runtime
    if runtime["status"] == "error":
        report["status"] = "error"
    elif runtime["status"] == "warning" and report["status"] == "ok":
        report["status"] = "warning"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="Read the configured database.")
    mode.add_argument("--demo", choices=("normal", "missing-data-node", "stopped-node", "unknown-node"))
    parser.add_argument("--baseline", type=Path, help="Compare against an earlier JSON report.")
    parser.add_argument("--status-config", type=Path, help="OpenTenBase V5 config for the native status check.")
    args = parser.parse_args()
    if args.demo and args.status_config:
        parser.error("--status-config requires --live")

    if args.demo:
        report = summarize(demo_rows(args.demo), "demo")
    else:
        try:
            report = summarize(read_topology(), "live")
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            error_codes = {
                FileNotFoundError: "psql_not_found",
                subprocess.TimeoutExpired: "psql_timeout",
                subprocess.CalledProcessError: "psql_failed",
            }
            fallback = "invalid_output" if isinstance(error, ValueError) else "psql_launch_failed"
            code = error_codes.get(type(error), fallback)
            report = {"source": "live", "status": "error", "error": code}

    if args.baseline and report["status"] != "error":
        try:
            compare_baseline(report, args.baseline)
        except (OSError, ValueError):
            report.update(status="error", error="invalid_baseline")

    if args.demo:
        affected = {"stopped-node": "stopped", "unknown-node": "unknown"}
        runtime_nodes = [
            {"name": node["name"], "state": affected.get(args.demo, "running") if node["type"] == "D" else "running"}
            for node in report["nodes"]
        ]
        add_runtime(report, summarize_runtime(runtime_nodes))
    elif args.status_config:
        add_runtime(report, read_runtime(args.status_config))

    print(json.dumps(report, indent=2))
    return {"ok": 0, "warning": 1, "error": 3}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
