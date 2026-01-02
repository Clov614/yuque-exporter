"""
语雀数据模型
============
定义知识库和文档的数据结构
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class Repository:
    """
    知识库模型
    
    对应 API 响应中的 target 字段
    
    Attributes:
        id: 知识库 ID
        name: 知识库名称
        slug: 知识库 slug (用于 URL)
        description: 知识库描述
        doc_count: 文档数量
        user_login: 用户 login (用于构建 URL)
        public: 是否公开 (0=私有, 1=公开)
        cover: 封面图 URL
    """
    id: int
    name: str
    slug: str
    user_login: str
    description: str = ""
    doc_count: int = 0
    public: int = 0
    cover: str = ""
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'Repository':
        """
        从 API 响应构建 Repository 对象
        """
        target = data.get('target', data)
        user = target.get('user', {})
        
        return cls(
            id=target.get('id', 0),
            name=target.get('name', ''),
            slug=target.get('slug', ''),
            user_login=user.get('login', ''),
            description=target.get('description', ''),
            doc_count=target.get('items_count', 0),
            public=target.get('public', 0),
            cover=target.get('cover', ''),
        )
    
    @property
    def url(self) -> str:
        """知识库 URL"""
        return f"https://www.yuque.com/{self.user_login}/{self.slug}"
    
    def __str__(self) -> str:
        visibility = "🌐" if self.public else "🔒"
        return f"{visibility} {self.name} ({self.doc_count} 篇)"


@dataclass
class Document:
    """
    文档模型
    
    Attributes:
        id: 文档 ID
        title: 文档标题
        slug: 文档 slug (用于 URL)
        created_at: 创建时间
        updated_at: 更新时间
        word_count: 字数
        book_id: 所属知识库 ID
    """
    id: int
    title: str
    slug: str
    uuid: str = ""
    parent_uuid: str = ""
    type: str = "DOC"  # "DOC" or "TITLE"
    level: int = 0
    doc_id: int = 0  # 实际文档ID, TITLE类型为0或None
    book_id: int = 0
    created_at: str = ""
    updated_at: str = ""
    word_count: int = 0
    children: List['Document'] = field(default_factory=list) # 用于构建树结构
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'Document':
        """
        从 API 响应构建 Document 对象
        """
        # 兼容 doc_id 字段 (catalog API 用 doc_id, docs API 用 id)
        doc_id = data.get('doc_id') or data.get('id', 0)
        
        return cls(
            id=doc_id,
            doc_id=doc_id,
            title=data.get('title', ''),
            slug=data.get('url', '') or data.get('slug', ''), # catalog API 用 url
            book_id=data.get('book_id', 0),
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
            word_count=data.get('word_count', 0),
            uuid=data.get('uuid', ''),
            parent_uuid=data.get('parent_uuid', ''),
            type=data.get('type', 'DOC'),
            level=data.get('level', 0)
        )
    
    def __str__(self) -> str:
        return f"📄 {self.title}"
