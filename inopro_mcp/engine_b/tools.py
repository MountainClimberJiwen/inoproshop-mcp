# -*- coding: utf-8 -*-
"""P2 工具组：工程 / POU / 编译。

实现方式：渲染 templates/*.py.j2 → EngineBRunner 送进无界面 InoProShop 执行。
每个调用都是全新 IDE 实例，所以除 project_open/create 外，所有工具都会先把
project_path 指定的工程在脚本里打开（由 _prelude 统一处理）。
"""
from __future__ import annotations

from typing import Any

from .runner import EngineBRunner

runner = EngineBRunner()


def project_open(path: str) -> dict[str, Any]:
    """打开工程（IDE 实例内），返回路径与 Application 存在性。"""
    return runner.execute("project_open.py.j2", "project_open", path=path)


def project_create(name: str, directory: str) -> dict[str, Any]:
    """创建新工程（会先关闭当前打开的工程）。"""
    return runner.execute("project_create.py.j2", "project_create",
                          name=name, directory=directory)


def project_save(project_path: str) -> dict[str, Any]:
    """保存工程（优先主线程执行）。"""
    return runner.execute("project_save.py.j2", "project_save",
                          project_path=project_path)


def project_close(project_path: str, save: bool = True) -> dict[str, Any]:
    """关闭工程，可选先保存。"""
    return runner.execute("project_close.py.j2", "project_close",
                          project_path=project_path, save=save)


def pou_list(project_path: str) -> dict[str, Any]:
    """列出工程所有 POU。"""
    return runner.execute("pou_list.py.j2", "pou_list", project_path=project_path)


def pou_read(project_path: str, pou_name: str) -> dict[str, Any]:
    """读取 POU 声明区+实现区全文。"""
    return runner.execute("pou_read.py.j2", "pou_read",
                          project_path=project_path, pou_name=pou_name)


def pou_write(project_path: str, pou_name: str, declaration: str | None = None,
              implementation: str | None = None) -> dict[str, Any]:
    """增量写 POU：只替换提供的部分（declaration / implementation）。"""
    return runner.execute("pou_write.py.j2", "pou_write",
                          project_path=project_path, pou_name=pou_name,
                          declaration=declaration, implementation=implementation)


def pou_create(project_path: str, pou_name: str, pou_type: str = "program",
               language: str = "st") -> dict[str, Any]:
    """新建 POU。program/functionblock/function；语言目前仅 st 经过验证。"""
    return runner.execute("pou_create.py.j2", "pou_create",
                          project_path=project_path, pou_name=pou_name,
                          pou_type=pou_type, language=language)


def pou_delete(project_path: str, pou_name: str) -> dict[str, Any]:
    """删除 POU。"""
    return runner.execute("pou_delete.py.j2", "pou_delete",
                          project_path=project_path, pou_name=pou_name)


def build(project_path: str, rebuild: bool = False) -> dict[str, Any]:
    """编译工程，返回 errors/warnings 结构化诊断。"""
    return runner.execute("build.py.j2", "build",
                          project_path=project_path, rebuild=rebuild)
