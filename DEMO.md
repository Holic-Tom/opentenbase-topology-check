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

## 独立集群的停机与恢复

在同一课程虚拟机上单独部署 OpenTenBase v5.21.8，使用测试账号和专用数据目录。只操作新集群的 `dn0001`。每个阶段都重新从协调节点读取节点目录，并调用原生运维工具检查运行状态。

| 阶段 | 登记节点数 | 运行 / 停止 / 未知 | 检测到的运行状态变化 | 退出码 |
| --- | --- | --- | --- | --- |
| [运行](examples/01-running.json) | 4 | 4 / 0 / 0 | 首次采集 | 0 |
| [停止](examples/02-stopped.json) | 4 | 3 / 1 / 0 | `dn0001: running → stopped` | 1 |
| [恢复](examples/03-recovered.json) | 4 | 4 / 0 / 0 | `dn0001: stopped → running` | 1 |

两次对比的登记新增、移除列表均为空。恢复阶段的退出码 1 提示检测到一次状态变化，此时四个实例均已运行。快照的实例标识已替换为一致的 `lab-*` 代号，保留实际观测到的节点名、状态、计数及前后变化。离线复现见 [README.md](README.md#离线回放真实快照)。实验结束后已停止测试实例，并清理实验账号、凭据和数据目录。

## Agent 自然语言调用

在安装了项目 Skill 的新 Claude Code 2.1.220 会话中，用自然语言要求模拟一个仍在名单里但已停机的数据节点。会话记录显示客户端主动选择了 `opentenbase-topology-check`，执行了 `python scripts/check_topology.py --demo stopped-node`。工具返回退出码 1 和 JSON。Agent 根据实际输出解释，`dn_demo` 仍登记为数据节点，状态为停止，其余两个实例均在运行。

[调用实录回放（GIF）](examples/agent-session.gif)按测试事件顺序展示提示、Skill 选择、脚本执行和回答节选。回放缩短了脚本路径，并省去了临时目录与会话标识。

### Codex 安装与模拟巡检

将整个 Skill 复制到临时演示项目的 `.agents/skills/opentenbase-topology-check/`。通过 Codex 的本地技能列表核对，客户端发现了该 Skill，作用域为当前项目，状态为已启用。

在当前协作会话中，按以下自然语言请求演练 Skill 的读取与调用流程。

> 模拟巡检 OpenTenBase，演示一个仍在名单里但已停机的数据节点。

读取已安装副本的 `SKILL.md` 后，实际调用该副本的 `scripts/check_topology.py --demo stopped-node`。报告显示登记中的 `dn_demo` 仍是数据节点，模拟进程状态为 `stopped`，其余两个实例为 `running`。工具退出码为 1，表示巡检完成且发现告警。

另起的 Codex CLI 会话在请求模型时超时。该客户端的自然语言触发仍待复验。

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

`python -B -m unittest discover -s tests -v` 运行 16 项测试，全部通过。覆盖登记节点增减、运行状态变化、同名实例配对、旧格式快照和真实样本离线回放。外部工具的异常和输出不完整用替身验证。
