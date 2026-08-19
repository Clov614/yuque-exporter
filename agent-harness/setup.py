from pathlib import Path
from shutil import copytree

from setuptools import find_namespace_packages, setup
from setuptools.command.build_py import build_py


src_root = Path(__file__).resolve().parent.parent / "src"


class BuildWithApplicationSources(build_py):
    def run(self):
        super().run()
        for package in ("core", "ui", "utils"):
            copytree(
                src_root / package,
                Path(self.build_lib) / package,
                dirs_exist_ok=True,
            )


setup(
    name="cli-anything-yuque",
    version="0.1.0",
    description="Stateful CLI harness for yuque-exporter",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    include_package_data=True,
    cmdclass={"build_py": BuildWithApplicationSources},
    install_requires=[
        "click>=8.1",
        "requests",
        "DrissionPage>=4.0",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-yuque=cli_anything.yuque.yuque_cli:main",
        ]
    },
)