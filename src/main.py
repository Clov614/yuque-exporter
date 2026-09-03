"""
语雀批量导出工具
================
主程序入口
"""

import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

# 添加 src 到路径以便导入 (开发模式)
sys.path.append(str(Path(__file__).parent))

from core.browser_writer import YuqueBrowserWriter
from core.client import YuqueClient, ExportType
from core.auth import YuqueAuth, LoginStatus
from core.favorite_document import FavoriteDocument
from core.incremental import finalize as finalize_incremental
from core.incremental import plan_incremental, record_exported, stamp_metadata
from core.markdown_input import read_markdown
from core.models import Document, Repository
from core.mutation_errors import MutationError
from core.exporter import DocumentExporter
from core.repository_reference import RepositoryReferenceError
from core.repository_resolver import RepositoryResolutionError
from utils.browser import BrowserManager
from ui.console import UI

class Application:
    def __init__(self) -> None:
        self.browser_manager = BrowserManager()
        self.page: Any | None = None
        self.client: YuqueClient | None = None
        self.auth = YuqueAuth()
        self.exporter = DocumentExporter()
        
    def startup(self):
        """启动流程"""
        try:
            UI.print_banner()
            UI.info("正在初始化浏览器环境...")
            self.page = self.browser_manager.start(headless=True)
            self.client = YuqueClient(self.page)
            self.check_login()
            self.main_menu()
        finally:
            self.shutdown()
        
    def check_login(self):
        """检查并处理登录"""
        status = self.auth.check_login_status(self.page)
        
        if status != LoginStatus.LOGGED_IN:
            UI.warning("检测到未登录或会话已过期")
            choice = UI.ask_choice("请选择操作:", ["登录账号", "退出程序"])
            
            if choice == "登录账号":
                self.perform_login()
            else:
                self.shutdown()
                sys.exit(0)
        else:
            UI.success("已检测到有效会话")

    def perform_login(self):
        """执行登录流程"""
        # 切换到有头模式
        self.page = self.browser_manager.restart_headed()
        self.client = YuqueClient(self.page) # 更新 client 的 tab 引用
        
        if self.client.login():
            UI.success("登录成功！即将切换回后台模式...")
            time.sleep(2)
            # 切换回无头模式
            self.page = self.browser_manager.restart_headless()
            self.auth.load_cookies(self.page) # 关键修复：切换模式后重新注入 cookies
            self.client = YuqueClient(self.page)
        else:
            UI.error("登录失败")
            self.shutdown()
            sys.exit(1)

    def main_menu(self):
        """主菜单循环"""
        while True:
            choice = UI.ask_choice(
                "\n主菜单:",
                [
                    "📚 导出知识库",
                    "📥 导入 Markdown",
                    "🆕 新建知识库",
                    "👤 账号信息",
                    "⚙️ 设置",
                    "🚪 退出",
                ]
            )
            
            if choice == "📚 导出知识库":
                self.export_flow()
            elif choice == "📥 导入 Markdown":
                self.import_markdown_flow()
            elif choice == "🆕 新建知识库":
                self.create_repository_flow()
            elif choice == "👤 账号信息":
                self.show_account_info()
            elif choice == "⚙️ 设置":
                UI.info("功能开发中...")
            elif choice == "🚪 退出":
                self.shutdown()
                break

    def export_flow(self):
        """导出流程"""
        selection = self._select_repositories()
        if isinstance(selection, tuple) and selection[0] == "favorite_docs":
            favorite_docs = selection[1]
            if not favorite_docs:
                return
            options = self._ask_export_options()
            if options is None:
                return
            export_type, download_images, incremental = options
            self.export_favorite_documents(
                favorite_docs,
                export_type,
                download_images=download_images,
                incremental=incremental,
            )
            return
        selected_repos = selection
        if not selected_repos:
            return
        options = self._ask_export_options()
        if options is None:
            return
        export_type, download_images, incremental = options

        # Process each repo
        for repo in selected_repos:
            self.process_repo_export(
                repo,
                export_type,
                download_images=download_images,
                incremental=incremental,
            )

    def _ask_export_options(self) -> tuple[ExportType, bool, bool] | None:
        """选择导出格式与 Markdown 专属选项，取消时返回 None。"""
        format_map = {
            "Markdown (推荐)": ExportType.MARKDOWN,
            "PDF": ExportType.PDF,
            "Word": ExportType.WORD,
            "Lakebook": ExportType.LAKEBOOK
        }
        fmt_choice = UI.ask_choice("选择导出格式:", list(format_map.keys()))
        if fmt_choice not in format_map:
            return None
        export_type = format_map[fmt_choice]
        download_images = False
        incremental = False
        if export_type == ExportType.MARKDOWN:
            download_images = UI.ask_confirm(
                "是否将 Markdown 中的网络图片下载到本地？", default=True
            )
            incremental = UI.ask_confirm(
                "是否只导出有更新的文档（增量导出）？", default=True
            )
        return export_type, download_images, incremental

    def create_repository_flow(self) -> None:
        """Create one private-by-default repository, protocol first."""
        client = self._require_client()
        name = UI.ask_required_text("请输入知识库名称")
        if not name:
            return
        slug = UI.ask_text("请输入知识库 slug（可留空自动生成）")
        description = UI.ask_text("请输入知识库描述（可留空）") or ""
        visibility = UI.ask_choice("选择知识库可见性:", ["私有", "公开"])
        visibility_value = "public" if visibility == "公开" else "private"
        UI.info(
            f"即将创建知识库：{name}；可见性：{visibility or '私有'}。"
            "创建后不会自动导入文档。"
        )
        if not UI.ask_confirm("确认创建知识库？", default=False):
            return
        try:
            repository = client.create_repository(
                name=name,
                slug=slug,
                description=description,
                visibility=visibility_value,
            )
        except RepositoryResolutionError as exc:
            if not self._is_protocol_unsupported(exc):
                UI.error(f"创建知识库失败: {self._describe_write_error(exc)}")
                return
            try:
                namespace = YuqueBrowserWriter(self.page).create_repository(
                    name=name,
                    slug=slug,
                    description=description,
                    visibility=visibility_value,
                )
                repository = client.get_repository(namespace)
            except (MutationError, RepositoryResolutionError) as fallback_exc:
                UI.error(
                    f"创建知识库失败: {self._describe_write_error(fallback_exc)}"
                )
                return
        UI.success(f"知识库创建成功：{repository.name} ({repository.url})")

    def import_markdown_flow(self) -> None:
        """Import one Markdown file into exactly one selected repository."""
        client = self._require_client()
        repository = self._select_single_repository()
        if repository is None:
            return
        source = UI.ask_required_text("请输入 Markdown 文件路径")
        if not source:
            return
        try:
            document = read_markdown(source)
        except MutationError as exc:
            UI.error(f"读取 Markdown 失败: {exc}")
            return
        override_title = UI.ask_text(
            f"请输入文档标题（留空使用：{document.title}）"
        )
        if override_title:
            document = replace(document, title=override_title)
        UI.info(
            f"即将导入 [{repository.name}]：{document.title}，"
            f"{document.byte_length} 字节；本次不会单独上传本地图片或附件。"
        )
        if not UI.ask_confirm("确认导入 Markdown？", default=False):
            return
        try:
            created = client.create_markdown_document(
                repository, document.title, document.body
            )
            url = client.document_url(repository, created)
        except RepositoryResolutionError as exc:
            UI.error(f"导入 Markdown 失败: {self._describe_write_error(exc)}")
            return
        UI.success(f"Markdown 导入成功：{url}")

    @staticmethod
    def _describe_write_error(exc: Exception) -> str:
        cause = exc.__cause__
        if cause is None or str(cause) == str(exc):
            return str(exc)
        return f"{exc}（原因: {cause}）"

    @staticmethod
    def _is_protocol_unsupported(exc: Exception) -> bool:
        """Only fall back to browser UI when the protocol itself is unavailable."""
        message = str(exc).lower()
        return "status 404" in message or "status 405" in message or "not found" in message

    def _require_client(self) -> YuqueClient:
        if self.client is None:
            raise RuntimeError("Yuque client is not initialized")
        return self.client

    def _select_repositories(self) -> list[Repository] | tuple[str, list[FavoriteDocument]]:
        """Choose repositories from the common list, favorites, docs, or a direct reference."""
        source = UI.ask_choice(
            "请选择知识库来源:",
            [
                "从常用知识库列表选择",
                "从收藏知识库列表选择",
                "从收藏文档列表选择",
                "通过 ID / namespace / URL 直接指定",
            ],
        )
        if source == "通过 ID / namespace / URL 直接指定":
            return self._select_direct_repositories()
        if source == "从常用知识库列表选择":
            return self._select_from_common_repositories()
        if source == "从收藏知识库列表选择":
            return self._select_from_favorite_repositories()
        if source == "从收藏文档列表选择":
            return ("favorite_docs", self._select_from_favorite_documents())
        return []

    def _select_from_common_repositories(self) -> list[Repository]:
        client = self._require_client()
        try:
            with UI.create_progress() as progress:
                task = progress.add_task("获取知识库列表...", total=None)
                repositories = client.get_repositories()
                progress.update(task, completed=100, visible=False)
        except RepositoryResolutionError as exc:
            UI.error(f"获取知识库列表失败: {exc}")
            return []

        if not repositories:
            UI.warning("未找到常用知识库")
            if UI.ask_confirm("是否改为直接输入知识库 ID、namespace 或 URL？"):
                return self._select_direct_repositories()
            return []

        UI.show_repos(repositories)
        repo_choices = [
            {
                "name": f"[{index}] {repository.name}",
                "value": repository,
            }
            for index, repository in enumerate(repositories, 1)
        ]
        return UI.ask_checkbox(
            "请选择要导出的知识库 (按空格选择，回车确认):",
            repo_choices,
        )

    def _select_from_favorite_repositories(self) -> list[Repository]:
        client = self._require_client()
        try:
            with UI.create_progress() as progress:
                task = progress.add_task("获取收藏知识库列表...", total=None)
                repositories = client.get_favorite_repositories()
                progress.update(task, completed=100, visible=False)
        except RepositoryResolutionError as exc:
            UI.error(f"获取收藏知识库列表失败: {exc}")
            return []

        if not repositories:
            UI.warning("未识别到收藏知识库；文档收藏不会升级为知识库收藏")
            if UI.ask_confirm("是否改为直接输入知识库 ID、namespace 或 URL？"):
                return self._select_direct_repositories()
            return []

        UI.show_repos(repositories)
        choices = [
            {"name": f"[{index}] {repository.name}", "value": repository}
            for index, repository in enumerate(repositories, 1)
        ]
        return UI.ask_checkbox(
            "请选择要导出的收藏知识库 (按空格选择，回车确认):",
            choices,
        )

    def _select_from_favorite_documents(self) -> list[FavoriteDocument]:
        client = self._require_client()
        try:
            with UI.create_progress() as progress:
                task = progress.add_task("获取收藏文档列表...", total=None)
                documents = client.get_favorite_documents()
                progress.update(task, completed=100, visible=False)
        except RepositoryResolutionError as exc:
            UI.error(f"获取收藏文档列表失败: {exc}")
            return []

        if not documents:
            UI.warning("未识别到收藏文档；可返回主菜单改用知识库来源重新导出")
            return []

        UI.show_favorite_docs(documents)
        choices = [
            {
                "name": f"[{index}] {document.title}（{document.book_display}）",
                "value": document,
            }
            for index, document in enumerate(documents, 1)
        ]
        return UI.ask_checkbox(
            "请选择要导出的收藏文档 (按空格选择，回车确认):",
            choices,
        )

    def _select_direct_repositories(self) -> list[Repository]:
        client = self._require_client()
        selected: tuple[Repository, ...] = ()
        while True:
            value = UI.ask_text("请输入知识库 ID、namespace 或 URL")
            if not value:
                return list(selected)
            try:
                repository = client.get_repository(value)
            except (RepositoryReferenceError, RepositoryResolutionError) as exc:
                UI.error(str(exc))
                if UI.ask_confirm("输入无效，是否重试？"):
                    continue
                return list(selected)

            selected = (*selected, repository)
            if not UI.ask_confirm("是否继续添加另一个知识库？"):
                return list(selected)

    def _select_single_repository(self) -> Repository | None:
        """Choose exactly one repository for Markdown import."""
        source = UI.ask_choice(
            "请选择知识库来源:",
            [
                "从常用知识库列表选择",
                "从收藏知识库列表选择",
                "通过 ID / namespace / URL 直接指定",
            ],
        )
        if source == "通过 ID / namespace / URL 直接指定":
            return self._select_single_direct_repository()
        if source == "从常用知识库列表选择":
            return self._select_single_from_common_repositories()
        if source == "从收藏知识库列表选择":
            return self._select_single_from_favorite_repositories()
        return None

    def _select_single_from_common_repositories(self) -> Repository | None:
        client = self._require_client()
        try:
            with UI.create_progress() as progress:
                task = progress.add_task("获取知识库列表...", total=None)
                repositories = client.get_repositories()
                progress.update(task, completed=100, visible=False)
        except RepositoryResolutionError as exc:
            UI.error(f"获取知识库列表失败: {exc}")
            return None

        if not repositories:
            UI.warning("未找到常用知识库")
            if UI.ask_confirm("是否改为直接输入知识库 ID、namespace 或 URL？"):
                return self._select_single_direct_repository()
            return None

        return self._choose_single_repository(
            repositories, "请选择要导入的知识库 (输入序号回车确认):"
        )

    def _select_single_from_favorite_repositories(self) -> Repository | None:
        client = self._require_client()
        try:
            with UI.create_progress() as progress:
                task = progress.add_task("获取收藏知识库列表...", total=None)
                repositories = client.get_favorite_repositories()
                progress.update(task, completed=100, visible=False)
        except RepositoryResolutionError as exc:
            UI.error(f"获取收藏知识库列表失败: {exc}")
            return None

        if not repositories:
            UI.warning("未识别到收藏知识库；文档收藏不会升级为知识库收藏")
            if UI.ask_confirm("是否改为直接输入知识库 ID、namespace 或 URL？"):
                return self._select_single_direct_repository()
            return None

        return self._choose_single_repository(
            repositories, "请选择要导入的收藏知识库 (输入序号回车确认):"
        )

    def _select_single_direct_repository(self) -> Repository | None:
        client = self._require_client()
        while True:
            value = UI.ask_text("请输入知识库 ID、namespace 或 URL")
            if not value:
                return None
            try:
                return client.get_repository(value)
            except (RepositoryReferenceError, RepositoryResolutionError) as exc:
                UI.error(str(exc))
                if UI.ask_confirm("输入无效，是否重试？"):
                    continue
                return None

    def _choose_single_repository(
        self, repositories: list[Repository], message: str
    ) -> Repository | None:
        UI.show_repos(repositories)
        labels = [
            f"[{index}] {repository.name}"
            for index, repository in enumerate(repositories, 1)
        ]
        choice = UI.ask_choice(message, labels)
        if choice is None:
            return None
        try:
            return repositories[labels.index(choice)]
        except ValueError:
            return None

    def process_repo_export(
        self,
        repo: Repository,
        export_type: ExportType,
        download_images: bool = False,
        incremental: bool = False,
    ) -> None:
        """处理单个知识库导出"""
        client = self._require_client()
        current_login = client._current_user_login()
        if (
            current_login
            and repo.user_login
            and repo.user_login != current_login
        ):
            UI.warning(
                f"[{repo.name}] 为他人知识库 ({repo.user_login})，"
                "官方导出接口可能无权限，失败属预期；"
                "可改导自有库，或等页面解析导出支持。"
            )
        UI.info(f"正在分析知识库: {repo.name}")

        # Get Catalog
        try:
            nodes = client.get_catalog_nodes(repo)
        except RepositoryResolutionError as exc:
            UI.error(f"获取 [{repo.name}] 的目录失败: {exc}")
            return
        if not nodes:
            UI.error(f"无法获取 [{repo.name}] 的目录结构")
            # Fallback to get_documents? No, catalog is better for structure.
            return

        # Group Filtering Option
        export_scope = UI.ask_choice(
            f"关于 [{repo.name}]，您希望导出:",
            ["全部文档", "选择特定分级/文档"]
        )
        
        target_docs = []
        
        if export_scope == "全部文档":
            target_docs = nodes
        else:
            # 构建节点树形展示列表
            # 1. 整理层级关系
            node_map = {n.uuid: n for n in nodes}
            children_map = {}
            roots = []
            
            for node in nodes:
                children_map.setdefault(node.uuid, [])
                if node.parent_uuid and node.parent_uuid in node_map:
                    children_map.setdefault(node.parent_uuid, []).append(node)
                else:
                    roots.append(node)
            
            # 2. 递归生成选项表 (扁平化带缩进)
            choices = []
            
            def add_nodes_to_choices(node_list, level=0):
                # 排序: 标题优先 (TITLE) ? 还是按 default 顺序
                # assuming node_list is already sorted by API or we sort them
                # node_list.sort(key=lambda x: x.id) 
                
                for node in node_list:
                    indent = "  " * level
                    icon = "📂" if node.type == "TITLE" else "📄"
                    display_name = f"{indent}{icon} {node.title}"
                    
                    choices.append({
                        "name": display_name,
                        "value": node,
                        "checked": False
                    })
                    
                    # Process children
                    children = children_map.get(node.uuid, [])
                    if children:
                        add_nodes_to_choices(children, level + 1)

            add_nodes_to_choices(roots)
            
            if not choices:
                UI.warning("该知识库似乎为空")
                return

            # 3. 用户选择
            UI.info("💡 提示: 选择[分组]会自动包含其下所有文档")
            selected_nodes = UI.ask_checkbox(
                "请选择要导出的内容 (支持多选):",
                choices
            )
            
            if not selected_nodes:
                return
                
            # 4. 智能解析: 如果选中了父节点，自动包含所有子孙节点
            # 使用集合避免重复
            final_uuids = set()
            
            def collect_descendants(node):
                final_uuids.add(node.uuid)
                for child in children_map.get(node.uuid, []):
                    collect_descendants(child)
            
            for node in selected_nodes:
                collect_descendants(node)
                
            # 保持原始顺序导出
            target_docs = [n for n in nodes if n.uuid in final_uuids]
        
        if not target_docs:
            UI.warning("未包含任何有效文档")
            return

        path_map = self._build_path_map(nodes)
        self._export_target_docs(
            repo,
            target_docs,
            path_map,
            export_type,
            download_images=download_images,
            incremental=incremental,
            full_catalog_nodes=nodes,
        )

    def _export_target_docs(
        self,
        repo: Repository,
        target_docs: list[Document],
        path_map: dict[str, str],
        export_type: ExportType,
        download_images: bool = False,
        incremental: bool = False,
        full_catalog_nodes: list[Document] | None = None,
    ) -> None:
        """导出已确定的文档集合，复用增量规划与落盘循环。"""
        client = self._require_client()
        # 增量模式仅支持 Markdown（与 CLI --incremental 保持一致）
        if incremental and export_type != ExportType.MARKDOWN:
            UI.warning("增量导出仅支持 Markdown 格式，已切换为全量导出")
            incremental = False

        # Begin Export

        ext = ".md" if export_type == ExportType.MARKDOWN else f".{export_type.value}"
        incremental_plan = plan_incremental(
            client=client,
            exporter=self.exporter,
            repository=repo,
            selected=target_docs,
            path_map=path_map,
            output_dir=self.exporter.output_dir,
            incremental=incremental,
            extension=ext,
        )
        skipped_uuids = incremental_plan.skipped_uuids
        pending_docs = [doc for doc in target_docs if doc.uuid not in skipped_uuids]
        if incremental:
            UI.info(
                f"增量导出：共 {len(target_docs)} 篇，未修改跳过 {len(skipped_uuids)} 篇，"
                f"待导出 {len(pending_docs)} 篇..."
            )
        else:
            UI.info(f"开始导出 {len(target_docs)} 篇文档...")

        success_count = 0
        image_downloaded_count = 0
        image_failed_count = 0
        failed_docs: list[str] = []
        with UI.create_progress() as progress:
            main_task = progress.add_task(
                f"导出 [{repo.name}]", total=len(pending_docs)
            )

            # 创建下载任务 (隐藏，用于显示单个文件进度)
            download_task = progress.add_task("等待下载...", total=None, visible=False)

            for doc in pending_docs:
                progress.update(main_task, description=f"处理: {doc.title}")

                # Calculate relative path
                full_path_str = path_map.get(doc.uuid, "")

                if doc.type == "TITLE":
                    self.exporter.get_save_path(doc, repo.name, relative_path=full_path_str)
                    progress.advance(main_task)
                    continue

                path_parts = full_path_str.split("/")
                relative_dir = "/".join(path_parts[:-1]) if len(path_parts) > 1 else ""

                url = client.export_document(doc, export_type)

                if not url:
                    failed_docs.append(f"{doc.title} (id={doc.id})")
                    UI.warning(f"导出失败: {doc.title} (id={doc.id})，已跳过")
                    progress.advance(main_task)
                    continue

                save_path = self.exporter.get_save_path(doc, repo.name, extension=ext, relative_path=relative_dir)

                if url == "EMPTY_DOC":
                    # 创建空文件
                    # Ensure directory exists
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    save_path.touch()
                    if export_type == ExportType.MARKDOWN:
                        # 对于 Markdown，可以写入标题作为元数据，即使内容为空
                        stamp_metadata(incremental_plan, self.exporter, save_path, doc)
                    success_count += 1
                    record_exported(incremental_plan, doc)
                    progress.advance(main_task)
                    continue

                if url:
                    # 定义回调函数
                    def update_progress(chunk_size, total=None):
                        progress.update(download_task, visible=True, description=f"⬇️ {doc.title[:15]}...")
                        if total:
                            progress.update(download_task, total=total)
                        if chunk_size:
                            progress.advance(download_task, chunk_size)

                    # 重置下载任务
                    progress.reset(download_task, total=None, visible=False)

                    if client.download_file(url, str(save_path), progress_callback=update_progress):
                        if export_type == ExportType.MARKDOWN:
                            if download_images:
                                image_result = self.exporter.localize_images(
                                    save_path, client.download_external_image
                                )
                                image_downloaded_count += image_result.downloaded_count
                                image_failed_count += len(image_result.failed_urls)
                            stamp_metadata(incremental_plan, self.exporter, save_path, doc)
                        success_count += 1
                        record_exported(incremental_plan, doc)
                    else:
                        failed_docs.append(f"{doc.title} (id={doc.id})")
                        UI.warning(f"下载失败: {doc.title} (id={doc.id})，已跳过")

                    # 隐藏下载任务
                    progress.update(download_task, visible=False)

                progress.advance(main_task)

        finalized = finalize_incremental(
            incremental_plan, full_catalog_nodes if full_catalog_nodes is not None else target_docs
        )
        UI.success(f"[{repo.name}] 导出完成: {success_count}/{len(pending_docs)}")
        if failed_docs:
            UI.warning(
                f"失败 {len(failed_docs)} 篇: " + "、".join(failed_docs[:10])
                + ("……" if len(failed_docs) > 10 else "")
            )
        if incremental:
            UI.info(f"未修改跳过: {len(skipped_uuids)} 篇")
            if finalized["stale"]:
                UI.warning(
                    "以下文档在语雀目录中已不存在（本地文件已保留，请自行处理）: "
                    + ", ".join(finalized["stale"])
                )
        if download_images:
            UI.info(
                f"图片本地化: 成功 {image_downloaded_count} 张，失败 {image_failed_count} 张"
            )

    def export_favorite_documents(
        self,
        favorite_docs: list[FavoriteDocument],
        export_type: ExportType,
        download_images: bool = False,
        incremental: bool = False,
    ) -> None:
        """按归属知识库分组导出用户选中的收藏文档。"""
        client = self._require_client()
        groups: dict[int, list[FavoriteDocument]] = {}
        no_book: list[FavoriteDocument] = []
        for favorite in favorite_docs:
            if isinstance(favorite.book_id, int) and favorite.book_id > 0:
                groups.setdefault(favorite.book_id, []).append(favorite)
            else:
                no_book.append(favorite)
        for book_id in sorted(groups):
            self._export_favorite_group(
                groups[book_id],
                export_type,
                download_images=download_images,
                incremental=incremental,
            )
        if no_book:
            UI.warning(
                f"跳过 {len(no_book)} 篇无归属知识库的收藏: "
                + "、".join(doc.title for doc in no_book[:10])
                + ("……" if len(no_book) > 10 else "")
            )

    def _export_favorite_group(
        self,
        favorites: list[FavoriteDocument],
        export_type: ExportType,
        download_images: bool = False,
        incremental: bool = False,
    ) -> None:
        client = self._require_client()
        book_id = favorites[0].book_id
        try:
            repo = client.get_repository(book_id)
        except RepositoryResolutionError as exc:
            UI.error(f"获取收藏归属知识库失败 (book_id={book_id}): {exc}")
            return
        try:
            nodes = client.get_catalog_nodes(repo)
        except RepositoryResolutionError as exc:
            UI.warning(
                f"获取 [{repo.name}] 的目录失败 ({exc})，"
                "改按收藏单篇直接导出（不保留原目录层级）"
            )
            nodes = []
        node_by_id = {
            node.id: node
            for node in nodes
            if node.type == "DOC" and isinstance(node.id, int) and node.id > 0
        }
        path_map = self._build_path_map(nodes)
        target_docs: list[Document] = []
        missing: list[str] = []
        for favorite in favorites:
            node = node_by_id.get(favorite.doc_id)
            if node is not None:
                target_docs.append(node)
                continue
            fallback = favorite.to_document()
            target_docs.append(fallback)
            missing.append(favorite.title)
        if missing:
            UI.info(
                f"[{repo.name}] {len(missing)} 篇收藏在目录中未命中，"
                "已按单篇导出到知识库根目录: " + "、".join(missing[:10])
                + ("……" if len(missing) > 10 else "")
            )
        if not target_docs:
            UI.warning(f"[{repo.name}] 本组无可导出文档")
            return
        self._export_target_docs(
            repo,
            target_docs,
            path_map,
            export_type,
            download_images=download_images,
            incremental=incremental,
            full_catalog_nodes=nodes,
        )

    def _build_path_map(self, nodes):
        """构建 uuid -> full_path string 映射"""
        node_map = {n.uuid: n for n in nodes}
        path_map = {}
        for node in nodes:
            parts = []
            curr = node
            while curr:
                parts.insert(0, curr.title)
                curr = node_map.get(curr.parent_uuid)
            path_map[node.uuid] = "/".join(parts)
        return path_map

    def show_account_info(self):
        info = self.auth.CREDENTIALS_DIR
        UI.info(f"凭证存储路径: {info}")
        # Could add more info check

    def shutdown(self):
        UI.info("正在清理资源...")
        self.browser_manager.quit()
        UI.success("程序已退出")

if __name__ == "__main__":
    try:
        app = Application()
        app.startup()
    except KeyboardInterrupt:
        print("\n用户中断")
    except Exception as e:
        UI.error(f"发生未预期的错误: {e}")
        import traceback
        traceback.print_exc()
