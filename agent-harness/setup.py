from setuptools import setup, find_namespace_packages

setup(
    name="cli-anything-yuque",
    version="0.1.0",
    description="Stateful CLI harness for yuque-exporter",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    include_package_data=True,
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