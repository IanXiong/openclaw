#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Vibe Flow 用户输入校验脚本

功能：
- 校验用户选择的阶段编号是否有效
- 检查阶段是否已解锁
- 返回结构化的校验结果

使用方式：
    python vibe_validate_choice.py --choice <user_input> --feature <feature_name>

输出：
    JSON 格式的校验结果，供 AI 解析
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
import argparse
from pathlib import Path
from typing import Optional, Tuple

# 复用 vibe_unlock_checker 的解锁逻辑
from vibe_unlock_checker import (
    check_phase_unlock,
)
from vibe_helpers import find_project_root, load_workspace_type


# ============ 路径解析 ============

def resolve_paths(feature: str) -> Tuple[str, Optional[str], Optional[str], Optional[str], str]:
    """根据 feature 自动推导状态文件路径（内部获取工作区类型）
    
    Returns:
        (status_path, services_dir_path, manifest_path, hub_status_path, mode)
    """
    root = find_project_root()
    mode = load_workspace_type()
    
    if mode == "hub":
        base = root / "features" / feature / "docs"
        return (
            str(base / "hub_status.json"),
            str(base / ".service_status"),
            str(base / "P05_preliminary_design" / "execution_manifest.json"),
            None,
            mode
        )
    else:  # service
        base = root / "docs" / "features" / feature
        
        # 从 manifest_local.json 获取 Hub 路径
        hub_status_path = None
        manifest_path = None
        manifest_local = base / "manifest_local.json"
        if manifest_local.exists():
            try:
                data = load_json_file(str(manifest_local))
                hub_path = data.get("hub_path")
                if hub_path:
                    hub_base = Path(hub_path) / "features" / feature / "docs"
                    hub_status_path = str(hub_base / "hub_status.json")
                    manifest_path = str(hub_base / "P05_preliminary_design" / "execution_manifest.json")
            except Exception:
                pass
        
        return (
            str(base / "phase_status.json"),
            None,
            manifest_path,
            hub_status_path,
            mode
        )


# ============ 阶段定义 ============

P_PHASES = ["P01", "P02", "P03", "P04", "P05", "P06-A", "P06-B", "P06-C"]
S_PHASES = ["S01", "S02", "S03", "S04", "S05", "S06", "S07", "T01"]

P_PHASE_NAMES = {
    "P01": "需求采集",
    "P02": "知识库加载",
    "P03": "PRD评审",
    "P04": "服务分解",
    "P05": "概要设计",
    "P06-A": "方案协调",
    "P06-B": "测试协调",
    "P06-C": "测试验收",
}

S_PHASE_NAMES = {
    "S01": "技术方案设计",
    "S02": "任务拆解",
    "S03": "编码实现",
    "S04": "单元测试",
    "S05": "集成测试计划",
    "S06": "代码审查",
    "S07": "集成测试执行",
    "T01": "E2E测试",
}


# ============ 工具函数 ============

