"""
命令行交互界面
==============
基于 Rich 和 Questionary 的 UI 封装
"""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import (
    Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn,
    TransferSpeedColumn, FileSizeColumn, DownloadColumn
)
import questionary
from typing import List, Any, Optional

console = Console()

class UI:
    """UI 助手类"""
    
    @staticmethod
    def print_banner():
        console.print(Panel.fit(
            "[bold green]语雀批量导出工具 (Yuque Exporter)[/bold green]\n"
            "[dim]版本: 1.0.0 | 作者: Clov614[/dim]",
            border_style="green"
        ))
    
    @staticmethod
    def warning(msg: str):
        console.print(f"[bold yellow]⚠️ {msg}[/bold yellow]")
        
    @staticmethod
    def error(msg: str):
        console.print(f"[bold red]❌ {msg}[/bold red]")
        
    @staticmethod
    def success(msg: str):
        console.print(f"[bold green]✅ {msg}[/bold green]")
        
    @staticmethod
    def info(msg: str):
        console.print(f"[blue]ℹ️ {msg}[/blue]")

    @staticmethod
    def ask_choice(message: str, choices: List[str]) -> Optional[str]:
        try:
            return questionary.select(
                message,
                choices=choices,
                use_indicator=True,
                use_shortcuts=True
            ).ask()
        except Exception:
            # Fallback for non-interactive consoles (e.g. PyCharm Run)
            console.print(f"\n[bold]{message}[/bold]")
            for i, choice in enumerate(choices, 1):
                console.print(f"  {i}. {choice}")
            
            while True:
                try:
                    user_input = input("请输入序号: ").strip()
                    idx = int(user_input) - 1
                    if 0 <= idx < len(choices):
                        return choices[idx]
                    console.print("[red]❌ 无效序号，请重试[/red]")
                except ValueError:
                    console.print("[red]❌ 请输入数字[/red]")
                except (KeyboardInterrupt, EOFError):
                    return None

    @staticmethod
    def ask_checkbox(message: str, choices: List[dict]) -> List[Any]:
        """
        多选框
        choices: [{'name': 'Display', 'value': 'val', 'checked': False}, ...]
        """
        try:
            return questionary.checkbox(
                message,
                choices=choices
            ).ask()
        except Exception:
            # Fallback for non-interactive consoles
            console.print(f"\n[bold]{message}[/bold]")
            value_map = {}
            for i, item in enumerate(choices, 1):
                console.print(f"  {i}. {item['name']}")
                value_map[i] = item['value']
            
            console.print("[dim]提示: 输入序号列表，用逗号分隔 (例如 1,3)[/dim]")
            
            while True:
                try:
                    user_input = input("请输入: ").strip()
                    if not user_input:
                        return []
                        
                    indices = [int(x.strip()) for x in user_input.split(",") if x.strip()]
                    selected = []
                    for idx in indices:
                        if idx in value_map:
                            selected.append(value_map[idx])
                            
                    if selected:
                        return selected
                    console.print("[red]❌ 未选择有效内容[/red]")
                    
                except ValueError:
                    console.print("[red]❌ 格式错误，请输入数字[/red]")
                except (KeyboardInterrupt, EOFError):
                    return []

    @staticmethod
    def show_repos(repos: List[Any]):
        table = Table(title="📚 知识库列表")
        table.add_column("ID", style="dim", width=6)
        table.add_column("名称", style="cyan")
        table.add_column("文档数", justify="right")
        table.add_column("状态", justify="center")
        
        for idx, repo in enumerate(repos, 1):
            visibility = "[green]公开[/green]" if repo.public else "[yellow]私有[/yellow]"
            table.add_row(str(idx), repo.name, str(repo.doc_count), visibility)
            
        console.print(table)
        
    @staticmethod
    def create_progress():
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            "•",
            DownloadColumn(),
            "•",
            TransferSpeedColumn(),
            "•",
            TimeRemainingColumn(),
        )
