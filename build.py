"""
构建脚本
========
用于将项目打包为 EXE 可执行文件
"""

import PyInstaller.__main__
import shutil
from pathlib import Path

import sys
import io

# 强制 stdout 使用 utf-8 编码，防止 GitHub Actions Windows Runner 报 UnicodeEncodeError
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def build():
    print("🚀 开始构建 Yuque Exporter...")
    
    # 清理旧构建
    dist_dir = Path("dist")
    build_dir = Path("build")
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    if build_dir.exists():
        shutil.rmtree(build_dir)
        
    # 定义 PyInstaller 参数
    args = [
        "src/main.py",                      # 入口文件
        "--name=YuqueExporter",             # 可执行文件名称
        "--onefile",                        # 单文件模式
        "--clean",                          # 清理缓存
        "--noconfirm",                      # 不确认覆盖
        "--console",                        # 显示控制台 (需要交互)
        "--paths=src",                      # 添加 src 到路径
        # "--icon=assets/icon.ico",       # 图标 (如果有)
    ]
    
    # 执行构建
    PyInstaller.__main__.run(args)
    
    print("\n✅ 构建完成！可执行文件位于: dist/YuqueExporter.exe")

if __name__ == "__main__":
    build()
