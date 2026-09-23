"""语雀文档收藏模型.

与 :class:`core.models.Document`（知识库目录节点）不同，
``FavoriteDocument`` 描述用户在“收藏”页中收藏的单篇文档，
额外携带归属知识库与收藏时间，供收藏文档导出分支展示与分组使用。
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Document


@dataclass
class FavoriteDocument:
    """单篇收藏文档.

    Attributes:
        doc_id: 文档 ID（导出与增量比对的主键）。
        title: 文档标题。
        slug: 文档 slug（增量 detail 接口的兜底标识）。
        book_id: 归属知识库 ID（分组导出的分组键）。
        book_name: 归属知识库名称（展示用）。
        book_namespace: 归属知识库 namespace（展示用，优先于名称）。
        favorite_time: 收藏时间原文（展示用，可为空）。
        url: 文档完整 URL（展示/溯源用，可为空）。
    """

    doc_id: int
    title: str
    slug: str = ""
    book_id: int = 0
    book_name: str = ""
    book_namespace: str = ""
    favorite_time: str = ""
    url: str = ""

    @property
    def book_display(self) -> str:
        """归属知识库的展示名称（namespace 优先）。"""
        if self.book_namespace:
            return self.book_namespace
        if self.book_name:
            return self.book_name
        if isinstance(self.book_id, int) and self.book_id > 0:
            return f"book_id={self.book_id}"
        return "未知归属"

    def to_document(self) -> Document:
        """转换为可复用现有导出链路的 :class:`Document`。

        合成的 ``uuid`` 保证增量计划的键非空；目录层级缺失时
        落到归属知识库根目录，由调用方按需补 ``path_map``。
        """
        return Document(
            id=self.doc_id,
            doc_id=self.doc_id,
            title=self.title or f"doc-{self.doc_id}",
            slug=self.slug,
            uuid=f"favorite-{self.book_id}-{self.doc_id}",
            book_id=self.book_id,
        )

    def __str__(self) -> str:
        return f"⭐ {self.title}（{self.book_display}）"
