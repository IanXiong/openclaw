#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Vibe Startup - 一键启动脚本

合并以下步骤为单次执行：
1. 目录校验（检测是否在 VAF 源码仓）
2. Git 同步（git pull）
3. 需求检测（feature 选择）
4. 菜单渲染

用户交互保持不变，仅减少 AI 工具调用次数。

使用方式：
    python vibe_startup.py [--skip-git] [--json] [--feature <name>]

参数：
    --skip-git    跳过 Git 同步
    --json        菜单输出 JSON 格式（供 AI 解析）
    --verbose     详细模式
    --feature     指定需求名称（跳过交互选择，用于 AI 对话模式）
"""

import sys
import io

# 跨平台 Unicode 输出支持 (Windows GBK 兼容)
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import os
import json
import subprocess
import argparse
from pathlib import Path
from typing import Optional, List, Tuple

# 导入 vibe_helpers 通用工具函数
from vibe_helpers import (
    load_json_file,
    find_project_root,
    get_workspace_type,
)


# ============ 常量 ============

VAF_MARKER_FILES = ["vaf_starter.md"]  # VAF 源码仓标识文件（根目录存在此文件）


# ============ 工具函数 ============


def run_command(cmd: List[str], cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    """执行命令并返回 (返回码, stdout, stderr)"""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "命令超时"
    except Exception as e:
        return 1, "", str(e)


# ============ Step 1: 目录校验 ============

def check_vaf_repo() -> Tuple[bool, str]:
    """
    检测是否在 VAF 源码仓中
    
    Returns:
        (is_vaf_repo, git_root_path)
    """
    # 获取 git 根目录
    code, stdout, _ = run_command(["git", "rev-parse", "--show-toplevel"])
    if code != 0:
        return False, ""
    
    git_root = Path(stdout)
    
    # 检查是否包含 VAF 标识文件
    for marker in VAF_MARKER_FILES:
        if (git_root / marker).exists():
            return True, str(git_root)
    
    return False, str(git_root)


def print_vaf_warning(vaf_path: str):
    """打印 VAF 源码仓警告"""
    print(f"""
⚠️ 当前目录指向 VAF 源码仓 ({vaf_path})

请在需求池 Hub 或服务代码仓的目录下重新运行本提示词：
  /path/to/vibe-xmstore-requirements   # 需求池示例
  /path/to/xmstore-api                 # 服务仓示例

