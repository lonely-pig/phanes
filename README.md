# Phanes P0 — Body Migration Proof of Concept

Phanes 是一个可迁移的持久 Agent 原型（Python 3.11+，纯标准库，无第三方依赖）。

P0 证明一个核心假设：**一个 Agent 的持久 Identity 与 Memory 可以独立于具体 Body 保存、搬运和恢复；恢复后的同一份 Self 可以在新的授权 Body 上继续利用迁移前的事实执行任务。**

P0 已验证：

- **Identity persistence** — `agent_id` 跨进程、跨环境保持不变；
- **Memory persistence** — 地点事实（如 `Alpha = (10, 20)`）随迁移完整恢复；
- **Body replacement** — 同一份 Core 在 Environment B 绑定全新 Body（`mock-uav-B`）后正常工作；
- **Authority non-persistence** — A 的授权绝不随包迁移；B 必须显式重新授权才能行动。

这不是完整的机器人智能系统：没有 LLM、没有规划器、没有真实硬件。

## P0 Scope

**P0 HAS**：Identity / Memory 持久化、Body abstraction（Protocol + Descriptor）、受控 CapabilityRegistry、显式 Body Discovery、MockUAV 测试身体、确定性 Core（三种固定中文命令语法）、CLI、带 SHA-256 校验的迁移包、独立 A/B 环境隔离验收。

**P0 DOES NOT HAVE**：LLM、语音、ROS、PX4、真实无人机、自主规划、云同步、语义记忆、动态插件系统、网络发现、自我传播、身份防伪/防克隆。

## Architecture Overview

```text
Self（可迁移）                Runtime（通用代码，随包迁移）      Host（宿主提供，不迁移）
┌──────────────────┐         ┌────────────────────────┐        ┌──────────────────────┐
│ Identity          │         │ PhanesCore              │        │ BodyAdapter (MockUAV) │
│  agent_id/name/   │◄────────│  固定语法 → 记忆操作/     │───────►│ host config           │
│  created_at       │         │  能力调用                 │ inject │ allowed_capabilities  │
│ Memory            │         │ CapabilityRegistry       │        │ （显式授权，默认空）    │
│  places: name→(x,y)│        │ Discovery / Migration    │        │ Body 运行时状态        │
└──────────────────┘         └────────────────────────┘        └──────────────────────┘
```

**Body 和 authority 不属于 Self。** 迁移包只携带 Self 数据 + 通用运行时源码白名单。

## Core Principles

- **Identity continuity ≠ authority continuity** — 身份连续不代表权限连续；
- **Capability existence ≠ authority** — Adapter 实现了某能力，不等于它被授权；
- **Default authority = zero** — 缺省授权为空，无通配符；
- **认知可迁移，权限不自动迁移** — 授权只来自目标宿主的显式配置；
- **Core 不依赖具体 Body** — Core 不导入 discovery/MockUAV/`phanes_host`，一切动作经 `lookup → registry.list → registry.invoke`。

## Quick Start

前置：Python 3.11+（本仓库在 Windows / Python 3.13.7 上验证）。在仓库根目录执行：

```powershell
python -m unittest discover -s tests -v   # 143 tests
```

```powershell
python -m phanes init --state .\demo\state
python -m phanes run --state .\demo\state --host-config .\examples\host.mock-uav.json
```

进入交互后输入固定语法（中英文括号/逗号均可，末尾句号可选）：

```text
记住 Alpha 是坐标 (10, 20)。     → 已保存 Alpha = (10, 20)
查询 Alpha。                     → Alpha = (10, 20)
去 Alpha。                       → [MockUAV] moving to (10, 20) / 已到达 (10, 20)
退出                             （EOF 同样正常退出）
```

无 `--host-config` 时 `body: none`，记忆命令可用，`去 Alpha。` 返回 `NO_BODY`。

## P0 Migration Demo（核心演示）

