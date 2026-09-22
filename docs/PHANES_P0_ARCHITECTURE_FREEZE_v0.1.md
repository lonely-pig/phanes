# PHANES P0 — Architecture Freeze v0.1 与 Sol 开发任务书

状态：Frozen / 待实现。日期：2026-09-22。架构负责人：Astra。实现负责人：Sol。

本文件是空仓库的首版设计，不代表功能已经实现或测试已经通过。实现只使用 Python 3.11+ 标准库；测试使用 unittest。P0 不调用 LLM。下面的接口是契约说明，不要求额外引入框架。

## 1. P0 Hypothesis

**一个 Agent 的持久身份和已保存事实，可以独立于具体 Body 保存、搬运和恢复；恢复后的同一份 Core，可以发现目标环境显式提供且授权的能力，并用恢复的事实驱动该能力。**

证明条件：A 保存 Alpha=(10,20) 并导出；人工将包放进干净的 B；B 不读取 A 的路径、配置或进程，恢复相同 agent_id 和 Alpha；B 的宿主显式提供 MockUAV 并授权 move_to；执行“去 Alpha。”，输出 `[MockUAV] moving to (10, 20)`，且 get_position 返回 (10,20)。

同一迁移包在没有本地授权的 B 上必须不能移动。此正反两条路径共同构成 P0 成功标准。

这里证明的是数据身份连续性、事实连续性及能力解耦，不是意识、人格、物理安全性，也不是不可伪造的身份认证。确定性解析足以验证假设；自然语言理解不属于实验变量。

## 2. Scope

### P0 MUST HAVE

- 创建一次并持久化 Identity；恢复时严格校验，不静默创建替代身份。
- 将地点事实存为 JSON；支持 remember、lookup、重启恢复。
- 固定的中文命令语法：记住地点、查询地点、前往地点。
- 无 Body 启动；一个显式配置的 MockUAV；每次进程启动最多绑定一个 Body。
- 本地宿主配置选择 Body 并逐项授权 capability；默认授权集合为空。
- BodyDescriptor、受控 CapabilityRegistry、参数校验及结构化错误。
- 手动导出/导入迁移目录；包括必要的通用可执行代码和 Self 数据。
- A/B 两个独立目录、独立进程的可重复演示及负向授权测试。
- 文档明确包、状态目录、本地配置和运行时 Body 状态的边界。

### P0 MUST NOT HAVE

- 真实无人机、PX4、ROS、MAVLink、机器人 SDK、视觉、复杂语音、LLM 接入、多 Agent、人格系统。
- 网络扫描、设备搜索、网络服务、自动设备配对、远程部署、自动复制或自主传播。
- 自动获取权限、继承 A 的权限、绕过认证、提权、exploit、隐蔽驻留、任意代码注入。
- 动态插件加载、从配置导入任意 Python 模块、eval/exec、任意 shell 工具能力。
- 向量库、数据库服务、事件总线、调度器、复杂依赖注入、通用技能框架。
- 密钥身份体系、加密同步、分布式一致性、在线 Body 热切换、迁移时不停机。
- 自动执行 Memory 中的指令、迁移后自动继续动作、未经用户新命令就移动。

## 3. Architecture

冻结为以下小组件，文件数量不等于类数量：

| 组件 | 责任 | 形态 |
|---|---|---|
| PhanesCore | 将确定性意图映射为记忆操作或能力调用；提供当前身份、Body 视图 | 一个类 |
| Identity | agent_id、name、created_at 及 JSON 读写校验 | dataclass + 函数 |
| MemoryStore | 地点事实持久化、lookup、更新 | 一个类 |
| BodyDescriptor / CapabilitySpec | 当前身体和能力的只读描述 | dataclass |
| BodyAdapter | descriptor、能力目录及 invoke | 一个 Protocol |
| CapabilityRegistry | 本次绑定、授权过滤、校验、唯一调用入口 | 一个类 |
| BodyDiscovery | 用宿主配置从固定工厂表创建 Adapter | 一个函数，无扫描服务 |
| MockUAVAdapter | 模拟当前位置及两项能力 | 一个类 |
| MigrationPackage | 导出/校验/导入目录协议 | 函数 + manifest，无独立对象层 |
| CLI / composition root | 解析参数、加载本地配置、组装 Core、处理输出 | 一个入口 |

