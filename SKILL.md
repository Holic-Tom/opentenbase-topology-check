---
name: opentenbase-topology-check
description: Inspect OpenTenBase node registration and runtime state, check coordinator connectivity, and compare a node list with an earlier snapshot. Use for read-only OpenTenBase cluster inspection.
---

# OpenTenBase 节点巡检

1. 从本 Skill 目录定位 `scripts/check_topology.py`。运行 Python 时使用脚本的完整路径，或先把工作目录切到本 Skill 目录。
2. 按请求选择模式。无需数据库的演示用 `--demo normal`，演示停止或未知用 `--demo stopped-node` 或 `--demo unknown-node`。真实环境用 `--live --status-config <已有配置文件>`，沿用当前终端配置的 libpq 连接参数。仅查询登记节点时使用 `--live`。参考 [README.md](README.md)。
3. 对比时加入 `--baseline <之前巡检的JSON文件>`。确认两份真实快照来自同一集群。用 `topology_changes` 汇报登记节点增减，用 `runtime_changes.changed` 汇报实例前后状态，并说明 `ambiguous_names` 中无法配对的同名实例。用 `--snapshot <当前JSON文件>` 可离线回放，标明历史数据。
4. 读取 JSON 后汇报数据来源 `source`、协调节点查询结果 `coordinator_query`、登记节点 `nodes` 和各运行实例 `runtime.nodes`。停止和未知分别解释。工具退出码 1 表示报告带告警，仍要读取输出。若没有 `runtime` 或状态查询报错，说明运行状态尚未取得。
