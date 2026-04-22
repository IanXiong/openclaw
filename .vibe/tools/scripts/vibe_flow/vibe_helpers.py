#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Vibe Flow 通用工具函数

功能：
- 提供所有 vibe_flow 脚本共用的工具函数
- 避免代码重复，统一维护

包含：
- 版本号获取
- JSON 文件读写
- 路径查找
- 工作区类型检测
"""

import sys
import io

# 跨平台 Unicode 输出支持 (Windows GBK 兼容)
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import json
import shutil
import subprocess
import logging
from pathlib import Path
from typing import Optional, Any, Callable, Tuple

logger = logging.getLogger("vibe_helpers")


# ============ 跨平台安全输入 ============

def safe_input(prompt: str = "") -> str:
    """
    跨平台安全输入函数
    
    解决 Windows + Python 3.13 + .bat 文件组合下，嵌套 subprocess 时
    input() 无法读取控制台输入的问题。
    
    原理：
    - Windows 下使用 msvcrt.getwch() 直接调用 Windows Console API
    - 其他平台使用标准 input()
    
    Args:
        prompt: 输入提示文本
    
    Returns:
        用户输入的字符串（不含换行符）
    """
    if sys.platform == 'win32':
        import msvcrt
        print(prompt, end='', flush=True)
        chars = []
        while True:
            char = msvcrt.getwch()
            if char in ('\r', '\n'):
                print()  # 换行
                break
            elif char == '\x03':  # Ctrl+C
                raise KeyboardInterrupt
            elif char in ('\x00', '\xe0'):
                # 特殊键前缀（方向键、F1-F12、Delete 等），读取并丢弃后续字节
                msvcrt.getwch()
                continue
            elif char == '\x08':  # Backspace
                if chars:
                    chars.pop()
                    # 回退光标、覆盖字符、再回退光标
                    print('\b \b', end='', flush=True)
            else:
                chars.append(char)
                print(char, end='', flush=True)
        return ''.join(chars)
    else:
        return input(prompt)


# ============ WSL 安全文件复制 ============

def wsl_safe_copytree(src_dir: Path, dst_dir: Path, ignore_func: Callable = None):
    """
    WSL 安全的目录复制函数
    
    shutil.copytree 即使设置了 copy_function=shutil.copy，仍会在复制完成后
    对目录调用 copystat 设置时间戳，这在 WSL 跨文件系统时会失败。
    
    此函数手动遍历目录树，只复制文件内容，不复制任何元数据。
    
    Args:
        src_dir: 源目录路径
        dst_dir: 目标目录路径
        ignore_func: 可选的忽略函数，签名为 (directory: str, contents: list) -> list
                     返回需要忽略的文件/目录名列表
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    
    # 获取需要忽略的项
    contents = [item.name for item in src_dir.iterdir()]
    ignored = set(ignore_func(str(src_dir), contents)) if ignore_func else set()
    
    for item in src_dir.iterdir():
        if item.name in ignored:
            continue
        
        src_path = item
        dst_path = dst_dir / item.name
        
        if item.is_dir():
            wsl_safe_copytree(src_path, dst_path, ignore_func)
        else:
            # 只复制文件内容，不复制任何元数据（权限、时间戳）
            shutil.copyfile(src_path, dst_path)


# ============ Feature 分支/目录命名 ============

def validate_feature_name(name: str) -> Tuple[bool, str]:
    """
    校验 feature 名称格式
    
    规则：
    - 必须以 feature_, feature/ 或 feature- 开头
    - 前缀后必须有内容
    
    Args:
        name: 分支名/需求名
    
    Returns:
        (is_valid, error_message)
    
    Examples:
        >>> validate_feature_name("feature/flash-sale")
        (True, "")
        >>> validate_feature_name("feature-flash-sale")
        (True, "")
        >>> validate_feature_name("flash-sale")
        (False, "分支名格式不符合规范...")
    """
    if not name:
        return False, "名称不能为空"
    
    # 检查是否以 feature_, feature/ 或 feature- 开头，且后面有内容
    if not (name.startswith("feature_") or name.startswith("feature/") or name.startswith("feature-")):
        return False, (
            f"分支名格式不符合规范\n"
            f"   当前: {name}\n"
            f"   要求: 必须以 feature_, feature/ 或 feature- 开头\n"
            f"   示例: feature/flash-sale, feature_user-upgrade, feature-new-module"
        )
    
    # 检查前缀后是否有内容
    suffix = name[8:]  # len("feature_") = len("feature/") = len("feature-") = 8
    if not suffix:
        return False, "feature 名称不能为空（前缀后需要有内容）"
    
    return True, ""


def get_feature_dir_name(branch_name: str) -> str:
    """
    获取 feature 目录名（仅将 / 转换为 _，其他格式保持一致）
    
    Args:
        branch_name: 分支名
    
    Returns:
        目录名（与分支名保持一致，仅 / 转换为 _）
    
    Examples:
        >>> get_feature_dir_name("feature/flash-sale")
        "feature_flash-sale"
        >>> get_feature_dir_name("feature_flash-sale")
        "feature_flash-sale"
        >>> get_feature_dir_name("feature-flash-sale")
        "feature-flash-sale"
    """
    # 仅处理 feature/ 格式（/ 不能作为目录名）
    return branch_name.replace("/", "_")