不引入独立的 Self、Mind、Body 实体类，也不分离 Capability 基类与 handler 类。Self 在 P0 是 Identity + Memory 的逻辑边界；Body 通过 descriptor 和 adapter 表示。

命令解析置于 Core，固定匹配三种语法，不做智能推理。移动业务路径必须经过 `lookup → registry.list → registry.invoke`，不得直接调用 Adapter。

## 4. Dependency Direction

箭头表示“依赖”：

```text
CLI / bootstrap ─→ Core ─→ Identity / MemoryStore / CapabilityRegistry
       │                              Registry ─→ contracts
       └─→ discovery ─→ MockUAVAdapter ──────────→ contracts
                            （未来其他 Adapter 同样依赖 contracts）
migration ─→ Identity / Memory 格式校验 + 标准库文件操作
contracts ─→ Python 标准库
```

- Core 不导入 discovery、MockUAV、操作系统 API 或任何硬件 SDK。
- Registry 仅持有 BodyAdapter Protocol，不按 body_type 分支。
- discovery 中固定工厂表允许依赖具体 Adapter；CLI 负责把 Registry 注入 Core。
- migration 不启动 Adapter、不注册能力、不调用 Core 执行动作。
- 通用代码允许用 pathlib 等标准库文件接口；不得依赖 Windows/Linux 特有行为。
- 未来接 PX4/ROS 时新增 Adapter 及宿主部署/工厂映射；符合现有能力契约时，Core 不变。新增任务语义可能需要修改 Core，P0 不承诺任意未来任务无需修改。

## 5. Identity

`identity.json`：

```json
{
  "schema_version": 1,
  "agent_id": "7d4d40b9-9f8a-40b1-80ec-cb0333bdb2b7",
  "name": "Phanes",
  "created_at": "2026-09-22T00:00:00Z"
}
```

- agent_id：初始化时 uuid4 生成一次，导入后原样保留。
- name：P0 固定 Phanes；不是人格模型。
- created_at：首次创建时间，UTC ISO 8601；恢复不修改。
- schema_version：Identity 文件自身的版本，P0 只接受 1。
- `init` 仅允许空状态目录；已有数据时拒绝覆盖。普通启动缺失/损坏 Identity 必须报错。
- 连续性证明：A/B 的 Identity 字段相等，agent_id 未重新生成；迁移哈希校验辅助检测文件损坏。
- UUID 与 SHA-256 都不是身份认证。复制包会产生同一身份的两个副本；P0 不提供唯一活动实例或防克隆保证。演示时先停止 A，再启动 B。

## 6. Memory

`memory.json`：

```json
{
  "schema_version": 1,
  "agent_id": "7d4d40b9-9f8a-40b1-80ec-cb0333bdb2b7",
  "places": {
    "Alpha": {"x": 10, "y": 20}
  }
}
```

- 格式仅存地点事实；不存命令、capability、凭据、授权或 Body 状态。
- 地点名去除首尾空白、区分大小写、非空。CLI 地点 token 不含空白及括号/逗号/句号等语法分隔符。
- x/y 是有限 JSON 数字，拒绝 bool、NaN、Infinity；允许负数和小数。
- `remember_place(name, x, y) -> None`：同名更新；磁盘写入成功才反馈成功。
- `lookup_place(name) -> Position | None`：未知地点返回 None；不猜测坐标。
- 写入采用同目录临时文件、flush、fsync、os.replace；异常保留上一份有效文件。只保证单进程使用，P0 不实现并发写者或跨平台断电事务。
- 每次启动完整校验 JSON、版本、字段类型及 memory.agent_id 与 Identity 一致性；损坏时失败，不当作空记忆。
- 恢复：导入校验通过后将原始 Identity/Memory 文件复制到新的状态目录，重新打开 MemoryStore；不做摘要、重建或格式迁移。

## 7. Body Model

最小 BodyDescriptor：

```json
{
  "body_id": "mock-uav-B",
  "body_type": "mock_uav",
  "adapter_api_version": 1
}
```

