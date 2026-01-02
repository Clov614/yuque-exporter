"""
命令行交互界面
==============
基于 Rich 和 Questionary 的 UI 封装
"""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
import questionary
from typing import List, Any, Optional

console = Console()

class UI:
    """UI 助手类"""
    
    @staticmethod
    def print_banner():
        console.print(Panel.fit(
            "[bold green]语雀批量导出工具 (Yuque Exporter)[/bold green]\n"
            "[dim]版本: 1.0.0 | 作者: Auto-Lab[/dim]",
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
        return questionary.select(
            message,
            choices=choices,
            use_indicator=True,
            use_shortcuts=True
        ).ask()
    
    @staticmethod
    def ask_checkbox(message: str, choices: List[dict]) -> List[Any]:
        """
        多选框
        choices: [{'name': 'Display', 'value': 'val', 'checked': False}, ...]
        """
        return questionary.checkbox(
            message,
            choices=choices
        ).ask()

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
            TimeRemainingColumn(),
        )
