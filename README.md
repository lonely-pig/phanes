# Phanes — Persistent Self Portability Prototype

> **Project status:** P0 已完成并永久标记为 `v0.1.0-p0`。P1 Experience Portability / Applicability 已完成实现，通过 Overall Engineering Acceptance 与 Final Architecture Acceptance，并正式发布为 `v0.2.0-p1`。P2（Host Deployment & One-shot Onboarding）已完成 P2.1–P2.7 engineering candidate，当前为 **release candidate，尚未发布**：未合并 master、未打 tag、未创建 release，等待最终 release review。

Phanes 是 Python 3.11+、仅依赖标准库的持久 Agent Self 架构原型。Identity、Memory 与可移植 Experience 可以随固定白名单内的通用 Runtime 搬运到另一 Host；目标 Host 独立提供当前 Body、环境、frame、时钟和授权。迁移 Self 不等于迁移当前 Host 状态或权限。

本 README 解释当前实现；架构规范以 [P0 Freeze](docs/PHANES_P0_ARCHITECTURE_FREEZE_v0.1.md) 和 [P1 Freeze v0.2](docs/PHANES_P1_ARCHITECTURE_FREEZE_v0.2.md) 为准，[P1 Prior Art Gate](docs/PHANES_P1_PRIOR_ART_GATE.md) 记录技术取舍。

## Architecture Overview

| 边界 | 包含 | 不随 Self 迁移的内容 |
|---|---|---|
| **Self** | `identity.json`、`memory.json`、P1 的 `experiences.json`；后者记录不可变的历史 Experience 与来源声明 | 当前 Body、位置、授权、TargetContext 或适用性缓存 |
| **Generic Runtime** | 严格验证、持久化、适用性评价、Gateway、操作 Core、Registry 与迁移机制；包只携带固定白名单中的源码 | Host 专用 Adapter 与配置 |
| **Host** | 当前 Body / Adapter、Body 状态、environment、frame 断言、当前时间和授权 | 不由迁移包恢复或自动授予 |

Self 和固定的通用 Runtime 可以搬运；Host 不搬运。Adapter 提供某项能力也不等于得到授权：当前 Host 的 CapabilityRegistry 默认零权限，动作仍须经过它。

## P0 — Body Migration Foundation

P0 证明：同一 `agent_id` 与地点 Memory 跨独立环境保持不变；B 可绑定新的 MockUAV Body，但 A 的授权和 Body 位置不会随迁移包恢复。P0 的固定中文命令、显式 Discovery、CLI、package-v1 与独立 A/B 测试仍保留。它是 P1 的基础，不是当前项目的全部范围。

P0 本身不包含 Experience 适用性、LLM、规划器、ROS/PX4、真实硬件、云同步或身份防克隆。

## P1 — Experience Portability & Applicability

历史 Experience 可以随 Self 到达 B，但在 A 有用的观测不能自动成为 B 可用的动作目标。P1 为此加入独立的 `experiences.json`、两种固定类型的不可变记录，以及由目标 Host 当前 context 驱动的确定性适用性评价。

- Experience kind 仅有 `place_observation` 和 `body_parameter_observation`；intended use 仅有 `historical_query` 和 `navigation_target`。参数观测是历史证据，不提供配置应用路径。
- 每条记录显式声明 Body、environment、frame、temporal 四维依赖。当前 `TargetContext` 每次请求都从 B 的独立 provider 获取，不能由历史 Self 填入或覆盖。
- `frame_id` 只是 opaque exact-match identifier：相等表示 Host/operator 断言当前 Adapter 的原点、轴向、mock unit 和坐标解释一致；不是物理等价证明。P1 不做 tf2 变换、别名或坐标推断。
- 已知所需条件不匹配 → `INAPPLICABLE`；无已知不匹配但缺少必要信息 → `UNKNOWN`；所需条件完整匹配 → `APPLICABLE`。记录损坏或评价故障走独立失败通道，不伪装成 `UNKNOWN`。