- body_id 由当前宿主配置给出，用于描述当前绑定；不是认证凭据，也不要求全球唯一。
- body_type 供显示和诊断；Core 不根据它做硬件分支。
- adapter_api_version 校验 Adapter 接口兼容性；不支持的版本拒绝绑定。
- `core.current_body -> BodyDescriptor | None` 回答“我现在拥有什么身体”。
- descriptor 与当前位置都是本次运行状态，不写入 Self、不进迁移包。
- 无本地 Body 时 current_body=None；记忆操作仍正常，动作返回 NO_BODY。
- P0 坐标为二维本地模拟坐标，单位为 mock unit；A/B 的 Alpha 在本实验约定同一模拟坐标系。真实地理坐标、坐标变换及飞行语义需另行 ADR。

## 8. Capability Model

Adapter 提供能力目录，宿主授权决定其中哪些能被注册。目录和授权都不来自 Memory 或迁移包。

```text
BodyAdapter:
  descriptor: BodyDescriptor
  capabilities() -> tuple[CapabilitySpec, ...]
  invoke(name: str, args: dict) -> dict

CapabilitySpec:
  name: str
  contract_version: int          # P0 固定 1

CapabilityRegistry:
  current_body: BodyDescriptor | None
  list() -> tuple[CapabilitySpec, ...]  # 仅返回已授权且已绑定能力
  invoke(name: str, args: dict) -> InvocationResult
```

P0 用固定契约和显式校验函数，不引入通用 JSON Schema 执行器：

| 能力 | 参数 | 成功 data | 行为 |
|---|---|---|---|
| get_position v1 | `{}` | `{"x":0,"y":0}` 等当前位置 | 读取模拟位置 |
| move_to v1 | `{"x":10,"y":20}` | `{"x":10,"y":20}` | 同步完成移动并更新模拟位置 |

初始位置固定 (0,0)。参数键必须精确匹配契约，不接受额外字段；坐标遵循 Memory 数字限制。两个能力可独立授权，move_to 不隐含 get_position 权限。

```json
{"ok": true, "data": {"x": 10, "y": 20}, "error": null}
```

```json
{"ok": false, "data": null, "error": {"code": "CAPABILITY_UNAVAILABLE", "message": "move_to is not registered"}}
```

错误码：NO_BODY、CAPABILITY_UNAVAILABLE、INVALID_ARGUMENT、ADAPTER_ERROR、INVALID_RESULT。未知地点为 Core 的 UNKNOWN_PLACE；语法不支持为 INVALID_COMMAND。

授权规则：

1. Registry 初始化为空；单次启动只绑定一次，不提供运行中 grant API。
2. `registered = adapter.offered ∩ host.allowed`；配置中的未知授权名、重复能力名或不兼容版本导致绑定失败，启动终止。
3. 缺省 allowed_capabilities 为 []；不支持通配符和自动“授权全部”。
4. 每次 invoke 先检查 Body，再检查 Registry 成员，再校验参数，最后调用 Adapter。未授权和未注册均返回 CAPABILITY_UNAVAILABLE，且 Adapter 调用次数为零。
5. Adapter 返回值也校验；异常转为 ADAPTER_ERROR，禁止自动重试 move_to。失败不能被报告为移动成功。
6. MockUAV 成功移动时仅输出一次 `[MockUAV] moving to (10, 20)`；整数坐标使用整数显示。CLI 可以另行显示结果，但不得重复此日志。

宿主配置文件代表运行者显式批准，P0 不构建用户认证服务。Registry 是可信本地代码的调用边界，不是恶意 Python 插件的沙箱；不加载外部插件。身份、Body 声明或 Adapter 自报能力均不能独立授予权限。

## 9. Body Discovery

`discover_body(config) -> BodyAdapter | None` 只处理命令行显式传入的本地 JSON：

```json
{
  "schema_version": 1,
  "body": {"adapter": "mock_uav", "body_id": "mock-uav-B"},
  "allowed_capabilities": ["get_position", "move_to"]
}
```

- 不传 `--host-config`：没有 Body、没有权限；不搜索当前目录、环境变量或网络。
- `body: null` 也表示无 Body，此时非空授权列表是配置错误。
- `adapter` 只能命中固定工厂表 `{"mock_uav": MockUAVAdapter}`；绝不是文件路径、模块路径或下载地址。
- 未知 adapter、损坏配置、未知字段和不支持版本：明确失败，不能回退到一个有权限的 Body。
- 发现成功后由 bootstrap 按第 8 节建立 Registry，再注入 Core。
- 未来新增 Adapter 修改宿主侧工厂表即可；没有理由在 P0 设计设备扫描接口。

