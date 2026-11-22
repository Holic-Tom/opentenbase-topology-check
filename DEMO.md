# 演示记录

## 真实课程集群

2026 年 11 月 22 日复用课程的 OpenCloudOS 8.10 虚拟机。管理工具报告版本为 `v5.21.8`，运行脚本使用虚拟机自带的 Python 3.6，通过本机套接字连接 CN。

| 检查 | 实际观测 |
| --- | --- |
| `--live --status-config opentenbase_config.ini` | 退出码 0，`coordinator_query=succeeded`。查到 `cn0001`、`dn0001`、`dn0002`、`gtm0001`。同一次执行调用了原生状态命令，4 个运行实例均为 `running`。 |
| 保存第一次 JSON，并用相同配置重跑、传入 `--baseline <快照>` | 退出码 0，`topology_changes.added=[]`，`topology_changes.removed=[]`。再次得到 4 个运行实例。 |

第二次真实巡检的 JSON 节选

```json
{
  "source": "live",
  "status": "ok",
  "coordinator_query": "succeeded",
  "role_counts": {"coordinator": 1, "data_node": 2, "gtm": 1, "unknown": 0},
  "missing_catalog_roles": [],
  "topology_changes": {"added": [], "removed": []},
  "runtime": {
    "status": "ok",
    "state_counts": {"running": 4, "stopped": 0, "unknown": 0}
  }
}
```

课堂依据为《OpenTenBase 课程4 集群部署与运维》第 25、26 页，分别演示了使用 `opentenbase_ctl status` 查看进程状态，以及使用 `psql` 查询 `pgxc_node` 查看路由信息。

## Codex 安装与模拟巡检

将整个 Skill 复制到临时演示项目的 `.agents/skills/opentenbase-topology-check/`。通过 Codex 的本地技能列表核对，客户端发现了该 Skill，作用域为当前项目，状态为已启用。

在当前协作会话中，按以下自然语言请求演练 Skill 的读取与调用流程。

> 模拟巡检 OpenTenBase，演示一个仍在名单里但已停机的数据节点。

读取已安装副本的 `SKILL.md` 后，实际调用该副本的 `scripts/check_topology.py --demo stopped-node`。报告显示登记中的 `dn_demo` 仍是数据节点，模拟进程状态为 `stopped`，其余两个实例为 `running`。工具退出码为 1，表示巡检完成且发现告警。

另起的 Codex CLI 会话在请求模型时超时。独立会话中的自然语言触发仍待复验。

## 模拟演示和测试

执行环境为 Windows PowerShell 与 Python 3.10.1。模拟节点移除用于演示变更提示。

| 场景 | 实际观测 | 退出码 |
| --- | --- | --- |
| `python scripts/check_topology.py --demo normal` | `source=demo`，`status=ok`，协调节点、数据节点和全局事务管理节点各 1 个，`coordinator_query=not_run` | 0 |
| `python scripts/check_topology.py --demo missing-data-node` | `source=demo`，`status=warning`，缺失 `data_node`，`coordinator_query=not_run` | 1 |
| `python scripts/check_topology.py --demo missing-data-node --baseline $baseline` | 与 `normal` 快照对比，`topology_changes.removed` 为 `[{"name":"dn_demo","type":"D"}]`，`added` 为空 | 1 |
| `python scripts/check_topology.py --demo stopped-node` | `dn_demo` 仍在登记名单里，原生状态的模拟结果为 `stopped` | 1 |
| `python scripts/check_topology.py --demo unknown-node` | `dn_demo` 仍在登记名单里，原生状态的模拟结果为 `unknown` | 1 |
| `python scripts/check_topology.py --live` | `source=live`，`status=error`，`error=psql_not_found` | 3 |

`python -B -m unittest discover -s tests -v` 运行 12 项测试，全部通过。测试覆盖登记节点增减、停止和状态未知；同时用替身验证了客户端调用、原生输出解析、报告不完整和错误处理。
