# 语雀批量导出工具 (Yuque Exporter)

一个优雅、高效的语雀知识库批量导出工具。支持导出为 Markdown、PDF 和 Word 格式。

## 功能

- 📚 **批量导出**: 支持导出整个知识库或特定分组。
- 🔄 **格式支持**: 支持 Markdown (推荐)、PDF、Word 和 Lakebook 格式。
- 🚀 **高效运行**: 默认使用无头模式运行，仅在登录时调用图形界面。
- 🍪 **持久化登录**: 自动保存会话，无需频繁扫描二维码。
- 🌳 **目录保持**: 完美还原语雀知识库的目录层级结构。
- 🖥️ **交互式 CLI**: 简单易用的命令行菜单界面。

## 📥 下载与安装

### 方式一：下载可执行文件 (推荐)
无需安装 Python 环境，开箱即用。

1. **下载**: 前往 [Releases 页面](../../releases) 下载最新的 `YuqueExporter.exe`。
2. **运行**: 直接双击 `YuqueExporter.exe` 即可启动。

### 方式二：源码运行
适合开发者或希望自行构建的用户。

1. **环境**: 确保安装 Python 3.10+。
2. **克隆**:
   ```bash
   git clone https://github.com/Clov614/yuque-exporter.git
   cd yuque-exporter
   ```
3. **依赖**: `pip install -r requirements.txt`
4. **运行**: `python src/main.py`

## 📖 使用指南

### 1. 启动与登录
- 双击运行程序后，将显示交互式菜单。
- 首次使用请选择 **[登录账号]**。程序将自动打开浏览器窗口，您可以选择 **扫码** 或 **验证码** 登录。
- 登录成功后，浏览器会自动关闭，程序将保存您的登录状态 (Cookies)，后续使用无需再次登录。

### 2. 导出知识库
1. 在主菜单选择 **[📚 导出知识库]**。
2. 程序会列出您所有可访问的知识库。
3. **选择知识库**: 使用 `空格键` 选中一个或多个知识库，`回车键` 确认。
4. **选择格式**: 支持 `Markdown` (推荐)、`PDF`、`Word`、`Lakebook`。
5. **选择范围**:
   - **全部文档**: 导出整个知识库。
   - **选择特定分级**: 浏览目录树，按需选择特定文件夹或文档导出。

### 3. 查看结果
导出完成后，文件将保存在程序同级目录下的 `download/` 文件夹中，并保持原有的目录层级结构。

## 构建

如果你想自己构建 EXE 文件：

```bash
pip install pyinstaller
python build.py
```

构建产物将位于 `dist/YuqueExporter.exe`。

## 分支说明：CLI Harness 独立版本

为了支持自动化代理与机器消费，本仓库新增了一个独立改造分支：

- `feat/cli-anything-yuque-harness-release`

该分支包含：

- `agent-harness/` 独立可安装包（`cli-anything-yuque`）
- profile 状态目录与会话管理（`~/.yuque_harness/<profile>/`）
- 统一 JSON envelope 输出与退出码映射
- 分层测试（unit / mocked e2e / subprocess）

如需体验该版本，请切换分支后安装：

```bash
git checkout feat/cli-anything-yuque-harness-release
python -m pip install -e ./agent-harness
```

## 开源协议

MIT License
