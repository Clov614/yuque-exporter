"""
语雀批量导出工具
================
主程序入口
"""

import sys
import time
from pathlib import Path
from typing import Any

# 添加 src 到路径以便导入 (开发模式)
sys.path.append(str(Path(__file__).parent))

from core.client import YuqueClient, ExportType
from core.auth import YuqueAuth, LoginStatus
from core.models import Repository
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
                ["📚 导出知识库", "👤 账号信息", "⚙️ 设置", "🚪 退出"]
            )
            
            if choice == "📚 导出知识库":
                self.export_flow()
            elif choice == "👤 账号信息":
                self.show_account_info()
            elif choice == "⚙️ 设置":
                UI.info("功能开发中...")
            elif choice == "🚪 退出":
                self.shutdown()
                break

    def export_flow(self):
        """导出流程"""
        selected_repos = self._select_repositories()
        if not selected_repos:
            return

        # Select Format
        format_map = {
            "Markdown (推荐)": ExportType.MARKDOWN,
            "PDF": ExportType.PDF,
            "Word": ExportType.WORD,
            "Lakebook": ExportType.LAKEBOOK
        }
        fmt_choice = UI.ask_choice("选择导出格式:", list(format_map.keys()))
        if fmt_choice not in format_map:
            return
        export_type = format_map[fmt_choice]
        download_images = False
        if export_type == ExportType.MARKDOWN:
            download_images = UI.ask_confirm(
                "是否将 Markdown 中的网络图片下载到本地？", default=False
            )

        # Process each repo
        for repo in selected_repos:
            self.process_repo_export(repo, export_type, download_images=download_images)

    def _require_client(self) -> YuqueClient:
        if self.client is None:
            raise RuntimeError("Yuque client is not initialized")
        return self.client

    def _select_repositories(self) -> list[Repository]:
        """Choose repositories from the common list or a direct reference."""
        source = UI.ask_choice(
            "请选择知识库来源:",
            ["从常用知识库列表选择", "通过 ID / namespace / URL 直接指定"],
        )
        if source == "通过 ID / namespace / URL 直接指定":
            return self._select_direct_repositories()
        if source == "从常用知识库列表选择":
            return self._select_from_common_repositories()
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

    def process_repo_export(
        self,
        repo: Repository,
        export_type: ExportType,
        download_images: bool = False,
    ) -> None:
        """处理单个知识库导出"""
        client = self._require_client()
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
        
        # Begin Export
        UI.info(f"开始导出 {len(target_docs)} 篇文档...")
        
        # 预计算路径映射
        path_map = self._build_path_map(nodes)
        
        success_count = 0
        image_downloaded_count = 0
        image_failed_count = 0
        with UI.create_progress() as progress:
            main_task = progress.add_task(f"导出 [{repo.name}]", total=len(target_docs))
            
            # 创建下载任务 (隐藏，用于显示单个文件进度)
            download_task = progress.add_task("等待下载...", total=None, visible=False)
            
            for doc in target_docs:
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
                
                # Determine extension
                ext = f".{export_type.value}"
                if export_type == ExportType.MARKDOWN:
                    ext = ".md"

                save_path = self.exporter.get_save_path(doc, repo.name, extension=ext, relative_path=relative_dir)

                if url == "EMPTY_DOC":
                    # 创建空文件
                    # Ensure directory exists
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    save_path.touch()
                    if export_type == ExportType.MARKDOWN:
                        # 对于 Markdown，可以写入标题作为元数据，即使内容为空
                        self.exporter.add_metadata(save_path, doc)
                    success_count += 1
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
                            self.exporter.add_metadata(save_path, doc)
                        success_count += 1
                    
                    # 隐藏下载任务
                    progress.update(download_task, visible=False)
                
                progress.advance(main_task)
        
        UI.success(f"[{repo.name}] 导出完成: {success_count}/{len(target_docs)}")
        if download_images:
            UI.info(
                f"图片本地化: 成功 {image_downloaded_count} 张，失败 {image_failed_count} 张"
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