请关闭当前对话，在目标目录重新启动 @vaf_starter。
""")


# ============ Step 2: Git 同步 ============

def is_git_repo() -> bool:
    """检测当前目录是否为 Git 仓库"""
    code, _, _ = run_command(["git", "rev-parse", "--is-inside-work-tree"])
    return code == 0


def git_pull() -> Tuple[bool, str]:
    """
    执行 git pull
    
    Returns:
        (success, message)
    """
    if not is_git_repo():
        return True, "⚠️ 非 Git 仓库，跳过同步"
    
    code, stdout, stderr = run_command(["git", "pull", "--rebase=false"])
    
    if code == 0:
        if "Already up to date" in stdout or "已经是最新" in stdout:
            return True, "✅ 仓库已是最新"
        else:
            return True, f"✅ Git 同步完成"
    else:
        # 非致命错误，继续流程
        return True, f"⚠️ Git 同步失败（非阻断）: {stderr[:100]}"


# ============ Step 3: 需求检测 ============

def get_features_dir() -> Tuple[Optional[Path], str]:
    """获取 features 目录路径"""
    root = find_project_root()
    ws_type = get_workspace_type()
    
    if ws_type == "hub":
        features_dir = root / "features"
        return (features_dir if features_dir.exists() else None, ws_type)
    elif ws_type == "service":
        features_dir = root / "docs" / "features"
        return (features_dir if features_dir.exists() else None, ws_type)
    
    return (None, ws_type)


def scan_features() -> List[str]:
    """扫描并返回所有有效的 feature 名称列表"""
    features_dir, _ = get_features_dir()
    
    if not features_dir or not features_dir.exists():
        return []
    
    features = []
    for item in features_dir.iterdir():
        if item.name.startswith('.'):
            continue
        if item.is_dir():
            features.append(item.name)
    
    return sorted(features)


def prompt_user_selection(features: List[str], max_retries: int = 3) -> Optional[str]:
    """提示用户选择 feature"""
    print("\n🔍 检测到多个需求，请选择：\n")
    for i, name in enumerate(features, 1):
        print(f"  [{i}] {name}")
    print()
    
    retries = 0
    while retries < max_retries:
        try:
            user_input = input(f"请输入编号 [1-{len(features)}]（输入 q 退出）：").strip()
            
            if user_input.lower() == 'q':
                return None
            
            index = int(user_input)
            if 1 <= index <= len(features):
                return features[index - 1]
            else:
                print(f"⚠️ 无效编号，请输入 1-{len(features)} 之间的数字")
                retries += 1
        except ValueError:
            print("⚠️ 无效输入，请输入数字编号")
            retries += 1
    
    print("❌ 多次输入无效，请通过 @vaf_starter.md 重新开始")
    return None


def print_no_feature_hint():
    """打印无 feature 时的提示信息"""
    root = find_project_root()
    ws_type = get_workspace_type()
    
    print("\n❌ 未检测到任何需求\n")
    
    if ws_type == "hub":
        print("请先创建需求：")
        print(f"  python3 {root}/.vibe/tools/scripts/init/vibe_init_hub_feature.py --feature <需求名称>")
    else:
        print("请先在 Hub 工作区创建需求，然后同步到 Service。")
    
    print("\n或通过 @vaf_starter.md 进入交互式流程。\n")


def print_auto_select_hint(feature_name: str):
    """打印自动选择时的提示信息"""
    print(f"\n📌 自动检测到唯一需求：{feature_name}")
    print(f"   └─ 将使用此需求继续执行")
    print()
    print("💡 如需切换，请通过 @vaf_starter.md 重新选择")
    print("────────────────────────────────────────────────")


def print_selected(feature_name: str):
    """打印选择确认信息（关键输出，用于会话上下文识别）"""
    print(f"\n✅ 已选择需求：{feature_name}\n")


def print_workspace_not_initialized():
    """打印工作区未初始化时的提示信息"""
    print("""
❌ 未检测到工作区配置

请先执行初始化命令：