def load_json_file(path: str) -> dict:
    """加载 JSON 文件"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


# check_p_phase_unlock 和 check_s_phase_unlock 已废弃
# 直接使用 vibe_unlock_checker 的 check_phase_unlock(feature, phase) 函数


def get_phase_status_info(phase: str, phases_status: dict) -> dict:
    """获取阶段状态详情"""
    phase_data = phases_status.get(phase, {})
    return {
        "status": phase_data.get("status", "not_started"),
        "started_at": phase_data.get("started_at"),
        "updated_at": phase_data.get("updated_at"),
        "comment": phase_data.get("comment", "")
    }


# ============ 主校验函数 ============

def validate_hub_choice(choice: str, feature: str, hub_status: dict) -> dict:
    """
    校验 Hub 模式用户选择
    
    Args:
        choice: 用户输入
        feature: 需求名称
        hub_status: Hub 状态
    
    Returns:
        {
            "valid": bool,
            "action": "execute" | "view" | "special" | None,
            "phase": str | None,
            "phase_name": str | None,
            "status": str | None,
            "reason": str,
            "is_locked": bool
        }
    """
    result = {
        "valid": False,
        "action": None,
        "phase": None,
        "phase_name": None,
        "status": None,
        "reason": "",
        "is_locked": False
    }
    
    # 特殊命令
    if choice.lower() in ["g", "global"]:
        result["valid"] = True
        result["action"] = "special"
        result["reason"] = "显示全局进度"
        return result
    
    if choice.lower() in ["b", "back"]:
        result["valid"] = True
        result["action"] = "special"
        result["reason"] = "返回需求列表"
        return result
    
    if choice.lower() in ["q", "quit", "exit"]:
        result["valid"] = True
        result["action"] = "special"
        result["reason"] = "退出"
        return result
    
    # 阶段 ID 直接输入（支持 P01, P02 等格式）
    choice_upper = choice.upper()
    
    # 🚨 Hub 模式下禁止执行 S 阶段
    if choice_upper in S_PHASES:
        result["valid"] = False
        result["reason"] = f"❌ {choice_upper} 是 S 阶段，必须在服务代码仓执行，不能在 Hub（需求池）执行。请先切换到服务目录，执行 `vaf init service` 初始化后再启动 {choice_upper}"
        return result
    
    if choice_upper in P_PHASES:
        phase = choice_upper
    else:
        # 数字选择
        try:
            num = int(choice)
        except ValueError:
            result["reason"] = f"无效输入: {choice}，请输入阶段ID(P01/P02等)、数字或命令 (g/b/q)"
            return result
        
        # 检查范围
        if num < 1 or num > len(P_PHASES):
            result["reason"] = f"选择超出范围，请输入 1-{len(P_PHASES)}"
            return result
        
        # 获取阶段
        phase = P_PHASES[num - 1]
    phase_name = P_PHASE_NAMES.get(phase, phase)
    phase_status_info = get_phase_status_info(phase, hub_status.get("phases", {}))
    
    result["phase"] = phase
    result["phase_name"] = phase_name
    result["status"] = phase_status_info["status"]
    
    # 检查解锁状态
    is_unlocked, unlock_reason = check_phase_unlock(feature, phase)
    
    if not is_unlocked:
        result["is_locked"] = True
        result["reason"] = unlock_reason
        return result
    
    # 已解锁，确定操作
    status = phase_status_info["status"]
    
    if status == "approved":
        result["valid"] = True
        result["action"] = "view"
        result["reason"] = f"{phase} 已放行，可查看产出"
    elif status == "pending_review":
        result["valid"] = True
        result["action"] = "execute"
        result["reason"] = f"{phase} 待确认放行"
    else:
        result["valid"] = True
        result["action"] = "execute"
        result["reason"] = f"可执行 {phase}"
    
    return result


def validate_service_choice(choice: str, feature: str, phase_status: dict) -> dict:
    """
    校验 Service 模式用户选择
    
    Args:
        choice: 用户输入
        feature: 需求名称
        phase_status: 服务阶段状态
    
    Returns:
        与 validate_hub_choice 相同的结构
    """
    result = {
        "valid": False,
        "action": None,
        "phase": None,
        "phase_name": None,
        "status": None,
        "reason": "",
        "is_locked": False
    }
    
    # 特殊命令
    if choice.lower() in ["g", "global"]:
        result["valid"] = True
        result["action"] = "special"
        result["reason"] = "显示全局进度"
        return result
    
    if choice.lower() in ["h", "hub"]:
        result["valid"] = True
        result["action"] = "special"
        result["reason"] = "返回 Hub"
        return result
    
    if choice.lower() in ["q", "quit", "exit"]:
        result["valid"] = True
        result["action"] = "special"
        result["reason"] = "退出"
        return result
    
    # 阶段 ID 直接输入（支持 S01, S02, T01 等格式）
    choice_upper = choice.upper()
    
    # 🚨 Service 模式下禁止执行 P 阶段
    if choice_upper in P_PHASES:
        result["valid"] = False
        result["reason"] = f"❌ {choice_upper} 是 P 阶段，必须在 Hub（需求池）执行，不能在服务代码仓执行。请切换到 Hub 目录后再执行 {choice_upper}"
        return result
    
    if choice_upper in S_PHASES:
        phase = choice_upper
    else:
        # 数字选择
        try:
            num = int(choice)
        except ValueError:
            result["reason"] = f"无效输入: {choice}，请输入阶段ID(S01/S02/T01等)、数字或命令 (g/h/q)"
            return result
        
        # 检查范围
        if num < 1 or num > len(S_PHASES):
            result["reason"] = f"选择超出范围，请输入 1-{len(S_PHASES)}"
            return result
        
        # 获取阶段
        phase = S_PHASES[num - 1]
    phase_name = S_PHASE_NAMES.get(phase, phase)
    phases_status = phase_status.get("phases", {})
    phase_status_info = get_phase_status_info(phase, phases_status)
    
    result["phase"] = phase
    result["phase_name"] = phase_name
    result["status"] = phase_status_info["status"]
    
    # 检查解锁状态
    is_unlocked, unlock_reason = check_phase_unlock(feature, phase)
    
    if not is_unlocked:
        result["is_locked"] = True
        result["reason"] = unlock_reason
        return result
    
    # 已解锁，确定操作
    status = phase_status_info["status"]
    
    if status == "approved":
        result["valid"] = True
        result["action"] = "view"
        result["reason"] = f"{phase} 已放行，可查看产出"
    elif status == "pending_review":
        result["valid"] = True
        result["action"] = "execute"
        result["reason"] = f"{phase} 待确认放行"
    else:
        result["valid"] = True
        result["action"] = "execute"
        result["reason"] = f"可执行 {phase}"
    
    return result


# ============ 主函数 ============

def main():
    parser = argparse.ArgumentParser(
        description="Vibe Flow 用户输入校验工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 校验用户选择（自动检测 Hub/Service 模式）
  python vibe_validate_choice.py --choice 2 --feature flash-sale
  
  # 校验 S 阶段选择
  python vibe_validate_choice.py --choice 3 --feature flash-sale
"""
    )
    
    parser.add_argument(
        "--choice", "-c",
        required=True,
        help="用户输入的选择（数字或命令）"
    )
    
    parser.add_argument(
        "--feature", "-f",
        required=True,
        help="需求名称（自动推导状态文件路径和工作区类型）"
    )
    
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式（默认行为，保留用于兼容）"
    )
    
    args = parser.parse_args()
    
    try:
        # 根据 feature 自动推导路径和工作区类型
        status_path, services_dir, manifest_path, hub_status_path, mode = resolve_paths(args.feature)
        status = load_json_file(status_path)
        
        if mode == "hub":
            result = validate_hub_choice(args.choice, args.feature, status)
        else:  # service
            result = validate_service_choice(args.choice, args.feature, status)
        
        # 添加 next_action 指引
        if result.get("valid"):
            if result.get("action") == "execute":
                result["next_action"] = {
                    "type": "auto_continue",
                    "instruction": "执行阶段 (--mode confirm)"
                }
            elif result.get("action") == "view":
                result["next_action"] = {
                    "type": "auto_continue",
                    "instruction": "查看详情 (--mode phase-detail)"
                }
            elif result.get("action") == "special":
                result["next_action"] = {
                    "type": "auto_continue",
                    "instruction": "执行特殊命令"
                }
        else:
            result["next_action"] = {
                "type": "auto_continue",
                "instruction": "提示错误，等待用户重新输入"
            }
        
        # 输出 JSON 结果
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    except Exception as e:
        error_result = {
            "valid": False,
            "action": None,
            "phase": None,
            "reason": f"校验错误: {str(e)}",
            "is_locked": False
        }
        print(json.dumps(error_result, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()

