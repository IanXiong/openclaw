#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VAF 埋点模块 - 环境检测

功能：
1. IDE 工具检测（Cursor/Windsurf/VSCode 等）
2. 操作系统检测
3. Git 信息获取
"""

import sys

# Windows 编码兼容：确保 stdout/stderr 使用 UTF-8，避免 emoji 输出时 GBK 编码错误
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import os
import platform
import subprocess
from pathlib import Path
from typing import Optional


def get_os_type() -> str:
    """获取操作系统类型"""
    os_map = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}
    return os_map.get(platform.system(), "Linux")


def get_branch_name() -> str:
    """获取当前 Git 分支名"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _get_parent_process_chain(max_depth: int = 10) -> list:
    """获取父进程链（完整命令行列表）
    
    从当前进程向上追溯，返回父进程的完整命令行列表。
    用于判断当前脚本是由哪个 IDE 启动的。
    
    注意：使用 args= 而非 comm= 以获取完整命令行，
    这样可以识别通过 node 启动的工具（如 micode、codex）。
    """
    chain = []
    pid = os.getpid()
    
    for _ in range(max_depth):
        try:
            # macOS/Linux: 使用 ps 命令获取父进程信息
            # 使用 args= 获取完整命令行，而非 comm=（只有短进程名）
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "ppid=,args="],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=1
            )
            if result.returncode != 0:
                break
                
            output = result.stdout.strip()
            if not output:
                break
                
            parts = output.split(None, 1)
            if len(parts) < 2:
                break
                
            ppid = int(parts[0])
            args = parts[1].strip()
            
            chain.append(args)
            
            if ppid <= 1:  # 到达 init/launchd
                break
            pid = ppid
            
        except Exception:
            break
    
    return chain


def _get_ide_from_process_chain(chain: list) -> str:
    """从进程链中识别 IDE
    
    枚举值: Codex CLI / Cursor / Windsurf / VSCode / IntelliJ IDEA / MiCode / 其他
    
    Args:
        chain: 父进程名称列表
    
    Returns:
        IDE 名称
    """
    for proc_name in chain:
        proc_lower = proc_name.lower()
        
        # Cursor（优先检测，因为 Cursor 基于 VSCode）
        if "cursor" in proc_lower:
            return "Cursor"
        
        # Windsurf
        if "windsurf" in proc_lower:
            return "Windsurf"
        
        # MiCode（小米内部 IDE）
        if "micode" in proc_lower:
            return "MiCode"
        
        # Codex CLI（OpenAI Codex 命令行工具）
        if "codex" in proc_lower:
            return "Codex CLI"
        
        # VSCode（排除 Cursor、Windsurf、MiCode 后）
        if "code" in proc_lower and "cursor" not in proc_lower and "windsurf" not in proc_lower and "micode" not in proc_lower:
            # 避免误匹配其他包含 code 的进程名
            if proc_lower in ("code", "code helper", "code helper (renderer)", "code helper (plugin)"):
                return "VSCode"
            if "/code" in proc_lower or "code.app" in proc_lower:
                return "VSCode"
        
        # JetBrains 系列 → 统一为 IntelliJ IDEA
        jetbrains_keywords = ["idea", "pycharm", "webstorm", "goland", "clion", 
                              "phpstorm", "rubymine", "rider", "datagrip", "jetbrains"]
        for keyword in jetbrains_keywords:
            if keyword in proc_lower:
                return "IntelliJ IDEA"
    
    return "其他"


def get_ide_tool() -> str:
    """检测当前执行脚本的 IDE 工具
    
    检测方法优先级：
    1. 父进程链检测（最可靠，直接从进程树获取）
    2. 环境变量检测（备选，覆盖父进程检测失败的情况）
    
    枚举值: Codex CLI / Cursor / Windsurf / VSCode / IntelliJ IDEA / MiCode / 其他
    """
    
    # 方法1: 父进程链检测（macOS/Linux，最可靠）
    try:
        chain = _get_parent_process_chain()
        if chain:
            result = _get_ide_from_process_chain(chain)
            if result != "其他":
                return result
    except Exception:
        pass
    
    # 方法2: 环境变量检测（Windows 兜底，或进程链未识别时）
    env = os.environ
    if env.get("CURSOR_TRACE_ID") or env.get("CURSOR_SESSION"):
        return "Cursor"
    if env.get("WINDSURF_CASCADE_TERMINAL") or env.get("WINDSURF_SESSION"):
        return "Windsurf"
    if "codex" in env.get("_", "").lower():
        return "Codex CLI"
    if "micode" in env.get("TERM_PROGRAM", "").lower():
        return "MiCode"
    if env.get("TERM_PROGRAM") == "vscode":
        return "VSCode"
    if env.get("IDEA_INITIAL_DIRECTORY"):
        return "IntelliJ IDEA"
    
    return "其他"


def get_git_remote_url_for_path(path: str = None) -> str:
    """获取指定路径的 Git 远程仓库地址
    
    Args:
        path: 目标路径，None 表示当前目录
    
    Returns:
        Git 远程 URL 或空字符串
    """
    try:
        cmd = ["git", "remote", "get-url", "origin"]
        kwargs = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
        if path:
            kwargs["cwd"] = path
        
        output = subprocess.run(cmd, **kwargs)
        if output.returncode == 0:
            return output.stdout.strip()
    except Exception:
        pass
    return ""


def get_larkkit_version() -> str:
    """获取 larkkit 版本（通过 CLI 调用）"""
    import os
    import sys
    
    # 添加 infra 目录到 path 以导入 larkkit_deps
    infra_dir = os.path.join(os.path.dirname(__file__), '..', 'infra')
    if infra_dir not in sys.path:
        sys.path.insert(0, os.path.abspath(infra_dir))
    
    try:
        from larkkit_deps import get_larkkit_version as _get_version
        return _get_version()
    except ImportError:
        return "not_installed"


def get_codex_model() -> str:
    """从 ~/.codex/config.toml 读取 model 配置
    
    Returns:
        model 值，如 "azure_openai/gpt-5.2-codex-9"，读取失败返回空字符串
    """
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.exists():
        return ""
    
    try:
        content = config_path.read_text(encoding='utf-8')
        for line in content.splitlines():
            line = line.strip()
            # 跳过注释行
            if line.startswith('#'):
                continue
            # 匹配 model = "xxx" 格式
            if line.startswith('model') and '=' in line:
                # 排除 model_provider, model_reasoning_effort 等
                key_part = line.split('=')[0].strip()
                if key_part == 'model':
                    value_part = line.split('=', 1)[1].strip()
                    # 去除引号
                    return value_part.strip('"').strip("'")
    except Exception:
        pass
    
    return ""