## 10. Migration

**迁移是用户主动导出、人工复制、显式导入及重启的离线过程。系统没有传输或部署功能。**

| 对象 | 性质 | 是否迁移 |
|---|---|---|
| Identity、Memory | Phanes Self 的持久数据 | 是 |
| Core、contracts、Memory/Identity I/O、Registry | 可替换但必需的通用执行代码，不等同于身份本身 | 是 |
| CLI、discovery 通用加载逻辑、migration 校验代码 | 通用启动支持 | 是 |
| host.json、授权列表、凭据 | 目标宿主 Authority | 否 |
| MockUAV / 未来硬件 Adapter 实现及其 SDK | Body-specific component | 否；B 独立预安装 |
| 当前坐标、当前 Body、已绑定 Registry | 运行时 Body 状态 | 否 |
| Python、虚拟环境、操作系统、源码仓库、缓存、日志 | 环境与开发材料 | 否 |

P0 不存在需要迁移的 Core 配置。schema/接口版本是格式元数据，不携带权限。manifest 声明所需 Python 与宿主 Adapter API 版本，不声明要求宿主授予哪些权限。

迁移包是固定结构的普通目录，避免归档解压逻辑：

```text
transfer/
  manifest.json
  self/
    identity.json
    memory.json
  runtime/
    phanes/                  # 明确白名单中的通用 .py 源文件
```

manifest 必含 `package_version: 1`、`core_version: "0.1"`、`python_requires: ">=3.11"`、`adapter_api_version: 1`、`files`（相对路径到 SHA-256 的映射）。files 列举每个 Self 文件及通用源码文件，manifest 本身不递归哈希。

导出规则：

- A 退出交互进程后，用独立 export 命令读取状态；执行者保证没有其他写者。
- 校验 Identity/Memory；按固定源码白名单复制通用代码，禁止递归打包仓库或工作目录。
- 先写同级临时目录，最后重命名为指定的、原本不存在的包目录；失败不留下看似完整的包。
- 不导出 phanes_host、配置、测试、日志、缓存、凭据或 Adapter。不同宿主配置下导出的 manifest 文件清单必须一致。

导入规则：

- B 预置 Python 和固定发布的宿主包 `phanes_host`，其独立目录通过启动命令的 PYTHONPATH 提供；不从迁移包安装 Body 代码。
- 由 B 预置的同版本通用 migration 工具先校验整个包；校验工具不能来自尚未验证的包。验证成功后 B 运行包内 runtime 的通用代码。
- 拒绝绝对路径、`..`、符号链接、预期清单以外的文件、缺失文件、哈希错误、不兼容版本、字段错误及 Identity/Memory 的 agent_id 不一致。
- 导入目标必须不存在；先写同级临时状态目录，全部验证成功后重命名。不得覆盖/合并已有 Self；失败不得创建可启动状态。
- import 只恢复 Self，不读取包中任何宿主配置，也不执行 Body 动作。
- 包内代码只适用于用户自己导出的可信包；哈希没有签名，不能证明恶意重写包的来源。未知来源可执行包不是 P0 支持场景。

“干净 B”指新的状态目录、独立进程，且不能访问 A 的状态/源码路径；允许预置上述显式运行依赖。P0 不验证跨操作系统迁移。代码随包携带证明可独立运行；Identity 连续性由 Self 决定，不由源码文件名决定。

## 11. Directory Structure

```text
phanes_try/
  README.md
  phanes/                    # 通用运行时；迁移白名单覆盖这些源码
    __init__.py              # core version
    __main__.py              # CLI + bootstrap
    contracts.py             # Protocol、数据结构、能力契约校验
    core.py
    identity.py
    memory.py
    capabilities.py
    discovery.py             # 显式配置、固定工厂；延迟导入宿主包
    migration.py
  phanes_host/               # 目标宿主独立提供；不迁移
    __init__.py
    mock_uav.py
  examples/
    host.mock-uav.json       # 显式授权示例，启动时不自动加载
  tests/
    test_state.py
    test_capabilities.py
    test_migration.py
    test_e2e.py
  docs/
    PHANES_P0_ARCHITECTURE_FREEZE_v0.1.md
```

