# TEST PLAN

## Scope

- Unit: session/profile state handling, output envelope, validators, exit-code mapping, audit write.
- E2E (mocked): full export orchestration without real network/browser.
- Subprocess: installed/module CLI behavior for `--json` output and return codes.

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

---

# TEST RESULTS

## Environment prep

- Installed harness editable package:
  - `python -m pip install -e ./agent-harness` (success)
- Installed test dependency:
  - `python -m pip install pytest` (success)

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
4. `PYTHONPATH=agent-harness python -m pytest agent-harness/cli_anything/yuque/tests -q`
   - ✅ 34 passed
