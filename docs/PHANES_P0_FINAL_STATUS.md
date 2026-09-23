# PHANES P0 — Final Status

状态：PASSED。日期：2026-09-23。本文档记录 P0 最终实现与验收结果，不修改 `PHANES_P0_ARCHITECTURE_FREEZE_v0.1.md` 中的任何历史决策。

## Acceptance Result

**PHANES P0 PASSED**

- 验收 commit：`b7a4531`（P0.6 隔离验收）；文档整理于其后提交，无产品代码变更。
- 完整测试：`python -m unittest discover -s tests -v` → **143 tests, OK**。
- 手工验收：Windows PowerShell 下完成 A → export → 人工复制 → B import → 零授权拒绝（NO_BODY）→ 新 Body `mock-uav-B` + 新授权 → `去 Alpha。` → `[MockUAV] moving to (10, 20)`，全程 A 目录已移走，B 加载的是包内 runtime。

## Four Proofs（Freeze §13 / ADR 12 全部满足）

| 证明 | 证据 |
|---|---|
| Identity Persistence | A/B 的 `agent_id` 逐字节相等；B 未调用 uuid4 重建身份 |
| Memory Persistence | B 恢复后 `Alpha = (10, 20)`，含重启与跨环境 |
| Body Replacement | `mock-uav-A` → `mock-uav-B`；AT08 另以 FakeBodyAdapter 验证 Core 不变 |
| Authority Non-Persistence | 包内无任何授权；B 无配置时 `去 Alpha。` 返回 NO_BODY，显式授权后才成功 |

## Architecture Summary

- Self = Identity + Memory（JSON 文件，严格校验，原子写入，损坏即失败）；
- Runtime = PhanesCore + CapabilityRegistry + Discovery + Migration（随包迁移的固定白名单源码）；
- Host = BodyAdapter 实现 + host config + 授权（不迁移，B 独立预置 `phanes_host`）；
- 动作唯一路径：`lookup → registry.list → registry.invoke`；Core 不导入 discovery/`phanes_host`；
- 迁移包 = `manifest.json`（版本 + agent_id + 全文件 SHA-256）+ `self/` + `runtime/phanes/`；先全量校验后原子提交，包内代码在验证前是数据。

## Deviations & Decisions（实现层，未触及冻结项）

- Freeze §12 冻结三种中文命令语法；Sol P0.4 Directive 的英文 `move`/`where` 为示意，按 Freeze 执行，未新增第四种语法；`get_position` 验收经 Registry 直接调用（Freeze §12 步骤 12）。
- P0.3.1 起 `load_identity` 要求 `agent_id` 为 canonical uuid4、`name == "Phanes"`、`created_at` 必须为带 `Z` 的 UTC ISO 8601。
- `bind()` 拒绝固定契约之外的 offered capability（如 `land`），失败不留部分绑定状态。
- `init` / `export` / `import` 均采用同级临时目录 + rename 提交，失败不留半成品。
- Windows/PowerShell 文档注意事项：JSON 配置须无 BOM（用 `[IO.File]::WriteAllText`）；管道 stdin 须 `$OutputEncoding = [Text.UTF8Encoding]::new($false)`。

## Known Limitations

- 坐标为二维 mock unit；无真实飞行、地理坐标与物理安全语义；
- 单进程、单 Body、无热切换、无并发写者；
- SHA-256 仅完整性校验，非来源认证；UUID 非身份凭证；不防克隆；
- 未验证跨操作系统迁移；迁移需停机（先停 A 再启 B）；
- 包内代码只适用于用户自己导出的可信包；未知来源可执行包不是 P0 支持场景。

## Next-Stage Questions（P1 候选，未开始）

- 经验语义：Body-dependent 与 Body-independent 记忆的边界；
- 模型后端接入时的 Memory/Identity 独立性与提示注入边界；
- 真实 Body（PX4/ROS）Adapter 的契约扩展与安全控制；
- 身份防伪、多副本检测、在线连续性。

## Test Distribution

| 文件 | 测试数 | 覆盖 |
|---|---|---|
| tests/test_capabilities.py | 62 | 契约校验、Registry 授权模型、MockUAV、Discovery（AT05–07/10/15） |
| tests/test_state.py | 29 | Identity/Memory 持久化与严格校验（AT01/02/16，AT10/11 Self 侧） |
| tests/test_e2e.py | 24 | Core 命令流、CLI、init 事务、依赖边界（AT07/08/10） |
| tests/test_migration.py | 26 | 导出/导入/校验/原子性（AT03/09 包内容/11/12） |
| tests/test_ab_migration.py | 2 | 独立 A/B 隔离验收（AT03/04/05/09/13/14） |
| **合计** | **143** | AT01–AT16 全覆盖 |
