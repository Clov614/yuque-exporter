# TEST PLAN

## Scope

- Unit: session/profile state handling, output envelope, validators, exit-code mapping, audit write.
- E2E (mocked): full export orchestration without real network/browser.
- Subprocess: installed/module CLI behavior for `--json` output and return codes.
- Repository references: immutable ID/namespace/URL parsing, direct resolution and safe URL rejection.
- Repository services: tree/export paths that do not depend on the common-used list.
- Application/UI: direct repository input, empty-list fallback and repository identity display.

## Cases

1. `test_core.py`
   - Session init/read/update
   - Corrupt session recovery
   - Output success/failure envelope and emit behavior
   - Validator pass/fail paths
   - Exit-code mapping
   - Audit log append
2. `test_full_e2e.py`
   - Mocked export run (`--all`) with success result aggregation and audit write
   - Mocked export run for node filtering path
3. `test_subprocess.py`
   - `project info` JSON envelope + rc 0
   - `project paths` JSON envelope + rc 0
   - Parameter error returns rc 2 + JSON failure envelope
4. `test_image_localization.py`
   - Inline HTTP/HTTPS 图片改写、URL 去重、稳定文件名和失败保留
   - 忽略相对图片、非 HTTP scheme 与 fenced code block
5. `test_external_image_download.py`
   - 无认证 Session、URL/IP 安全校验、Content-Type/大小限制和临时文件清理
6. `test_cli_download_images.py`
   - `export run/batch --download-images` 参数传递
   - 非 Markdown 格式返回参数错误
7. `test_repository_reference.py`
   - 正整数 ID、namespace 和 Yuque URL 归一化
   - 非法 host、文档 URL、编码路径和不安全 authority 拒绝
8. `test_repository_resolver.py` / `test_repository_client.py`
   - 已登录 Web API 查询、知识库页面 appData fallback、响应解包、状态码和错误脱敏
9. `test_repository_services.py` / `test_repository_cli.py`
   - direct tree/export 不调用 common-used 列表
   - `--repo-id` 兼容、`--repo` 直达和 batch 混合目标
10. `test_application_repository_selection.py`
   - GUI 直接输入、空列表 fallback、文本输入和真实 repo 标识
11. `test_auth_security.py`
   - Cookie 凭据原子写入、POSIX owner-only 权限与 ACL 失败清理
12. `test_favorite_repository_provider.py` / `test_favorite_repository_client.py`
   - 真实 Book 卡片、marks 中明确 Book action、文档收藏排除、稳定去重和 favorites transport

---

# TEST RESULTS

## Environment prep

- Installed harness editable package:
  - `python -m pip install -e ./agent-harness` (success)
- Installed test dependencies:
  - `python -m pip install pytest pytest-cov` (success)

## CLI verification

1. `cli-anything-yuque --json project info`
   - ✅ Pass, `ok=true`, returns project root/src/harness paths.
2. `cli-anything-yuque --json project paths`
   - ✅ Pass, `ok=true`, returns profile path set.
3. `cli-anything-yuque --json session init --profile default`
   - ✅ Pass, `ok=true`, session initialized/read successfully.
4. `cli-anything-yuque auth status --profile default --json`
   - ✅ Pass, `ok=true`, status available.
5. `cli-anything-yuque repo list --profile default --json`
   - ✅ Pass after login, returns repository array.
6. `cli-anything-yuque export run --repo-id 42252691 --format markdown --all --profile default --json`
   - ✅ Pass, exported docs with success summary.

## JSON error-envelope verification

1. `cli-anything-yuque --json export run --format markdown`
   - ✅ Pass, exit code `2`, JSON error envelope (`usage_error`).
2. `cli-anything-yuque --json export run --repo-id 1`
   - ✅ Pass, exit code `2`, JSON error envelope (`bad_parameter`).

## Pytest execution results

1. `python -m pytest -v --tb=no agent-harness/cli_anything/yuque/tests/test_core.py`
   - ✅ 6 passed
2. `python -m pytest -v --tb=no agent-harness/cli_anything/yuque/tests/test_full_e2e.py`
   - ✅ 3 passed
3. `python -m pytest -v --tb=no agent-harness/cli_anything/yuque/tests/test_subprocess.py`
   - ✅ 3 passed
4. `python -m pytest agent-harness/cli_anything/yuque/tests -q`
   - ✅ 196 passed (2 POSIX-only lock tests skipped on Windows)
5. `python -m pytest agent-harness/cli_anything/yuque/tests -q --cov=src --cov=agent-harness/cli_anything/yuque --cov-branch --cov-report=term-missing`
   - ✅ 196 passed (2 POSIX-only lock tests skipped on Windows)
   - Full source aggregate: 79.02% (test files excluded from the production target)
   - Changed production modules: 83% branch-aware coverage
   - Favorites provider: 84%; favorites service: 91%; production target command covers auth, resolver, browser, CLI, services and validators
8. Non-editable wheel smoke test
   - ✅ `pip wheel ./agent-harness --no-deps` succeeded
   - ✅ installed wheel imports `cli_anything.yuque` and packaged `core` modules
   - Existing CI has no coverage gate
6. Live authenticated browser smoke test
   - ✅ `/api/books` repository lookup, repository-page `appData.book`, catalog, and one Markdown export succeeded
   - ℹ️ `/api/v2/repos/...` returned 401 with browser cookies and is not used by the implementation
   - ℹ️ The test account's only collected repository was also present in `common_used`, so an outside-`common_used` live fixture was unavailable
7. Isolated PyInstaller build
   - ✅ `YuqueExporter.exe` built successfully in a temporary D: drive directory (77 MiB)
8. Live favorites enumeration smoke
   - ✅ 18 explicit Book cards resolved through `/api/books?namespace=...`
   - ✅ `/api/mine/marks?limit=20&offset=0&type=all&q=` returned 11 actions, including 5 explicit Book targets
   - ✅ Document actions and their owning repositories were excluded from repository discovery
   - ℹ️ Current account's 18 collected repositories also appeared in common-used; outside-common live fixture remains unavailable