# ============ 版本号获取 ============

def get_vaf_version() -> str:
    """
    获取 VAF 版本号（统一从 workspace.json 读取）
    
    优先级：
    1. .vibe/workspace.json 中的 vaf_version 字段
    2. 默认值 "2.0.0"
    
    Returns:
        版本号字符串（如 "v2.0.0"）
    """
    workspace_file = Path(".vibe/workspace.json")
    if workspace_file.exists():
        try:
            with open(workspace_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                version = data.get("vaf_version", "2.0.0")
                return f"v{version}" if not version.startswith("v") else version
        except (json.JSONDecodeError, IOError):
            pass
    
    return "v2.0.0"


# ============ JSON 文件操作 ============

def load_json_file(file_path: str) -> dict:
    """
    安全加载 JSON 文件
    
    Args:
        file_path: JSON 文件路径
    
    Returns:
        解析后的字典，失败返回空字典
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return {}


def save_json_file(file_path: str, data: dict, indent: int = 2) -> bool:
    """
    安全保存 JSON 文件
    
    Args:
        file_path: JSON 文件路径
        data: 要保存的数据
        indent: 缩进空格数
    
    Returns:
        是否保存成功
    """
    try:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        return True
    except IOError:
        return False


# ============ 路径查找 ============

def find_project_root() -> Path:
    """
    从当前目录向上查找 .vibe 目录，定位项目根目录
    
    Returns:
        项目根目录的 Path 对象
    
    Raises:
        FileNotFoundError: 未找到 .vibe 目录
    """
    current = Path.cwd()
    while current != current.parent:
        if (current / ".vibe").exists():
            return current
        current = current.parent
    raise FileNotFoundError("❌ 未找到 .vibe 目录，请确认当前在 VAF 项目中")


def find_vaf_root() -> Optional[Path]:
    """
    查找 VAF 框架根目录（包含 AGENTS.md 的目录）
    
    Returns:
        VAF 框架根目录，未找到返回 None
    """
    current = Path.cwd()
    while current != current.parent:
        if (current / "AGENTS.md").exists():
            return current
        current = current.parent
    return None


def resolve_paths(feature: str) -> dict:
    """
    根据 feature 自动推导状态文件路径（工作区类型自动检测）
    
    Args:
        feature: 需求名称
    
    Returns:
        {
            "mode": str,               # "hub" 或 "service"
            "status_path": str,        # hub_status.json 或 phase_status.json
            "services_dir": str,       # .service_status/ 目录（仅 hub 模式）
            "manifest_path": str       # execution_manifest.json 路径
            "hub_status_path": str     # Hub 状态路径（仅 service 模式）
        }
    """
    root = find_project_root()
    mode = load_workspace_type()
    
    if mode == "hub":
        base = root / "features" / feature / "docs"
        return {
            "mode": mode,
            "status_path": str(base / "hub_status.json"),
            "services_dir": str(base / ".service_status"),
            "manifest_path": str(base / "P05_preliminary_design" / "execution_manifest.json")
        }
    else:  # service
        base = root / "docs" / "features" / feature
        
        # 尝试从 manifest_local.json 获取 Hub 路径
        hub_status_path = None
        hub_manifest_path = None
        manifest_local = base / "manifest_local.json"
        if manifest_local.exists():
            try:
                data = load_json_file(str(manifest_local))
                hub_path = data.get("hub_path")
                if hub_path:
                    # 注意：manifest_local.json 中的 feature 可能是分支名 (feature/xxx)
                    # 但这里的 feature 参数是目录名（来自扫描结果），可以直接使用
                    hub_status_path = str(Path(hub_path) / "features" / feature / "docs" / "hub_status.json")
                    hub_manifest_path = str(Path(hub_path) / "features" / feature / "docs" / "P05_preliminary_design" / "execution_manifest.json")
            except Exception:
                pass
        
        return {
            "mode": mode,
            "status_path": str(base / "phase_status.json"),
            "services_dir": None,
            "manifest_path": hub_manifest_path,  # 从 Hub 读取 execution_manifest.json
            "hub_status_path": hub_status_path
        }


# ============ 工作区类型检测 ============

def load_workspace_type() -> str:
    """
    从 .vibe/workspace.json 读取工作区类型
    
    Returns:
        "hub" 或 "service"
    
    Raises:
        FileNotFoundError: 未找到 .vibe 目录
    """
    root = find_project_root()
    ws_file = root / ".vibe" / "workspace.json"
    if ws_file.exists():
        data = load_json_file(str(ws_file))
        return data.get("type", "hub")
    return "hub"


def get_workspace_type() -> str:
    """
    获取工作区类型（安全版本，不抛异常）
    
    Returns:
        "hub" | "service" | "unknown"
    """
    try:
        return load_workspace_type()
    except FileNotFoundError:
        return "unknown"


def load_workspace_config() -> dict:
    """
    加载完整的工作区配置
    
    Returns:
        workspace.json 内容，失败返回空字典
    """
    try:
        root = find_project_root()
        ws_file = root / ".vibe" / "workspace.json"
        if ws_file.exists():
            return load_json_file(str(ws_file))
    except FileNotFoundError:
        pass
    return {}


# ============ 时间格式化 ============

def calc_duration(started_at: str, updated_at: str) -> str:
    """
    计算两个 ISO8601 时间戳之间的耗时
    
    Args:
        started_at: 开始时间 (ISO8601 格式)
        updated_at: 结束时间 (ISO8601 格式)
    
    Returns:
        格式化的耗时字符串，如 "15分钟" 或 "1小时20分钟"
    """
    from datetime import datetime, timezone, timedelta
    
    if not started_at or not updated_at:
        return "--"
    
    try:
        start = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
        end = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
        
        # 处理时区不一致的情况：
        # 如果一个有时区一个没有，统一假设无时区的是本地时间（+08:00）
        local_tz = timezone(timedelta(hours=8))
        
        if start.tzinfo is None and end.tzinfo is not None:
            start = start.replace(tzinfo=local_tz)
        elif start.tzinfo is not None and end.tzinfo is None:
            end = end.replace(tzinfo=local_tz)
        elif start.tzinfo is None and end.tzinfo is None:
            # 两个都没有时区，直接计算
            pass
        
        diff = end - start
        total_minutes = int(diff.total_seconds() / 60)
        
        if total_minutes < 0:
            return "--"  # 时间顺序错误
        elif total_minutes < 1:
            return "<1分钟"
        elif total_minutes < 60:
            return f"{total_minutes}分钟"
        else:
            hours = total_minutes // 60
            minutes = total_minutes % 60
            if minutes == 0:
                return f"{hours}小时"
            return f"{hours}小时{minutes}分钟"
    except Exception:
        return "--"


def format_duration(seconds: float) -> str:
    """
    格式化时长为人类可读格式
    
    Args:
        seconds: 秒数
    
    Returns:
        格式化字符串（如 "2分钟", "1小时30分钟"）
    """
    if seconds < 60:
        return f"{int(seconds)}秒"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes}分钟"
    else:
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        if minutes > 0:
            return f"{hours}小时{minutes}分钟"
        return f"{hours}小时"


# ============ Git 操作 ============

def run_git(repo_path: Path, git_args: list) -> Tuple[bool, str]:
    """
    执行 Git 命令
    
    Args:
        repo_path: Git 仓库路径
        git_args: Git 命令参数列表（不含 'git'）
    
    Returns:
        (success, output): 成功标志和命令输出
    """
    cmd = ["git", "-C", str(repo_path)] + git_args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30
        )
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Git 命令执行超时"
    except Exception as e:
        return False, str(e)


def check_current_branch(repo_path: Path, target_branch: str = None, colors: Any = None) -> bool:
    """
    检查当前分支是否适合创建新分支
    
    逻辑：
    1. 如果当前分支 == 目标分支，跳过检查（用户已在目标分支）
    2. 如果当前分支是 main/master，跳过检查（正常流程）
    3. 否则提示用户确认是否继续
    
    Args:
        repo_path: Git 仓库路径
        target_branch: 目标分支名（用户想要创建/切换的分支）
        colors: 可选的颜色类（需包含 YELLOW, CYAN, RESET 属性）
    
    Returns:
        True: 继续执行
        False: 停止执行
    """
    # 检查是否为 Git 仓库
    if not (repo_path / ".git").exists():
        return True  # 非 Git 仓库，跳过检查
    
    # 获取当前分支
    success, branch = run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if not success:
        logger.warning("⚠️ 无法获取当前分支，跳过分支检查")
        return True
    
    # 如果当前分支 == 目标分支，跳过检查（用户已在目标分支）
    if target_branch and branch == target_branch:
        logger.debug(f"✅ 已在目标分支: {branch}，跳过分支检查")
        return True
    
    # 检查是否为 main/master
    if branch.lower() in ("main", "master"):
        return True
    
    # 非 main/master 分支，提示用户确认
    if colors:
        print(f"\n{colors.YELLOW}⚠️ 当前分支: {branch}{colors.RESET}")
        print(f"   通常建议从 {colors.CYAN}main{colors.RESET} 或 {colors.CYAN}master{colors.RESET} 分支开始初始化")
    else:
        print(f"\n⚠️ 当前分支: {branch}")
        print(f"   通常建议从 main 或 master 分支开始初始化")
    
    confirm = safe_input(f"   是否继续在当前分支执行初始化？(y/n): ").strip().lower()
    
    if confirm == 'y':
        return True
    else:
        if colors:
            print(f"\n{colors.YELLOW}已取消初始化{colors.RESET}")
        else:
            print(f"\n已取消初始化")
        print(f"💡 提示: 请先切换到 main/master 分支后再执行初始化\n")
        return False