以下 PowerShell 流程在本仓库当前代码上实测通过。注意两个 Windows 细节：PowerShell 5.1 的 `Out-File -Encoding utf8` 会写入 BOM（Phanes 严格拒绝），请用 `[IO.File]::WriteAllText` 写 JSON；管道 stdin 需 `$OutputEncoding = [Text.UTF8Encoding]::new($false)` 去掉 BOM。

```powershell
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.UTF8Encoding]::new($false)

$repo = "<本仓库路径>"
$demo = Join-Path $env:LOCALAPPDATA "Temp\phanes_demo"

# --- Environment A：记住 Alpha，用 Body A 移动，然后导出 ---
Set-Location $repo
python -m phanes init --state $demo\env_a\state
[IO.File]::WriteAllText("$demo\env_a\host.json", '{ "schema_version": 1, "body": {"adapter": "mock_uav", "body_id": "mock-uav-A"}, "allowed_capabilities": ["get_position", "move_to"] }')
"记住 Alpha 是坐标 (10, 20)。", "查询 Alpha。", "去 Alpha。", "退出" |
  python -m phanes run --state $demo\env_a\state --host-config $demo\env_a\host.json
python -m phanes export --state $demo\env_a\state --out $demo\env_a\transfer

# --- 人工复制迁移包到 B（系统无传输功能，由你搬运） ---
Copy-Item -Recurse $demo\env_a\transfer $demo\env_b\transfer

# --- Environment B：预置可信导入工具与宿主包（独立于 A/仓库） ---
Copy-Item -Recurse $repo\phanes $demo\env_b\tool\phanes
Copy-Item -Recurse $repo\phanes_host $demo\env_b\host\phanes_host

# --- B 用自己的工具校验并导入（只恢复 Self） ---
Set-Location $demo\env_b\tool
python -m phanes import --package $demo\env_b\transfer --state $demo\env_b\state

# --- 停止 A（演示时先停 A 再启 B；P0 不防克隆） ---
Move-Item $demo\env_a $demo\env_a_gone

# --- B 无授权：Memory 已在，但去 Alpha 必须失败为 NO_BODY ---
Set-Location $demo\env_b\transfer\runtime
$env:PYTHONPATH = "$demo\env_b\host"
"查询 Alpha。", "去 Alpha。", "退出" |
  python -m phanes run --state $demo\env_b\state
# 预期：Alpha = (10, 20)  +  NO_BODY: no body is bound

# --- B 显式提供新 Body 与新授权，然后移动 ---
[IO.File]::WriteAllText("$demo\env_b\host.json", '{ "schema_version": 1, "body": {"adapter": "mock_uav", "body_id": "mock-uav-B"}, "allowed_capabilities": ["get_position", "move_to"] }')
"去 Alpha。", "退出" |
  python -m phanes run --state $demo\env_b\state --host-config $demo\env_b\host.json
# 预期：[MockUAV] moving to (10, 20)

# --- 证据：B 实际加载的是迁移包内 runtime ---
python -c "import phanes; print(phanes.__file__)"
# 预期指向 $demo\env_b\transfer\runtime\phanes\__init__.py
```

## Security Model

- 无网络扫描、无设备自动搜索、无自动传播、无远程部署；
- 无提权、无动态插件、无 `eval`/`exec`、无任意模块加载；
- Discovery 只认命令行显式传入的本地 JSON，固定工厂表，不回退；
- 迁移包不含任何 authority（host config、授权列表、凭据、Adapter 实现）；
- Import 先全量校验（清单、SHA-256、版本、路径安全、symlink、agent_id 一致性）再提交；包内代码在验证完成前只是数据，**包不能验证自己**（B 使用预置的可信工具）；
- SHA-256 只证明完整性，不证明来源真实性；UUID 不是身份认证；复制包会产生同一身份的两个副本，P0 不提供防克隆保证。

## Repository Map