四个不同的连续性边界必须分别判断：

1. **Experience continuity ≠ applicability continuity**：历史记录不变，B 的 Body／环境／frame／时间仍可使其不可用。
2. **Applicability ≠ authority**：`APPLICABLE` 只说明目标适用，不能授予 `move_to`。
3. **Self continuity ≠ Host-state continuity**：B 的 Body 位置与当前配置从新 Host 开始。
4. **Identity continuity ≠ permission continuity**：同一 `agent_id` 不继承 A 的授权。

受支持的 Experience 动作路径为：

```text
精确 experience_id → Gateway → 当前 TargetContext snapshot → applicability
  → 本请求专用 ResolvedNavigationTarget → Experience Core
  → 当前 Host 的 CapabilityRegistry.list / invoke → Adapter
```

导航不按 place、时间或“最新记录”自动选择。修正只追加新记录并以 `supersedes` 连接；导航中显式选择已被 supersede 的旧 ID 为 `INAPPLICABLE`，不自动跟随 successor。历史查询仍可读取该旧记录。`derived_from` 表示派生关系，不继承父记录的适用性。legacy `MemoryStore.lookup_place()` 不进入 P1 动作路径。

Provenance 是**未经认证的历史来源声明**，不是签名、真实性证明、信任分数、授权或适用性加成。P1 尚不提供生产 `user_statement`／`adapter_observation` 本地录入路径；本地新建仅支持显式 per-Store `test_mode=True` 下的 `test_fixture`。已有历史 Self 中的三种合法来源声明仍可加载和迁移，不作重新认证。

### Package-v2 and isolated A/B evidence

P1 package-v2 精确携带 `identity.json`、`memory.json`、`experiences.json` 和固定白名单内的通用 Runtime；与 P0 package-v1 严格分版，无隐式升级或降级。SHA-256 校验完整性，**不认证来源**。在受信任本地 Runtime 的测试边界内，导出／导入先完整验证、暂存，再原子提交；失败不覆盖既有目标。B 使用预装可信工具导入，导入不执行包内代码、不发现 Body、不恢复授权或 TargetContext、不评价 Experience，也不调用 Adapter。

[P1.7 隔离验收](tests/test_p1_isolated_acceptance.py) 包含 26 个显式场景：A/B 为不同 OS 进程，只有复制的包跨环境；A 的 Body 曾移动到 `[31,47]`，B 的新 Body 从真实观测的 `[0,0]` 开始，Runtime 来自复制包而 Host 由 B 独立提供。导入动作计数为零。B context 不匹配或缺失会阻断动作；相同适用记录在零授权时不能动作，B 新授权后才可调用一次 Mock `move_to`。`±10**400` 整数坐标在受测 Gateway → Core → Registry → Mock Adapter 链中精确保留。

P1 没有对应 P0 CLI 的用户演示入口；验收使用程序化 Runtime 组合与独立 Host provider。完整契约见 [P1 Freeze v0.2](docs/PHANES_P1_ARCHITECTURE_FREEZE_v0.2.md)。

## P2 — Host Deployment & One-shot Onboarding（工程候选，未发布）

P2 在冻结的 P0/P1 之上完成目标 Host 侧的部署与显式一次性 onboarding，不引入新研究机制。目标 Host 独立安装固定名称的 `_p0_discovery`（`phanes/discovery.py` 的逐字节副本）与 `phanes_host.p2_bootstrap`；`import phanes_host.p2_bootstrap` 完全惰性；显式调用 `onboard_host_session(...)` 后一个进程至多发布一个完整 Host 会话，失败可重试，成功后不可重置、重绑或追加授权。

