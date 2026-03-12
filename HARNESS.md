# HARNESS.md — Yuque Exporter CLI Harness Specification

本文件定义 **yuque-exporter** 项目的 CLI harness 规范，供 `/cli-anything` 与后续自动化代理实现时遵循。

---

## 1. 目标与范围

### 1.1 目标
将当前交互式导出工具（基于 DrissionPage + Rich + Questionary）扩展为：

- 可脚本化（非交互）
- 可状态化（session / profile / resume）
- 可机器消费（`--json`）
- 可测试（unit + e2e + subprocess）

### 1.2 范围
- 支持登录、会话管理、知识库枚举、文档导出、目录结构保留、结果审计。
- 支持批量导出与按目录节点过滤导出。
- 不实现对语雀官方 API 协议的逆向扩展（仅用现有稳定接口）。

### 1.3 非目标
- 不绕过认证机制。
- 不提供破坏性操作（删除线上文档/覆盖远端内容）。
- 不修改用户知识库内容，仅做读取与导出。

---

## 2. 当前代码架构（已确认）

- 入口：`src/main.py`
- 核心：
  - `src/core/client.py`（API 调用、导出轮询、下载）
  - `src/core/auth.py`（cookie 持久化与登录状态检查）
  - `src/core/exporter.py`（路径生成、元数据写入）
  - `src/core/models.py`（数据模型）
- 浏览器：`src/utils/browser.py`
- UI：`src/ui/console.py`

现状特点：
- 交互逻辑与业务逻辑耦合较高（`main.py`）。
- 已具备可复用能力（auth/client/exporter），适合抽象 CLI 子命令。

---

## 3. Harness 目录与包规范

在项目根目录新增：

```text
agent-harness/
├── YUQUE.md
├── setup.py
└── cli_anything/
    └── yuque/
        ├── __init__.py
        ├── README.md
        ├── yuque_cli.py
        ├── core/
        │   ├── project.py
        │   ├── session.py
        │   ├── auth.py
        │   ├── repo.py
        │   ├── export.py
        │   └── audit.py
        ├── utils/
        │   ├── output.py
        │   └── validators.py
        └── tests/
            ├── TEST.md
            ├── test_core.py
            └── test_full_e2e.py
```

命名要求：
- package name: `cli-anything-yuque`
- namespace: `cli_anything.yuque`
- 命令名：`cli-anything-yuque`

---

## 4. CLI 领域模型与命令分组

### 4.1 命令组

1. `auth`
   - `auth login`
   - `auth status`
   - `auth logout`（清理本地凭证）

2. `repo`
   - `repo list`
   - `repo tree --repo-id <id>`

3. `export`
   - `export run --repo-id <id> --format markdown|pdf|word|lake [--all | --node <uuid> ...]`
   - `export batch --repo-id <id>...`

4. `session`
   - `session init`
   - `session show`
   - `session doctor`（依赖检查、浏览器可用性检查）

5. `project`
   - `project info`
   - `project paths`

### 4.2 通用参数
- `--json`：结构化输出，禁止富文本。
- `--profile <name>`：多账号/多配置隔离。
- `--output-dir <path>`：导出目录覆盖。
- `--verbose`：调试日志。

---

## 5. 状态模型

### 5.1 本地状态目录
建议：`~/.yuque_harness/<profile>/`

包含：
- `cookies.json`
- `session.json`（最近成功操作、默认导出格式、默认输出目录）
- `audit.log`（每次导出记录）

### 5.2 状态约束
- 所有写入原子化（先写临时文件再替换）。
- 会话损坏时可恢复到最小可用状态（保留 cookies，重建 session）。

---

## 6. 输出协议（JSON）

统一 envelope：

```json
{
  "ok": true,
  "data": {},
  "error": null,
  "meta": {
    "request_id": "...",
    "ts": "2026-03-12T00:00:00Z"
  }
}
```

失败时：
- `ok=false`
- `error` 包含 `code`、`message`、`details`（可选）

---

## 7. 错误处理与退出码

- `0` 成功
- `2` 参数错误
- `3` 认证失败/会话过期
- `4` 远端 API 错误
- `5` 下载或文件系统错误
- `6` 未知错误

要求：
- 人类可读错误写 stderr
- `--json` 时 stdout 仅输出 JSON

---

## 8. 测试规范

### 8.1 Unit Tests
- 覆盖 core 模块（状态管理、路径清理、参数校验、输出协议）。
- 禁止依赖真实网络与真实浏览器。

### 8.2 E2E Tests
- 覆盖完整流程：登录状态读取 → repo 列表 → 单库导出模拟。
- 使用受控测试夹具（mock API 或录制响应）。

### 8.3 Subprocess Tests
- 必须通过 `_resolve_cli("cli-anything-yuque")` 调用安装后的命令。
- 支持 `CLI_ANYTHING_FORCE_INSTALLED=1`。

---

## 9. 安全与合规

- 不在日志打印敏感 cookie/token。
- 错误信息不得泄露完整响应头/凭证。
- 本地凭证文件权限应最小化（用户可读写）。
- 仅用于用户授权账户与知识库导出。

---

## 10. 实施阶段（与 cli-anything 对齐）

1. Phase 1：代码分析与动作映射
2. Phase 2：CLI 架构与 SOP（`YUQUE.md`）
3. Phase 3：核心实现（core + CLI + JSON）
4. Phase 4：测试计划（`TEST.md`）
5. Phase 5：测试实现（unit/e2e/subprocess）
6. Phase 6：执行测试并回填结果
7. Phase 7：打包发布与本地安装验证

---

## 11. 完成标准（DoD）

满足以下全部条件方可判定完成：

1. 所有核心命令可运行（交互与非交互）
2. `--json` 在所有命令生效
3. 单元测试、E2E、subprocess 测试全部通过
4. `TEST.md` 同时包含测试计划与结果
5. `setup.py` 可安装并产生 `cli-anything-yuque` 命令
6. README 给出安装、认证、导出、故障排查示例

---

## 12. 维护约定

- 新增命令必须同步更新：`README.md`、`TEST.md`、`YUQUE.md`。
- 任何输出字段变更必须保持向后兼容或提升 major 版本。
- 发生 API 变更时优先修复 adapter，不直接污染命令层。
