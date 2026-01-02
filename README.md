# 语雀批量导出工具 (Yuque Exporter)

一个优雅、高效的语雀知识库批量导出工具。支持导出为 Markdown、PDF 和 Word 格式。

## 功能

- 📚 **批量导出**: 支持导出整个知识库或特定分组。
- 🔄 **格式支持**: 支持 Markdown (推荐)、PDF、Word 和 Lakebook 格式。
- 🚀 **高效运行**: 默认使用无头模式运行，仅在登录时调用图形界面。
- 🍪 **持久化登录**: 自动保存会话，无需频繁扫描二维码。
- 🌳 **目录保持**: 完美还原语雀知识库的目录层级结构。
- 🖥️ **交互式 CLI**: 简单易用的命令行菜单界面。

## 安装

### 方式一：直接运行 EXE
从 Release 页面下载最新的 `YuqueExporter.exe`，直接双击运行即可。

### 方式二：源码运行
需要 Python 3.10+ 环境。

1. 克隆仓库
   ```bash
   git clone https://github.com/your-repo/yuque-exporter.git
   cd yuque-exporter
   ```

2. 安装依赖
   ```bash
   pip install -r requirements.txt
   ```

3. 运行
   ```bash
   python src/main.py
   ```

## 使用指南

1. **登录**: 首次运行，程序会提示切换到浏览器进行登录（支持扫码或验证码）。
2. **选择知识库**: 登录成功后，会自动列出所有有权限的知识库。
3. **选择格式**: 选择想要导出的文件格式。
4. **导出**: 程序会自动下载并保存到 `yuque_export/` 目录下。

## 构建

如果你想自己构建 EXE 文件：

```bash
pip install pyinstaller
python build.py
```

构建产物将位于 `dist/YuqueExporter.exe`。

## 开源协议

MIT License