- 公开 API 仅三项：`HostSessionDescriptor`（只读、临时、可持久化声明之外的空接口）、`OnboardingError`、`onboard_host_session`；授权输入 `allowed_capabilities` 是被动数据（精确 `tuple[str, ...]`，不含可执行迭代器，重复/未知语义仍由 Registry 判定）。
- offered 声明永远不是授权：零授权会话是完整的已发布会话，请求走既有 P1 适用性路径后仍被 Registry 拒绝。
- 三类 provider（environment/frame/evaluation_time）由当前 Host 显式提供，onboarding 期间只检查可调用、从不调用；当前 context 不被历史 Experience 填充。
- session ID 为成功完成 Runtime composition 后、publication 前生成的一次性 opaque UUID，不进入 TargetContext、Experience、Self 或迁移包；新 Body/新会话需要新进程。
- package-v2 白名单与 P1 完全一致；P2 代码、Host 状态与授权均不迁移。

已验证的边界证据（隔离部署端到端、具身/会话替换矩阵、失败注入与边界加固、迁移排除审计）见 [P2 Onboarding 文档](docs/PHANES_P2_ONBOARDING.md)。P2 不证明物理真实、恶意 Host 安全、加密身份、真实硬件安全或在线换体。

## P0 Quick Start

前置：Python 3.11+（本仓库在 Windows / Python 3.13.7 上验证）。在仓库根目录执行：

```powershell
python -m unittest discover -s tests -v   # 当前完整仓库：454 tests
```

以下 CLI 命令与交互语法是 **P0-only** 演示；不要用它从 P1 Self 的 legacy Memory 坐标发起 P1 动作。

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
New-Item -ItemType Directory -Force -Path $demo\env_b\tool, $demo\env_b\host | Out-Null
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

## Security / Trust Model

- Registry 默认零授权；授权只由当前 Host 提供，不随 Self 或 package-v2 迁移。P1 适用性评价也不会授予能力。
- P0 Discovery 只认显式本地配置与固定工厂表；系统不扫描设备或网络，不自动传播、提权、安装插件、以 `eval`/`exec` 执行输入或从配置加载任意模块，也不回退到有权限的 Body。
- 迁移不包含 host config、授权、凭据、Adapter、Body 运行时状态或待执行动作。导入由目标环境预装的可信工具验证包，验证／导入阶段不执行包内 Runtime 源码；**包不能验证自己**。
- SHA-256 只检查包内容完整性，不证明包的来源；Experience provenance 也只是未经认证的历史声明。P1 不宣称可安全导入恶意包，运行假设是可信本地 Runtime。
- `frame_id` 相等只表示 Host 作出的坐标解释断言，不证明两个物理坐标系相同。UUID 不是身份认证；复制包可产生同一身份的两个副本，P0/P1 均不提供防克隆保证。

## Repository Map

```text
phanes/               # 通用代码；v1/v2 各有不同的固定迁移白名单
  __init__.py         # legacy __version__ = "0.1"
  __main__.py         # P0 CLI + composition root；不在 P1 v2 Runtime 白名单
  contracts.py        # 数据结构、错误码、固定能力契约校验、BodyAdapter Protocol
  core.py             # P0 确定性命令映射（三种固定语法）
  identity.py         # Identity 初始化/严格加载 + 原子 JSON 写入
  memory.py           # legacy 地点事实持久化 MemoryStore
  capabilities.py     # CapabilityRegistry（默认零权限、单次绑定）
  discovery.py        # P0 显式配置 + 固定工厂表
  migration.py        # P0 package-v1 导出/校验/导入
  experience_contracts.py   # P1 固定 kind、来源、依赖与错误码
  experience_validation.py  # P1 严格结构验证（stdlib）
  experience_semantic.py    # P1 kind 表、关系图与语义验证
  experience_store.py       # 不可变 Experience 历史与受限测试写入
  applicability.py          # IntendedUse、TargetContext 与三值评价
  experience_gateway.py     # 精确 ID、当前 context、请求级目标
  experience_core.py        # P1 Experience 动作门控
  p1_runtime.py             # P1 程序化组合与 Host provider 注入
  migration_v2.py           # P1 package-v2 工具；不在 v2 Runtime 白名单
phanes_host/          # 宿主独立提供；不迁移
  mock_uav.py         # MockUAVAdapter（get_position / move_to）
  p2_bootstrap.py     # P2 显式一次性 onboarding（目标 Host 独立安装）
  _p0_discovery.py    # 仅部署产物：phanes/discovery.py 的逐字节副本（不在仓库中）
examples/
  host.mock-uav.json  # P0 显式授权示例（不自动加载）
schemas/
  phanes-experience-v1.schema.json  # P1 规范性结构 schema
tests/                # unittest：P0、P1 各阶段、P2 onboarding 及隔离 A/B 证据
docs/                 # P0/P1 Architecture Freeze、Prior Art 与 P2 文档
```

