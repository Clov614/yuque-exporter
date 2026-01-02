"""
文档导出器
==========
负责文件系统操作，保存文档内容
"""

import re
from pathlib import Path
from typing import Optional
from datetime import datetime
from .models import Document

class DocumentExporter:
    """文档导出工具类"""
    
    DEFAULT_OUTPUT_DIR = Path("./yuque_export")
    
    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or self.DEFAULT_OUTPUT_DIR
    
    def get_save_path(self, doc: Document, repo_name: str, extension: str = ".md", relative_path: str = "") -> Path:
        """
        生成并创建保存目录
        Args:
            doc: 文档对象
            repo_name: 知识库名称
            extension: 扩展名
            relative_path: 相对目录路径 (用于保持层级结构)
        """
        # 清理路径名
        safe_repo = self._sanitize_filename(repo_name)
        
        # 基础目录
        save_dir = self.output_dir / safe_repo
        
        # 处理相对路径
        if relative_path:
            parts = [self._sanitize_filename(p) for p in relative_path.split("/") if p]
            save_dir = save_dir.joinpath(*parts)
        
        # 确保目录存在
        save_dir.mkdir(parents=True, exist_ok=True)
        
        # 文件名
        filename = self._sanitize_filename(doc.title) + extension
        return save_dir / filename

    def add_metadata(self, filepath: Path, doc: Document) -> None:
        """为 Markdown 文件添加 Front Matter"""
        if not filepath.exists():
            return
            
        try:
            content = filepath.read_text(encoding='utf-8')
            # 检查是否已经有 metadata (避免重复添加)
            if content.startswith('---'):
                return
                
            metadata = f"""---
title: {doc.title}
url: {doc.slug}
doc_id: {doc.doc_id}
book_id: {doc.book_id}
created_at: {doc.created_at}
updated_at: {doc.updated_at}
exported_at: {datetime.now().isoformat()}
---

"""
            filepath.write_text(metadata + content, encoding='utf-8')
        except Exception as e:
            print(f"⚠️ 添加元数据失败: {e}")

    def _sanitize_filename(self, name: str) -> str:
        """文件名去除非法字符"""
        # 替换 Windows 非法字符 < > : " / \ | ? *
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        # 移除控制字符
        name = re.sub(r'[\x00-\x1f\x7f]', '', name)
        # 移除首尾空格和点
        name = name.strip().strip('.')
        
        if not name:
            name = "Untitled"
            
        return name[:100]  # 限制长度