📋 Hub：vaf init hub --feature <需求名称>
💻 Service：vaf init service --hub-path <路径> --feature <需求名称>
""")


def detect_feature(specified_feature: Optional[str] = None, use_json: bool = False) -> Tuple[str, Optional[str], Optional[List[str]]]:
    """
    检测 feature 上下文
    
    Args:
        specified_feature: 指定的需求名称（跳过交互选择）
        use_json: 是否使用 JSON 输出模式
    
    Returns:
        (status, feature_name, features_list)
        status: "success" | "select_required" | "error"
        feature_name: 选中的需求名称（status=success 时有效）
        features_list: 需求列表（status=select_required 时有效）
    """
    ws_type = get_workspace_type()
    
    # 检查工作区类型
    if ws_type == "unknown":
        if use_json:
            print(json.dumps({
                "status": "error",
                "error": "workspace_not_initialized",
                "message": "未检测到工作区配置，请先执行初始化命令"
            }, ensure_ascii=False, indent=2))
        else:
            print_workspace_not_initialized()
        return "error", None, None
    
    # 扫描 features
    features = scan_features()
    
    # 根据数量处理
    if len(features) == 0:
        if use_json:
            print(json.dumps({
                "status": "error",
                "error": "no_features",
                "message": "未检测到任何需求"
            }, ensure_ascii=False, indent=2))
        else:
            print_no_feature_hint()
        return "error", None, None
    
    # 如果指定了需求名称
    if specified_feature:
        if specified_feature in features:
            if not use_json:
                print_selected(specified_feature)
            return "success", specified_feature, None
        else:
            if use_json:
                print(json.dumps({
                    "status": "error",
                    "error": "feature_not_found",
                    "message": f"需求 '{specified_feature}' 不存在",
                    "available_features": features
                }, ensure_ascii=False, indent=2))
            else:
                print(f"❌ 需求 '{specified_feature}' 不存在")
                print(f"可用需求：{', '.join(features)}")
            return "error", None, None
    
    # 单一 feature，自动选择
    if len(features) == 1:
        feature_name = features[0]
        if not use_json:
            print_auto_select_hint(feature_name)
            print_selected(feature_name)
        return "success", feature_name, None
    
    # 多个 feature
    if use_json:
        # JSON 模式：返回选择列表，不调用 input()
        return "select_required", None, features
    else:
        # 交互模式：调用 input() 让用户选择
        selected = prompt_user_selection(features)
        if selected:
            print_selected(selected)
            return "success", selected, None
        else:
            print("❌ 未选择需求，流程终止")
            return "error", None, None


# ============ Step 4: 菜单渲染 ============

def render_menu(feature_name: str, ws_type: str, use_json: bool = False, verbose: bool = False) -> Tuple[bool, str]:
    """
    调用 vibe_render_menu.py 渲染菜单
    
    Returns:
        (success, menu_output)
    """
    root = find_project_root()
    script_path = root / ".vibe" / "tools" / "scripts" / "vibe_flow" / "vibe_render_menu.py"
    
    # 如果 .vibe 下没有，尝试当前仓库的 tools 目录
    if not script_path.exists():
        script_path = root / "tools" / "scripts" / "vibe_flow" / "vibe_render_menu.py"
    
    if not script_path.exists():
        return False, "❌ 找不到 vibe_render_menu.py 脚本"
    
    # 构建命令 - 使用 progress 模式，脚本会自动检测工作区类型
    cmd = [sys.executable, str(script_path), "--mode", "progress", "--feature", feature_name]
    
    if use_json:
        cmd.append("--json")
    
    if verbose:
        cmd.append("--verbose")
    
    # 执行
    code, stdout, stderr = run_command(cmd)
    
    if code == 0:
        return True, stdout
    else:
        return False, f"❌ 菜单渲染失败: {stderr}"


# ============ 主流程 ============

def main():
    parser = argparse.ArgumentParser(
        description="Vibe Startup - 一键启动脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "--skip-git",
        action="store_true",
        help="跳过 Git 同步"
    )
    
    parser.add_argument(
        "--json",
        action="store_true",
        help="菜单输出 JSON 格式（供 AI 解析）"
    )
    
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="详细模式"
    )
    
    parser.add_argument(
        "--feature",
        type=str,
        default=None,
        help="指定需求名称（跳过交互选择，用于 AI 对话模式）"
    )
    
    args = parser.parse_args()
    
    # ========== Step 1: 目录校验 ==========
    is_vaf, vaf_path = check_vaf_repo()
    if is_vaf:
        print_vaf_warning(vaf_path)
        sys.exit(1)
    
    # ========== Step 2: Git 同步（已移至 vibe_render_menu.py progress 模式）==========
    # 注意：git pull 逻辑已移至 vibe_render_menu.py，每次渲染菜单时自动执行
    # Hub 模式：拉取当前仓库
    # Service 模式：只拉取 Hub（通过 hub_path）
    # --skip-git 参数保留但不再生效（兼容旧调用）
    
    # ========== Step 3: 需求检测 ==========
    status, feature_name, features_list = detect_feature(args.feature, args.json)
    
    if status == "error":
        sys.exit(1)
    
    if status == "select_required":
        # 多需求场景，返回选择列表（仅 JSON 模式）
        # 构建选择菜单的渲染模板
        options_text = "\n".join([f"  [{i+1}] {name}" for i, name in enumerate(features_list)])
        render_template = f"""🔍 检测到多个需求，请选择：

{options_text}

请输入编号（如 1）或需求名称："""
        
        print(json.dumps({
            "status": "select_required",
            "features": features_list,
            "message": "检测到多个需求，请选择",
            "render_template": render_template,
            "hint": "请用户选择后，使用 --feature <name> 参数重新调用",
            "next_action": {
                "type": "wait_input",
                "instruction": "等待用户选择需求",
                "handlers": {
                    "number": "使用 --feature <features[n-1]> 重新调用",
                    "name": "使用 --feature <name> 重新调用"
                }
            }
        }, ensure_ascii=False, indent=2))
        sys.exit(0)
    
    # ========== Step 4: 菜单渲染 ==========
    ws_type = get_workspace_type()
    success, menu_output = render_menu(feature_name, ws_type, args.json, args.verbose)
    
    if success:
        # JSON 模式下，包装成完整的成功响应
        if args.json:
            # menu_output 已经是 JSON 格式，直接输出
            print(menu_output)
        else:
            print(menu_output)
        sys.exit(0)
    else:
        print(menu_output)
        sys.exit(1)


if __name__ == "__main__":
    main()