P1 v2 manifest 使用 `package_version: 2` 与 `core_version: "0.2"`；legacy `phanes.__version__` 仍为 `"0.1"`，供 P0 版本路径使用。两种表示尚未统一，v2 按 manifest 合同校验；本次文档更新不改变任何版本常量。

## Test Status and P0 Matrix

统一入口：`python -m unittest discover -s tests -v`。P1 发布时的已验收基线为 **362/362 PASS**；P2.2 接收时基线为 **404/404 PASS**；当前 P2 工程候选分支完整套件为 **454/454 PASS**（含 Sol P2.2 审查 B1 修正回归；P0/P1 生产语义未变，新增全部为 P2 测试）。下表保留 P0 的 AT01–AT16；P1 的结构、语义、Store、适用性、Gateway、操作、v2 迁移及隔离 A/B 由相应 `tests/test_experience_*.py`、`tests/test_applicability.py`、`tests/test_migration_v2.py` 和 `tests/test_p1_isolated_acceptance.py` 覆盖；P2 的隔离部署、替换矩阵与失败边界由 `tests/test_p2_*.py` 覆盖。

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

## P0 Limitations (Historical)

P0 本质是确定性的 Self/runtime 迁移原型，尚未解决：

- 经验语义（哪些记忆是 Body-dependent、哪些 Body-independent）；
- 模型后端独立性（P0 不调用 LLM）；
- 云端/在线连续性、多副本防克隆、身份防伪；
- 真实机器人 embodiment（坐标仅为二维 mock unit，无地理坐标与飞行语义）；
- 自适应 Body 映射（新能力语义需修改 Core，P0 不承诺未来任务零修改）；
- 并发写者、跨操作系统迁移、迁移时不停机。

P0 设计边界与冻结决策见 [P0 Architecture Freeze](docs/PHANES_P0_ARCHITECTURE_FREEZE_v0.1.md)；验收结论见 [P0 Final Status](docs/PHANES_P0_FINAL_STATUS.md)。P1 已针对部分历史 Experience 适用性问题建立有限的新契约；上述限制描述的是 P0 本身。

## P1 Nonclaims and Limits

P1 的验收仅使用二维 mock 坐标、可信本地 Runtime、两种 Experience kind 与两种 intended use。它**不证明** Experience 内容或依赖声明真实、provenance 可认证、相同 frame ID 对应真实物理等价，也不证明恶意迁移包安全、任意机器人知识迁移或 transfer learning。它不提供安全的 PID／控制参数转移、真实无人机飞行安全、权限或 Body 状态连续、在线迁移、多进程一致性，亦不涉及意识连续性。

将来可以另行研究更丰富的 Experience／技能与真实机器人验证；本文不承诺 P2 架构或实现。P1 的规范性边界见 [Architecture Freeze v0.2](docs/PHANES_P1_ARCHITECTURE_FREEZE_v0.2.md)，技术复用决策见 [Prior Art Gate](docs/PHANES_P1_PRIOR_ART_GATE.md)。
