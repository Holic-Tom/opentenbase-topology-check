"""Test the CLI contract and the psql integration boundary."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_topology.py"
SPEC = importlib.util.spec_from_file_location("check_topology", SCRIPT)
check_topology = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_topology)

STATUS_OUTPUT = """ ------------ Node status ------------
Node cn0001(192.0.2.1:11003) is Running
Node dn0001(192.0.2.1:11006) is Stopped
Node dn0001([2001:db8::1]:11006) is Unknown
[Result] Total: 3, Running: 1, Stopped: 1, Unknown: 1
"""


class TopologyCheckTest(unittest.TestCase):
    def run_demo(self, scenario, *options):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--demo", scenario, *options],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode, json.loads(result.stdout)

    def run_live(self, **psql_result):
        output = io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), "--live"]),
            patch.object(subprocess, "run", **psql_result) as psql,
            contextlib.redirect_stdout(output),
        ):
            exit_code = check_topology.main()
        return exit_code, json.loads(output.getvalue()), psql

    def test_normal_demo(self):
        exit_code, result = self.run_demo("normal")
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["source"], "demo")
        self.assertEqual(result["coordinator_query"], "not_run")
        self.assertEqual(result["role_counts"]["data_node"], 1)

    def test_missing_data_node_demo(self):
        exit_code, result = self.run_demo("missing-data-node")
        self.assertEqual(exit_code, 1)
        self.assertEqual(result["missing_catalog_roles"], ["data_node"])

    def test_baseline_comparison(self):
        _, normal = self.run_demo("normal")
        _, missing = self.run_demo("missing-data-node")
        cases = [
            (normal, "normal", 0, [], []),
            (normal, "missing-data-node", 1, [], [{"name": "dn_demo", "type": "D"}]),
            (missing, "normal", 1, [{"name": "dn_demo", "type": "D"}], []),
        ]
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.json"
            for report, scenario, status, added, removed in cases:
                with self.subTest(scenario=scenario, status=status):
                    baseline.write_text(json.dumps(report), encoding="utf-8-sig")
                    exit_code, result = self.run_demo(scenario, "--baseline", str(baseline))
                    self.assertEqual(exit_code, status)
                    self.assertEqual(result["topology_changes"], {"added": added, "removed": removed})

    def test_live_baseline_cannot_be_used_in_demo(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.json"
            baseline.write_text('{"source":"live","status":"ok","nodes":[]}', encoding="utf-8")
            exit_code, result = self.run_demo("normal", "--baseline", str(baseline))
            self.assertEqual(exit_code, 3)
            self.assertEqual(result["error"], "invalid_baseline")

    def test_live_query_success(self):
        rows = [{"name": "协调节点", "type": "C"}, {"name": "dn01", "type": "D"}]
        response = subprocess.CompletedProcess([], 0, json.dumps(rows, ensure_ascii=False).encode())
        exit_code, result, psql = self.run_live(return_value=response)
        self.assertEqual(exit_code, 0)
        self.assertEqual(result["coordinator_query"], "succeeded")
        self.assertEqual(result["nodes"][0]["name"], "协调节点")
        command = psql.call_args.args[0]
        self.assertEqual(command[0], "psql")
        self.assertIn("-w", command)
        self.assertIn("FROM pg_catalog.pgxc_node", command[-1])

    def test_live_error_codes(self):
        cases = [
            (FileNotFoundError(), "psql_not_found"),
            (subprocess.TimeoutExpired("psql", 15), "psql_timeout"),
            (subprocess.CalledProcessError(2, "psql", stderr=b"private details"), "psql_failed"),
            (OSError(), "psql_launch_failed"),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected):
                exit_code, result, _ = self.run_live(side_effect=error)
                self.assertEqual(exit_code, 3)
                self.assertEqual(result, {"source": "live", "status": "error", "error": expected})

    def test_live_response_schema(self):
        for output in (b"malformed", b"{}", b'[{"name":"cn01"}]'):
            with self.subTest(output=output):
                response = subprocess.CompletedProcess([], 0, output)
                exit_code, result, _ = self.run_live(return_value=response)
                self.assertEqual(exit_code, 3)
                self.assertEqual(result["error"], "invalid_output")

    def test_runtime_demo_keeps_node_registered(self):
        for state in ("stopped", "unknown"):
            with self.subTest(state=state):
                exit_code, result = self.run_demo(state + "-node")
                self.assertEqual(exit_code, 1)
                self.assertEqual(result["missing_catalog_roles"], [])
                self.assertEqual(result["role_counts"]["data_node"], 1)
                self.assertEqual(result["runtime"]["state_counts"][state], 1)
                self.assertIn({"name": "dn_demo", "state": state}, result["runtime"]["nodes"])

    def test_runtime_parser_keeps_distinct_instances(self):
        report = check_topology.parse_runtime(STATUS_OUTPUT)
        self.assertEqual(report["state_counts"], {"running": 1, "stopped": 1, "unknown": 1})
        self.assertEqual(report["nodes"][1:], [
            {"name": "dn0001", "state": "stopped"},
            {"name": "dn0001", "state": "unknown"},
        ])

    def test_runtime_parser_rejects_partial_output(self):
        for output in ("", STATUS_OUTPUT.replace("Total: 3", "Total: 4")):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    check_topology.parse_runtime(output)

    def test_live_runtime_invokes_read_only_status_subcommand(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory, "cluster config.ini").resolve()
            config.touch()
            output = io.StringIO()
            processes = [
                subprocess.CompletedProcess([], 0, b'[{"name":"cn0001","type":"C"},{"name":"dn0001","type":"D"}]'),
                subprocess.CompletedProcess([], 0, STATUS_OUTPUT.encode()),
            ]
            with (
                patch.object(sys, "argv", [str(SCRIPT), "--live", "--status-config", str(config)]),
                patch.object(subprocess, "run", side_effect=processes) as run,
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(check_topology.main(), 1)
            report = json.loads(output.getvalue())
            self.assertEqual(report["coordinator_query"], "succeeded")
            self.assertEqual(report["runtime"]["status"], "warning")
            command = run.call_args
            self.assertEqual(command.args[0], ["opentenbase_ctl", "status", "-c", str(config)])
            self.assertEqual(command.kwargs["cwd"], str(config.parent))

    def test_runtime_failure_keeps_catalog_result(self):
        report = check_topology.summarize([{"name": "cn01", "type": "C"}], "live")
        with tempfile.TemporaryDirectory() as directory:
            runtime = check_topology.read_runtime(Path(directory, "missing.ini"))
        check_topology.add_runtime(report, runtime)
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["coordinator_query"], "succeeded")
        self.assertEqual(report["runtime"]["error"], "status_config_not_found")


if __name__ == "__main__":
    unittest.main()