单仓库、两个代码目录，不拆微服务，不要求包管理器或第三方依赖。A 无 Body 时 discovery 不导入 phanes_host，通用运行时可独立启动。

## 12. End-to-End Flow

冻结 CLI 形状：

```text
python -m phanes init --state <A/state>
python -m phanes run --state <A/state>
python -m phanes export --state <A/state> --out <transfer>
python -m phanes import --package <B/transfer> --state <B/state>
python -m phanes run --state <B/state> --host-config <B/host.json>
```

命令交互语法：

- `记住 Alpha 是坐标 (10, 20)。`：逗号和括号接受相应中英文形式，允许分隔空白与可选末尾句号。
- `查询 Alpha。`：输出查得坐标或 UNKNOWN_PLACE。
- `去 Alpha。`：发起一次移动；其他自由表达不承诺理解。
- `退出`：退出交互；EOF 同样正常退出。

完整调用流：

1. A/init 创建 Identity，并创建相同 agent_id 的空 Memory。
2. A/run 恢复 Self；未提供 host config，Registry 为空，current_body=None。
3. 用户输入记忆命令 → Core 解析地点及坐标 → MemoryStore.remember_place → 原子写入 → 回复保存成功。
4. 退出 A/run；export 校验 Self → 复制通用源码及 Self → 写 manifest → 提交迁移目录。
5. 用户手动复制 transfer 到 B。B 具有预置 Python、可信导入工具和独立的 phanes_host；无需 A 仓库。
6. B 用预置工具 import → 校验版本、清单、哈希和 Self → 提交 B/state。
7. B/run 的工作目录是 B/transfer/runtime；宿主包搜索路径只包含 B 的宿主目录。启动加载的是迁移包 Core，不能回退到 A 的源码。
8. bootstrap 读取 B/state → Identity 与 Memory 恢复 → 读取 B/host.json → discovery 固定工厂创建 MockUAV → 初始位置 (0,0)。
9. Registry 检查能力目录与宿主授权，注册 get_position 和 move_to → 注入 Core；显示 agent_id、BodyDescriptor 和已授权能力列表。
10. 用户输入“去 Alpha。” → Core.lookup_place("Alpha") → Position(10,20) → Registry.list 确认 move_to v1 可用 → Registry.invoke("move_to", {"x":10,"y":20})。
11. Registry 检查当前绑定、成员和参数 → MockUAVAdapter.invoke → 更新位置 → 输出 `[MockUAV] moving to (10, 20)` → Registry 校验结果 → Core 报告成功。
12. 验收通过 Registry 调用 get_position 确认 (10,20)，并核对 B 的 agent_id 与 A 相同。

A 无 Body 完成记忆、B 获得新 Body 完成动作，已经足够证明本阶段的 Body 迁移假设。

## 13. Acceptance Tests

统一入口：`python -m unittest discover -s tests -v`。使用 TemporaryDirectory、subprocess 和标准库测试替身；不需要网络或真实硬件。

| 编号 | 场景 | 必须断言 |
|---|---|---|
| AT01 | 初始化与重启 | Identity 不变，重复 init 拒绝且原状态不变 |
| AT02 | Memory 持久化 | 记住 Alpha 后重建 MemoryStore，lookup 得到 (10,20)；同名更新可恢复 |
| AT03 | 跨环境恢复 | A 导出，B 独立导入，所有 Identity 字段及 Memory 相同；B 未调用 uuid4 创建新身份 |
| AT04 | 完整闭环 | B 输入“去 Alpha。”出现精确 MockUAV 日志；get_position=(10,20)；A/B 是独立进程 |
| AT05 | 默认无授权 | B 提供 MockUAV 但省略/置空授权，Registry.list 为空；move_to 拒绝，位置不变 |
| AT06 | 未注册能力 | 调用不存在的 land 或未授权 move_to 返回 CAPABILITY_UNAVAILABLE；Adapter 调用计数为零 |
| AT07 | 无 Body | Memory 可用；Registry invoke 返回 NO_BODY；Core 不崩溃 |
| AT08 | 换 Body | 测试本地 FakeBodyAdapter 实现同契约，将原 Core 注入新 Registry 后仍可移动；Core 源码不改且不导入 phanes_host |
| AT09 | 权限不迁移 | A 曾拥有 move_to；迁移包不含宿主配置/Adapter/绑定状态；B 无授权时仍拒绝，显式本地授权重启后才成功 |
| AT10 | 错误与副作用 | 未知 Alpha 不调用 Adapter；缺参数、额外字段、bool、非有限数等拒绝；异常/非法结果不报告成功且不重试 |
| AT11 | 损坏与兼容 | 损坏 JSON、未知 schema/package/API 版本、agent_id 不一致或哈希错误拒绝；不生成新身份或部分目标状态 |
| AT12 | 包路径边界 | 意外文件、绝对路径、路径穿越、符号链接、缺失文件拒绝；已存在导入目标不覆盖 |
| AT13 | 真正隔离 | B 使用迁移 runtime 与 B 本地宿主；环境清除 A 的 PYTHONPATH，以 A 目录移走/不可达状态运行，仍通过 AT04 |
| AT14 | 运行状态不迁移 | A MockUAV 曾移动；B 新绑定初始位置仍是 (0,0)，没有恢复待执行动作 |
| AT15 | 显式 Discovery | 不传配置不加载 Body；未知 adapter/授权、重复能力、错误版本启动失败，不扫描、不回退 |
| AT16 | 写入失败 | 模拟 os.replace 前写入失败，旧 Memory 仍可读取，CLI 不反馈保存成功 |

