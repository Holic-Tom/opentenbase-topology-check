"""Check the OpenTenBase node catalog through a coordinator."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
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
RUNTIME_NODE = re.compile(r"^\s*Node\s+(\S+?)\(([^\r\n]+)\)\s+is\s+(Running|Stopped|Unknown)\s*$", re.M)
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
        {
            "name": name,
            "instance_id": hashlib.sha256(address.encode("utf-8")).hexdigest()[:16],
            "state": state.lower(),
        }
        for name, address, state in RUNTIME_NODE.findall(output)
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


def load_snapshot(path):
    snapshot = json.loads(path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("source") not in ("live", "demo")
        or snapshot.get("status") not in ("ok", "warning")
    ):
        raise ValueError("Expected a successful inspection snapshot")
    validate_rows(snapshot.get("nodes"))
    if snapshot.get("runtime") is not None:
        runtime_states(snapshot["runtime"])
    return snapshot


def runtime_states(runtime):
    if not isinstance(runtime, dict) or runtime.get("status") not in ("ok", "warning"):
        raise ValueError("Invalid runtime snapshot")
    nodes = runtime.get("nodes")
    if not isinstance(nodes, list) or any(
        not isinstance(node, dict)
        or not isinstance(node.get("name"), str)
        or node.get("state") not in RUNTIME_STATES
        or ("instance_id" in node and not isinstance(node["instance_id"], str))
        for node in nodes
    ):
        raise ValueError("Invalid runtime nodes")
    by_name = defaultdict(list)
    for node in nodes:
        by_name[node["name"]].append(node)
    for instances in by_name.values():
        ids = [node["instance_id"] for node in instances if "instance_id" in node]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate runtime instance identifier")
    return by_name


def compare_runtime(previous, current):
    before = runtime_states(previous)
    after = runtime_states(current)
    result = {"changed": [], "ambiguous_names": []}
    for name in sorted(before.keys() & after.keys()):
        old_nodes, new_nodes = before[name], after[name]
        if all("instance_id" in node for node in old_nodes + new_nodes):
            old_by_id = {node["instance_id"]: node for node in old_nodes}
            new_by_id = {node["instance_id"]: node for node in new_nodes}
            pairs = [(old_by_id[key], new_by_id[key]) for key in sorted(old_by_id.keys() & new_by_id.keys())]
        elif len(old_nodes) == len(new_nodes) == 1:
            pairs = [(old_nodes[0], new_nodes[0])]
        else:
            result["ambiguous_names"].append(name)
            continue

        for old, new in pairs:
            if old["state"] != new["state"]:
                change = {"name": name, "before": old["state"], "after": new["state"]}
                if "instance_id" in new:
                    change["instance_id"] = new["instance_id"]
                result["changed"].append(change)
    return result


def compare_baseline(report, baseline_path):
    baseline = load_snapshot(baseline_path)
    if baseline["source"] != report["source"]:
        raise ValueError("Baseline and current report use different sources")
    before = {(node["name"], node["type"]) for node in baseline["nodes"]}
    after = {(node["name"], node["type"]) for node in report["nodes"]}
    report["topology_changes"] = {
        "added": [{"name": name, "type": code} for name, code in sorted(after - before)],
        "removed": [{"name": name, "type": code} for name, code in sorted(before - after)],
    }
    runtime = report.get("runtime")
    runtime_delta = {"changed": [], "ambiguous_names": []}
    if baseline.get("runtime") and runtime and runtime["status"] != "error":
        runtime_delta = compare_runtime(baseline["runtime"], runtime)
        report["runtime_changes"] = runtime_delta
    if report["status"] == "ok" and (before != after or any(runtime_delta.values())):
        report["status"] = "warning"


def replay_snapshot(path):
    saved = load_snapshot(path)
    report = summarize(saved["nodes"], saved["source"])
    report["replayed"] = True
    if saved.get("runtime"):
        add_runtime(report, summarize_runtime(saved["runtime"]["nodes"]))
    return report


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
    mode.add_argument("--snapshot", type=Path, help="Read a saved inspection without contacting a database.")
    parser.add_argument("--baseline", type=Path, help="Compare against an earlier JSON report.")
    parser.add_argument("--status-config", type=Path, help="OpenTenBase V5 config for the native status check.")
    args = parser.parse_args()
    if not args.live and args.status_config:
        parser.error("--status-config requires --live")

    if args.demo:
        rows = demo_rows(args.demo)
        report = summarize(rows, "demo")
        affected = {"stopped-node": "stopped", "unknown-node": "unknown"}
        runtime_nodes = [
            {"name": node["name"], "state": affected.get(args.demo, "running") if node["type"] == "D" else "running"}
            for node in rows
        ]
        add_runtime(report, summarize_runtime(runtime_nodes))
    elif args.snapshot:
        try:
            report = replay_snapshot(args.snapshot)
        except (OSError, ValueError):
            report = {"source": "snapshot", "replayed": True, "status": "error", "error": "invalid_snapshot"}
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
        if args.status_config:
            add_runtime(report, read_runtime(args.status_config))

    if args.baseline and "nodes" in report:
        try:
            compare_baseline(report, args.baseline)
        except (OSError, ValueError):
            report.update(status="error", error="invalid_baseline")

    print(json.dumps(report, indent=2))
    return {"ok": 0, "warning": 1, "error": 3}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