```text
phanes/               # 通用运行时（迁移白名单覆盖这些源码）
  __init__.py         # core version
  __main__.py         # CLI + composition root（init/run/export/import）
  contracts.py        # 数据结构、错误码、固定能力契约校验、BodyAdapter Protocol
  core.py             # 确定性命令映射（三种固定语法）
  identity.py         # Identity 初始化/严格加载 + 原子 JSON 写入
  memory.py           # 地点事实持久化 MemoryStore
  capabilities.py     # CapabilityRegistry（默认零权限、单次绑定）
  discovery.py        # 显式配置 + 固定工厂表（延迟导入宿主包）
  migration.py        # 迁移包导出/校验/导入
phanes_host/          # 宿主独立提供；不迁移
  mock_uav.py         # MockUAVAdapter（get_position / move_to）
examples/
  host.mock-uav.json  # 显式授权示例（不自动加载）
tests/                # unittest；见下表
docs/                 # Architecture Freeze 与最终状态
```

## Test Matrix

统一入口：`python -m unittest discover -s tests -v`（143 tests，全部 PASS）。

| AT | 验证目标 | 主要测试位置 | 状态 |
|---|---|---|---|
| AT01 | 初始化与重启；重复 init 拒绝 | tests/test_state.py | PASS |
| AT02 | Memory 持久化与同名更新 | tests/test_state.py | PASS |
| AT03 | 跨环境恢复，Identity/Memory 一致 | tests/test_migration.py, tests/test_ab_migration.py | PASS |
| AT04 | 完整闭环：精确 MockUAV 日志 + get_position=(10,20) | tests/test_ab_migration.py | PASS |
| AT05 | 默认无授权，list 为空、move_to 拒绝 | tests/test_capabilities.py, tests/test_ab_migration.py | PASS |
| AT06 | 未注册/未授权能力拒绝且 Adapter 零调用 | tests/test_capabilities.py | PASS |
| AT07 | 无 Body：Memory 可用、invoke 返回 NO_BODY | tests/test_capabilities.py, tests/test_e2e.py | PASS |
| AT08 | 换 Body（FakeBodyAdapter）不改 Core | tests/test_e2e.py | PASS |
| AT09 | 权限不迁移（包内容与行为两级） | tests/test_migration.py, tests/test_ab_migration.py | PASS |
| AT10 | 未知地点/非法参数/异常/非法结果 | tests/test_capabilities.py, tests/test_e2e.py | PASS |
| AT11 | 损坏 JSON、版本与 agent_id 不一致拒绝 | tests/test_state.py, tests/test_migration.py | PASS |
| AT12 | 包路径边界（穿越/绝对/symlink/额外/缺失） | tests/test_migration.py | PASS |
| AT13 | 真隔离：B 用包内 runtime，A 不可达 | tests/test_ab_migration.py | PASS |
| AT14 | 运行状态不迁移：B 新绑定初始位置 (0,0) | tests/test_ab_migration.py | PASS |
| AT15 | 显式 Discovery：不扫描、不回退 | tests/test_capabilities.py, tests/test_e2e.py | PASS |
| AT16 | 写入失败：旧文件可读、不反馈成功 | tests/test_state.py | PASS |

## P0 Limitations

P0 本质是确定性的 Self/runtime 迁移原型，尚未解决：

- 经验语义（哪些记忆是 Body-dependent、哪些 Body-independent）；
- 模型后端独立性（P0 不调用 LLM）；
- 云端/在线连续性、多副本防克隆、身份防伪；
- 真实机器人 embodiment（坐标仅为二维 mock unit，无地理坐标与飞行语义）；
- 自适应 Body 映射（新能力语义需修改 Core，P0 不承诺未来任务零修改）；
- 并发写者、跨操作系统迁移、迁移时不停机。

设计边界与冻结决策见 `docs/PHANES_P0_ARCHITECTURE_FREEZE_v0.1.md`；验收结论见 `docs/PHANES_P0_FINAL_STATUS.md`。