AT08 的 FakeBodyAdapter 仅为测试替身，不增加产品 Body，也不涉及多 Agent。AT13 必须确认实际加载的 phanes.__file__ 位于 B 的 runtime，避免同仓库测试产生假阳性。

## 14. Sol Implementation Backlog

按顺序实施。每个任务完成后只运行相关测试；P0.7 执行完整验收。Sol 可以直接进行普通实现与修复，无需每一步请求 Astra。

### P0.1 — 固化契约和源码边界

- 目标：建立最小目录及可导入的公共契约。
- 创建：phanes/__init__.py、contracts.py；phanes_host/__init__.py；测试目录。定义 Position、Identity 所需类型、BodyDescriptor、CapabilitySpec、BodyAdapter Protocol、InvocationResult 及固定能力校验。
- 完成条件：参数/返回结构与本文一致；通用模块不导入具体 Adapter；版本常量集中明确。
- 测试：test_capabilities 中契约校验用例，覆盖有限数、bool、额外字段和错误结果；执行对应 unittest 模块。
- Astra：不需要；修改接口语义、坐标含义或依赖方向时才升级。

### P0.2 — 持久身份与地点记忆

- 目标：只靠本地文件证明重启连续性。
- 创建：identity.py、memory.py、tests/test_state.py。
- 完成条件：显式 init、严格加载、agent_id 交叉校验、remember/lookup 和原子写；损坏不会重建身份。
- 测试：AT01、AT02、AT16，以及 AT10/AT11 涉及 Self 的子用例；`python -m unittest discover -s tests -p test_state.py -v`。
- Astra：不需要；加入身份认证、数据库或并发写者支持前升级。

### P0.3 — Registry、MockUAV 与显式 Discovery

- 目标：建立默认拒绝、显式授权的能力调用路径。
- 创建：capabilities.py、discovery.py、phanes_host/mock_uav.py、examples/host.mock-uav.json；补齐 test_capabilities.py。
- 完成条件：固定工厂、单次绑定、授权成员检查、参数/返回校验、两个 Mock 能力及精确日志；无配置无 Body。
- 测试：AT05–AT07、AT10 能力子用例、AT15；`python -m unittest discover -s tests -p test_capabilities.py -v`。
- Astra：不需要；增加动态加载、自动发现、授权继承或热切换时升级。

### P0.4 — Core 与 CLI 的最小任务闭环

- 目标：固定语法连接持久记忆和受控能力。
- 创建：core.py、__main__.py；开始 tests/test_e2e.py。
- 完成条件：init/run、三种命令及退出可用；Core 只依赖公共组件；缺 Body、未知地点及 Adapter 失败均明确反馈；启动显示身份与当前 Body/能力。
- 测试：单环境记忆→移动，AT07、AT08、AT10；用 stdin 驱动 CLI，不要求人工交互测试。
- Astra：不需要；引入 LLM 或新任务编排语义时升级。

### P0.5 — 迁移包导出与导入

