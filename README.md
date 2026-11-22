# OpenTenBase 节点巡检 Skill

一次巡检可以通过 `psql` 读取系统目录 `pgxc_node`、通过 `opentenbase_ctl status` 查询实际运行状态，并与上次的 JSON 快照比较登记节点。Agent 读取统一的 JSON 后生成摘要。一个节点即使已停止，也可能仍登记在数据库中。

## 本地演示

在本目录打开终端。命令行工具已在 Python 3.6 和 3.10 上运行，测试需 Python 3.10 或以上版本。无需安装 Python 第三方包。

```powershell
python scripts/check_topology.py --demo normal
python scripts/check_topology.py --demo missing-data-node
python scripts/check_topology.py --demo stopped-node
python scripts/check_topology.py --demo unknown-node
python -B -m unittest discover -s tests -v
```

模拟正常场景包括协调节点、数据节点和全局事务管理节点。其他场景分别演示数据节点从名单里消失、仍在名单里但已停止、以及状态未知。演示输出统一标记 `"source": "demo"`。实际执行记录见 [DEMO.md](DEMO.md)。

把第一份报告保存为快照，再运行第二次巡检，即可让脚本列出消失的节点。

```powershell
$baseline = Join-Path ([IO.Path]::GetTempPath()) ('otb-' + [guid]::NewGuid() + '.json')
python scripts/check_topology.py --demo normal | Set-Content -Encoding UTF8 -LiteralPath $baseline
python scripts/check_topology.py --demo missing-data-node --baseline $baseline
```

真实环境用 `--live` 生成和对比快照，须使用同一集群。比较使用节点名和类型，角色变化会显示为旧条目移除、新条目新增。

## 连接 OpenTenBase

将数据库客户端 `psql` 加入 PATH，准备有权限读取节点目录的实验账号。在 PowerShell 中填写实验环境的实际连接信息，以下使用示例地址和账号。

```powershell
$env:PGHOST = '127.0.0.1'
$env:PGPORT = '15001'
$env:PGDATABASE = 'lab'
$env:PGUSER = 'student'
python scripts/check_topology.py --live
```

需要密码时，通过本机 libpq 密码文件配置。Windows 的默认位置是 `%APPDATA%\postgresql\pgpass.conf`。脚本也支持读取终端通过 `PGSERVICE` 指定的服务配置。

在 OpenTenBase V5 的运维机上，把 `opentenbase_ctl` 加入 PATH，并配置它所需的运行库。用已有的集群配置文件发起完整巡检。数据库连接和运维工具配置应指向同一集群。

```bash
python3 scripts/check_topology.py --live --status-config /path/to/opentenbase_config.ini
```

脚本从配置所在目录运行固定的 `opentenbase_ctl status -c`，并把每个运行实例的状态写到 `runtime.nodes`。有主备配置时，同名节点可能显示多条运行实例。真实快照对比可以同时传入 `--baseline`，比较的是登记节点。

## 在 Agent 中调用

技能包采用 [Agent Skills 规范](https://agentskills.io/specification)。如果你的客户端支持这一规范并允许执行本地命令，可按该客户端的说明导入整个 `opentenbase-topology-check` 文件夹。保留 [SKILL.md](SKILL.md) 和 `scripts/` 的相对位置。

导入后可以输入「请用 OpenTenBase 节点巡检 Skill 模拟巡检，演示一个仍在名单里但已停机的数据节点」。预期调用 `python scripts/check_topology.py --demo stopped-node`，并根据 JSON 报告 `dn_demo` 仍在登记名单中，模拟状态为已停止。真实巡检时，在 Agent 的执行环境中配置数据库连接和运维工具，并提供已有集群配置文件的路径。

如果手头的工具只支持聊天，在终端运行上述 Python 命令，再把输出贴给它解读即可。客户端安装验证及自然语言调用的复验状态见 [DEMO.md](DEMO.md)。

### 在 Codex 中调用

在解压后含有 `opentenbase-topology-check` 文件夹的目录打开 PowerShell。把整个 Skill 文件夹复制到新演示项目的 `.agents/skills/` 后启动 Codex。

```powershell
$project = Join-Path (Get-Location) 'otb-agent-demo'
$skills = Join-Path $project '.agents/skills'
New-Item -ItemType Directory -Path $skills -Force | Out-Null
Copy-Item -LiteralPath './opentenbase-topology-check' -Destination $skills -Recurse
Set-Location -LiteralPath $project
codex
```

在 Codex 中输入上面的模拟巡检请求，也可以用 `$opentenbase-topology-check` 明确指定这个 Skill。

[Codex Skill 安装说明](https://learn.chatgpt.com/docs/build-skills)

## 结果解读

| 退出码 | 含义 |
| --- | --- |
| 0 | 已请求的检查均完成，未发现告警 |
| 1 | 登记中缺少协调节点或数据节点、检测到节点变更，或存在停止或状态未知的实例 |
| 2 | 命令行参数使用错误 |
| 3 | 查询无法完成或结果格式异常，详见 JSON 中的 `error` |

`coordinator_query: succeeded` 说明本次连接的协调节点能完成查询。检查局部失败时，已取得的结果仍会保留。例如原生工具未安装时，JSON 仍可包含登记节点，并在 `runtime.error` 标明 `status_tool_not_found`。仅运行 `--live` 时，报告只包含登记信息和协调节点查询结果。

常见错误包括 `psql_not_found`（检查客户端和 PATH）、`psql_timeout`（检查网络、端口和服务器响应）、`psql_failed`（检查连接参数、身份验证和目录权限）和 `invalid_output`（检查客户端输出及 OpenTenBase 版本）。

官方资料：[OpenTenBase 节点目录示例](https://docs.opentenbase.org/en/guide/05-component/) · [OpenTenBase V5 快速入门](https://docs.opentenbase.org/guide/01-quickstart/) · [libpq 密码文件](https://www.postgresql.org/docs/current/libpq-pgpass.html)