- 目标：Self 和通用代码可移植，Authority 和 Body 实现不随包传递。
- 创建：migration.py、tests/test_migration.py；补齐 CLI export/import。
- 完成条件：固定白名单、manifest、SHA-256、严格路径/版本/数据校验、临时目录提交、不覆盖已有目标；导入不执行 Adapter。
- 测试：AT03、AT09 的包内容、AT11、AT12；`python -m unittest discover -s tests -p test_migration.py -v`。
- Astra：不需要；修改包边界、引入网络传输、签名身份或格式升级机制时升级。

### P0.6 — 独立环境迁移验收

- 目标：排除共享源码、共享状态、共享权限导致的假成功。
- 修改：test_e2e.py；建立 A/B 临时目录与 subprocess harness，分别预置可信工具和 B 宿主包。
- 完成条件：B 使用包内 Core，A 路径不可达；记忆指令→导出→人工复制的测试模拟→导入→显式绑定→移动完整运行；无授权反例同样通过。
- 测试：AT03、AT04、AT09、AT13、AT14 及换 Body 的 AT08；`python -m unittest discover -s tests -p test_e2e.py -v`。
- Astra：仅在无法不修改冻结边界就完成闭环时介入；不得为使测试通过偷偷携带宿主配置或 Adapter。

### P0.7 — 可复现实验与交付

- 目标：让下一位工程师按说明独立复现。
- 创建：README.md，给出准确的 Windows PowerShell A/B 路径及 PYTHONPATH 命令、前置依赖、三种语法、成功日志和无授权失败日志；明确预置导入工具与包内运行时的不同角色。
- 完成条件：按 README 在新目录完成演示；全部 AT 有对应自动测试；报告实际测试数量/结果和已知限制，移除临时数据。
- 测试：`python -m unittest discover -s tests -v`，再按 README 跑一次两个目录的演示。
- Astra：通过后不需例行复审；任一核心 ADR 必须变更才升级。报告失败不能标记为完成。

## 15. Architecture Decision Record

# PHANES P0 ARCHITECTURE FREEZE v0.1

状态：冻结。适用：Sol、Kimi、DeepSeek 及后续实现者。修改下列核心决定需要提出 ADR 变更并升级 Astra；普通命名、私有函数、错误文案及测试实现细节由实现者自行决定。

1. **验证目标固定**：仅 Identity Persistence、Memory Persistence、Body Discovery、Capability Execution；以独立 A/B 闭环及无授权反例验收。
2. **Self 与 Body 分离**：Self=Identity+Memory；Body 是宿主提供的 Adapter 与运行时 descriptor。Body 状态不构成 Self。
3. **认知可迁移，权限不自动迁移**：授权仅来自目标宿主的显式配置，默认零权限；Identity continuity 不等于 permission continuity。
4. **Core 不依赖具体 Body**：所有动作经过注入的 Registry；Core 不导入 MockUAV、ROS、PX4 或平台专用 API。
5. **最小实现固定**：Python 标准库、JSON、固定语法、单进程单 Body；不引入 LLM、动态插件、通用 Agent 框架或硬件连接。
6. **Discovery 只认显式本地配置**：固定工厂表，不扫描、不下载、不自动寻址；启动错误不得回退授权。
7. **调用必须被授权且符合契约**：Registry 是 Core 唯一动作入口；调用前校验注册/参数，调用后校验结果；失败不重试移动。
8. **Identity 连续性仅为数据连续性**：保留同一 UUID 和 Memory，不宣称防伪、防克隆或意识连续性；损坏时不能静默新建身份。
9. **迁移边界固定**：人工离线搬运 Self+通用源码；不带宿主授权、凭据、Adapter、SDK、运行时位置或待执行动作；B 独立提供 Body。
10. **导入失败必须封闭**：完整校验再提交，不覆盖已有 Self，不执行包内动作，不自动恢复任务；SHA-256 只用于完整性检查，不是认证。
11. **坐标仅属 Mock 实验**：二维共同模拟坐标系；真实飞行、地理坐标与物理安全控制必须后续另立设计。
12. **成功以证据为准**：B 实际加载迁移代码、A 不可达、Identity/Memory 相等、授权移动成功、无授权拒绝、替换测试 Body 不改 Core，缺一不可。

升级格式：说明冲突的 ADR 条目、具体失败场景、最小替代方案及影响的验收测试；冻结变更获决策前，继续完成不受冲突影响的任务。
