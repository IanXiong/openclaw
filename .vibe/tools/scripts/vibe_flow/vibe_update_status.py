#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Vibe Flow 状态更新脚本

功能：
- 原子化更新阶段状态
- 自动记录时间戳
- 支持状态转换校验
- 返回结构化的更新结果

使用方式：
    # 开始执行阶段
    python vibe_update_status.py --action start --phase P02 --status <status_file>
    
    # 标记待确认
    python vibe_update_status.py --action pending --phase P02 --status <status_file>
    
    # 确认放行
    python vibe_update_status.py --action approve --phase P02 --status <status_file>

输出：
    JSON 格式的更新结果，供 AI 解析
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
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

# 导入 vibe_helpers 通用工具函数
from vibe_helpers import (
    load_json_file,
    save_json_file,
    find_project_root,
    calc_duration,
    wsl_safe_copytree,
)


# ============ 常量定义 ============

# 有效的状态转换（含幂等规则：当前状态==目标状态时允许）
VALID_TRANSITIONS = {
    "not_started": ["in_progress"],
    "in_progress": ["in_progress", "pending_review"],     # 幂等 + 正常流转
    "pending_review": ["pending_review", "approved", "in_progress"],  # 幂等 + 放行 + 返工
    "approved": ["approved", "in_progress", "pending_review"],  # 幂等 + 重新执行 + 修改后待审
}

# 操作到目标状态的映射
ACTION_TO_STATUS = {
    "start": "in_progress",
    "pending": "pending_review",
    "approve": "approved",
}

# S 阶段产物到 P06 协调目录的映射
PHASE_ARTIFACT_MAP = {
    "S01": {
        "source": "S01_tech_design",
        "target": "P06_coordination/P06_A_scheme_coordination/service_inputs"
    },
    "S05": {
        "source": "S05_integration_plan",
        "target": "P06_coordination/P06_B_test_coordination/service_inputs"
    },
    "S07": {
        "source": "S07_integration_execute",
        "target": "P06_coordination/P06_C_test_acceptance/service_inputs"
    }
}


# ============ 工具函数 ============


def get_workspace_info(status_file: str) -> dict:
    """
    获取工作区信息（类型、hub_path、服务名等）
    
    返回:
        {
            "type": "hub" | "service" | "unknown",
            "hub_path": str | None,
            "service_name": str | None,
            "feature_name": str | None
        }
    """
    result = {
        "type": "unknown",
        "hub_path": None,
        "service_name": None,
        "feature_name": None
    }
    
    # 从 status_file 路径推断项目根目录
    status_path = Path(status_file).resolve()
    root = find_project_root()
    
    # 读取 workspace.json
    workspace_file = root / ".vibe" / "workspace.json"
    if workspace_file.exists():
        ws_data = load_json_file(str(workspace_file))
        result["type"] = ws_data.get("type", "unknown")
    
    # Service 模式：读取 manifest_local.json 获取 hub_path
    # status_file 路径格式: docs/features/<feature>/phase_status.json
    if result["type"] == "service":
        # 从 status_file 路径提取 feature 目录名
        # status_path.parent = docs/features/<feature>
        feature_dir = status_path.parent
        feature_dir_name = feature_dir.name  # 这是目录名（如 feature_flash-sale）
        
        # manifest_local.json 位于 feature 目录下
        manifest_file = feature_dir / "manifest_local.json"
        if manifest_file.exists():
            manifest = load_json_file(str(manifest_file))
            result["hub_path"] = manifest.get("hub_path")
            # 注意：manifest 中的 feature 可能是分支名 (feature/xxx)
            # 但路径拼接需要目录名，所以始终使用从路径提取的目录名
            result["feature_name"] = feature_dir_name
        else:
            result["feature_name"] = feature_dir_name
        
        # 服务名：从项目根目录名称推断
        result["service_name"] = root.name
    
    return result


def get_current_time() -> str:
    """获取当前时间的 ISO8601 格式字符串"""
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def can_transition(current_status: str, target_status: str) -> bool:
    """检查状态转换是否有效"""
    valid_targets = VALID_TRANSITIONS.get(current_status, [])
    return target_status in valid_targets


def init_service_status_files(status_file: str) -> dict:
    """
    P04 放行后，根据 execution_manifest.json 中的服务列表初始化服务状态文件
    
    Args:
        status_file: hub_status.json 的路径
    
    Returns:
        {
            "success": bool,
            "services_initialized": list[str],
            "message": str
        }
    """
    result = {
        "success": False,
        "services_initialized": [],
        "message": ""
    }
    
    status_path = Path(status_file).resolve()
    docs_dir = status_path.parent  # features/<feature>/docs
    
    # 读取 execution_manifest.json
    manifest_file = docs_dir / "P05_preliminary_design" / "execution_manifest.json"
    if not manifest_file.exists():
        result["message"] = f"execution_manifest.json 不存在: {manifest_file}"
        return result
    
    manifest = load_json_file(str(manifest_file))
    if not manifest:
        result["message"] = f"无法加载 execution_manifest.json"
        return result
    
    # 获取服务列表
    services = manifest.get("services", [])
    if not services:
        result["success"] = True
        result["message"] = "无服务需要初始化（单服务模式或无服务）"
        return result
    
    # 创建 .service_status 目录
    service_status_dir = docs_dir / ".service_status"
    service_status_dir.mkdir(parents=True, exist_ok=True)
    
    # 获取当前时间
    now = get_current_time()
    
    # 为每个服务创建状态文件
    initialized = []
    for service in services:
        service_name = service.get("name")
        if not service_name:
            continue
        
        service_status_file = service_status_dir / f"{service_name}.json"
        
        # 如果文件已存在，跳过（不覆盖已有状态）
        if service_status_file.exists():
            initialized.append(service_name)
            continue
        
        # 创建初始状态文件
        service_status = {
            "phases": {},
            "updated_at": now,
            "service": service_name
        }
        
        if save_json_file(str(service_status_file), service_status):
            initialized.append(service_name)
    
    result["success"] = True
    result["services_initialized"] = initialized
    result["message"] = f"已初始化 {len(initialized)} 个服务状态文件: {', '.join(initialized)}"
    
    return result


# ============ 状态更新函数 ============

def update_phase_status(
    status_file: str,
    phase: str,
    action: str,
    comment: str = None,
    token_usage: int = None,
    turns: int = None,
    model: str = None
) -> dict:
    """
    更新阶段状态（支持幂等操作）
    
    Args:
        status_file: 状态文件路径
        phase: 阶段 ID（如 P02, S01）
        action: 操作类型（start, pending, approve）
        comment: 备注
        token_usage: 本阶段预估 token 消耗（可选，已废弃）
        turns: 本阶段对话轮次（可选）
        model: 使用的 AI 模型名称（可选，仅放行时记录）
    
    Note:
        - 幂等规则：当前状态 == 目标状态时静默成功
        - 返工支持：pending_review -> in_progress 允许重新执行
    
    Returns:
        {
            "success": bool,
            "phase": str,
            "previous_status": str,
            "new_status": str,
            "started_at": str | None,
            "updated_at": str,
            "duration": str | None,
            "message": str
        }
    """
    result = {
        "success": False,
        "phase": phase,
        "previous_status": None,
        "new_status": None,
        "started_at": None,
        "updated_at": None,
        "duration": None,
        "message": ""
    }
    
    # 验证操作
    if action not in ACTION_TO_STATUS:
        result["message"] = f"无效操作: {action}，支持的操作: {list(ACTION_TO_STATUS.keys())}"
        return result
    
    target_status = ACTION_TO_STATUS[action]
    
    # 加载状态文件
    status_data = load_json_file(status_file)
    
    # 确保 phases 字段存在
    if "phases" not in status_data:
        status_data["phases"] = {}
    
    # 获取当前阶段状态
    phase_data = status_data["phases"].get(phase, {
        "status": "not_started",
        "started_at": None,
        "updated_at": None,
        "comment": ""
    })
    
    current_status = phase_data.get("status", "not_started")
    result["previous_status"] = current_status
    
    # 检查状态转换是否有效
    if not can_transition(current_status, target_status):
        result["message"] = f"无效状态转换: {current_status} -> {target_status}"
        return result
    
    # 获取当前时间
    now = get_current_time()
    
    # 更新状态
    phase_data["status"] = target_status
    phase_data["updated_at"] = now
    
    # 特殊处理：开始执行时记录 started_at（仅首次）
    if action == "start" and not phase_data.get("started_at"):
        phase_data["started_at"] = now
    
    # 记录执行次数（每次 start 时 +1）
    if action == "start":
        existing_times = phase_data.get("execution_times", 0)
        phase_data["execution_times"] = existing_times + 1
    
    # 更新备注
    if comment:
        phase_data["comment"] = comment
    
    # 记录 token 消耗（仅放行时记录，累加模式，已废弃）
    if action == "approve" and token_usage is not None:
        existing_usage = phase_data.get("token_usage", 0)
        phase_data["token_usage"] = existing_usage + token_usage
    
    # 记录对话轮次（仅放行时记录，累加模式）
    if action == "approve" and turns is not None:
        existing_turns = phase_data.get("conversation_turns", 0)
        phase_data["conversation_turns"] = existing_turns + turns
    
    # 记录使用的 AI 模型（仅放行时记录）
    if action == "approve" and model:
        phase_data["model"] = model
    
    # 保存更新
    status_data["phases"][phase] = phase_data
    status_data["updated_at"] = now
    
    # 保存文件
    if not save_json_file(status_file, status_data):
        result["message"] = f"保存状态文件失败: {status_file}"
        return result
    
    # 构建结果
    result["success"] = True
    result["new_status"] = target_status
    result["started_at"] = phase_data.get("started_at")
    result["updated_at"] = now
    
    # 计算耗时（仅放行时）
    if action == "approve":
        result["duration"] = calc_duration(
            phase_data.get("started_at"),
            now
        )
        result["message"] = f"{phase} 已放行，耗时 {result['duration']}"
        # 放行后执行 Git 提交
        result["next_action"] = {
            "type": "auto_continue",
            "instruction": "执行 Git 提交 (vibe_git_flow.py)"
        }
        
        # P04 放行后，自动初始化服务状态文件
        if phase == "P04":
            init_result = init_service_status_files(status_file)
            result["service_init"] = init_result
            if init_result["success"] and init_result["services_initialized"]:
                result["message"] += f"；{init_result['message']}"
    elif action == "start":
        # 幂等处理：如果状态未变，说明是重复执行
        if current_status == "in_progress":
            result["message"] = f"{phase} 继续执行（幂等）"
        else:
            result["message"] = f"{phase} 开始执行"
        # 开始执行后加载阶段提示词
        result["next_action"] = {
            "type": "auto_continue",
            "instruction": "加载阶段提示词并执行"
        }
    elif action == "pending":
        # 计算当前耗时（供放行确认框显示）
        result["duration"] = calc_duration(
            phase_data.get("started_at"),
            now
        )
        # 幂等处理
        if current_status == "pending_review":
            result["message"] = f"{phase} 已是待确认状态（幂等）"
        else:
            result["message"] = f"{phase} 标记为待确认"
        # 待确认后显示放行确认框
        result["next_action"] = {
            "type": "auto_continue",
            "instruction": "显示放行确认框 (--mode approval)"
        }
    else:
        result["message"] = f"{phase} 状态更新为 {target_status}"
    
    return result


def batch_update_status(
    status_file: str,
    updates: list[dict]
) -> dict:
    """
    批量更新阶段状态
    
    Args:
        status_file: 状态文件路径
        updates: 更新列表，每项包含 phase, action, comment（可选）
    
    Returns:
        {
            "success": bool,
            "results": list[dict],
            "message": str
        }
    """
    results = []
    all_success = True
    
    for update in updates:
        phase = update.get("phase")
        action = update.get("action")
        comment = update.get("comment", "")
        
        if not phase or not action:
            results.append({
                "success": False,
                "phase": phase,
                "message": "缺少 phase 或 action"
            })
            all_success = False
            continue
        
        token_usage = update.get("token_usage")
        conversation_turns = update.get("conversation_turns")
        result = update_phase_status(status_file, phase, action, comment, token_usage, conversation_turns)
        results.append(result)
        
        if not result["success"]:
            all_success = False
    
    return {
        "success": all_success,
        "results": results,
        "message": f"批量更新完成，{sum(1 for r in results if r['success'])}/{len(results)} 成功"
    }


def sync_to_hub(
    service_status_file: str,
    hub_service_status_dir: str,
    service_name: str
) -> dict:
    """
    同步服务状态到 Hub
    
    Args:
        service_status_file: 服务端 phase_status.json 路径
        hub_service_status_dir: Hub 的 .service_status/ 目录路径
        service_name: 服务名称
    
    Returns:
        {
            "success": bool,
            "source": str,
            "target": str,
            "message": str
        }
    """
    result = {
        "success": False,
        "source": service_status_file,
        "target": None,
        "message": ""
    }
    
    # 加载服务状态
    service_status = load_json_file(service_status_file)
    if not service_status:
        result["message"] = f"无法加载服务状态文件: {service_status_file}"
        return result
    
    # 确保目标目录存在
    target_dir = Path(hub_service_status_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # 构建目标文件路径
    target_file = target_dir / f"{service_name}.json"
    result["target"] = str(target_file)
    
    # 更新 updated_at
    service_status["updated_at"] = get_current_time()
    service_status["service"] = service_name
    
    # 保存到 Hub
    if not save_json_file(str(target_file), service_status):
        result["message"] = f"保存到 Hub 失败: {target_file}"
        return result
    
    result["success"] = True
    result["message"] = f"已同步 {service_name} 状态到 Hub"
    
    return result


def sync_artifacts_to_hub(
    service_feature_dir: Path,
    hub_docs_dir: Path,
    phase: str,
    service_name: str
) -> dict:
    """
    同步 S 阶段产物到 Hub 的 P06 协调目录
    
    Args:
        service_feature_dir: 服务端 feature 目录（如 docs/features/<feature>）
        hub_docs_dir: Hub 端 docs 目录（如 features/<feature>/docs）
        phase: 阶段 ID（S01/S05/S07）
        service_name: 服务名称
    
    Returns:
        {
            "success": bool,
            "skipped": bool,  # 非 S01/S05/S07 时跳过
            "source": str,
            "target": str,
            "message": str
        }
    """
    result = {
        "success": False,
        "skipped": False,
        "source": None,
        "target": None,
        "message": ""
    }
    
    # 非 S01/S05/S07 阶段，跳过
    if phase not in PHASE_ARTIFACT_MAP:
        result["success"] = True
        result["skipped"] = True
        result["message"] = f"阶段 {phase} 无需同步产物"
        return result
    
    mapping = PHASE_ARTIFACT_MAP[phase]
    source_dir = service_feature_dir / mapping["source"]
    target_dir = hub_docs_dir / mapping["target"] / service_name
    
    result["source"] = str(source_dir)
    result["target"] = str(target_dir)
    
    # 检查源目录是否存在
    if not source_dir.exists():
        result["message"] = f"源目录不存在: {source_dir}"
        return result
    
    try:
        # 确保目标父目录存在
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        
        # 如果目标已存在，先删除
        if target_dir.exists():
            shutil.rmtree(target_dir)
        
        # 复制整个目录
        # 使用自定义函数避免 WSL 跨文件系统时的权限问题
        wsl_safe_copytree(source_dir, target_dir)
        
        result["success"] = True
        result["message"] = f"已同步 {phase} 产物到 Hub: {target_dir}"
        return result
    except Exception as e:
        result["message"] = f"同步产物失败: {str(e)}"
        return result


def auto_sync_to_hub_if_service(status_file: str, phase: str = None, action: str = None) -> Optional[dict]:
    """
    如果是 Service 模式，自动同步状态和产物到 Hub
    
    Args:
        status_file: 服务端状态文件路径
        phase: 阶段 ID（如 S01），用于判断是否需要同步产物
        action: 操作类型（start/pending/approve），只有 approve 才同步产物
    
    Returns:
        {
            "success": bool,
            "status_sync": dict,      # 状态同步结果
            "artifact_sync": dict,    # 产物同步结果（仅 S01/S05/S07 且 action=approve）
            "message": str
        }
        或 None（非 Service 模式）
    """
    ws_info = get_workspace_info(status_file)
    
    # 非 Service 模式，跳过
    if ws_info["type"] != "service":
        return None
    
    # 缺少必要信息
    hub_path = ws_info.get("hub_path")
    feature_name = ws_info.get("feature_name")
    service_name = ws_info.get("service_name")
    
    if not hub_path or not feature_name or not service_name:
        return {
            "success": False,
            "message": f"缺少同步信息: hub_path={hub_path}, feature={feature_name}, service={service_name}"
        }
    
    result = {
        "success": False,
        "status_sync": None,
        "artifact_sync": None,
        "message": ""
    }
    
    # 1. 同步状态到 Hub
    hub_service_dir = Path(hub_path) / "features" / feature_name / "docs" / ".service_status"
    status_result = sync_to_hub(status_file, str(hub_service_dir), service_name)
    result["status_sync"] = status_result
    
    if not status_result["success"]:
        result["message"] = f"状态同步失败: {status_result['message']}"
        return result
    
    # 2. 同步产物到 Hub（仅 S01/S05/S07 阶段 且 action=approve）
    if phase and phase in PHASE_ARTIFACT_MAP and action == "approve":
        service_feature_dir = Path(status_file).parent  # docs/features/<feature>
        hub_docs_dir = Path(hub_path) / "features" / feature_name / "docs"
        
        artifact_result = sync_artifacts_to_hub(
            service_feature_dir,
            hub_docs_dir,
            phase,
            service_name
        )
        result["artifact_sync"] = artifact_result
        
        if not artifact_result["success"] and not artifact_result.get("skipped"):
            result["message"] = f"产物同步失败: {artifact_result['message']}"
            return result
    
    result["success"] = True
    result["message"] = f"已同步 {service_name} 状态和产物到 Hub"
    return result


# ============ 主函数 ============

def main():
    parser = argparse.ArgumentParser(
        description="Vibe Flow 状态更新工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 开始执行阶段
  python vibe_update_status.py --action start --phase P02 --status hub_status.json
  
  # 标记待确认
  python vibe_update_status.py --action pending --phase P02 --status hub_status.json
  
  # 确认放行
  python vibe_update_status.py --action approve --phase P02 --status hub_status.json
  
  # 同步到 Hub
  python vibe_update_status.py --sync --service-status phase_status.json \\
    --hub-service-dir /path/to/.service_status --service-name xmstore-api
"""
    )
    
    parser.add_argument(
        "--action", "-a",
        choices=["start", "pending", "approve"],
        help="操作类型：start(开始执行), pending(待确认), approve(放行)"
    )
    
    parser.add_argument(
        "--phase", "-p",
        help="阶段 ID（如 P02, S01）"
    )
    
    parser.add_argument(
        "--status", "-s",
        help="状态文件路径"
    )
    
    parser.add_argument(
        "--comment", "-c",
        default="",
        help="备注信息"
    )
    
    parser.add_argument(
        "--token-usage", "-t",
        type=int,
        default=None,
        help="本阶段预估 token 消耗（仅放行时记录，已废弃）"
    )
    
    parser.add_argument(
        "--turns",
        type=int,
        default=None,
        help="本阶段对话轮次（仅放行时记录）"
    )
    
    parser.add_argument(
        "--model",
        default=None,
        help="使用的 AI 模型名称（仅放行时记录）"
    )
    
    parser.add_argument(
        "--sync",
        action="store_true",
        help="同步模式：将服务状态同步到 Hub"
    )
    
    parser.add_argument(
        "--service-status",
        help="服务端 phase_status.json 路径（同步模式用）"
    )
    
    parser.add_argument(
        "--hub-service-dir",
        help="Hub 的 .service_status/ 目录路径（同步模式用）"
    )
    
    parser.add_argument(
        "--service-name",
        help="服务名称（同步模式用）"
    )
    
    parser.add_argument(
        "--batch",
        help="批量更新 JSON 字符串（格式: [{\"phase\":\"P01\",\"action\":\"approve\"}]）"
    )
    
    parser.add_argument(
        "--_internal",
        action="store_true",
        help=argparse.SUPPRESS  # 隐藏参数，仅供 vibe_sync.py 内部调用
    )
    
    args = parser.parse_args()
    
    # 🚨 调用来源检测：禁止直接调用，必须通过 vibe_sync.py
    if not args._internal and not args.sync:
        print(json.dumps({
            "success": False,
            "message": "❌ 禁止直接调用 vibe_update_status.py！请使用 vibe_sync.py",
            "hint": "正确用法: python .vibe/tools/cli/vibe_sync.py report <phase> --action <action> --feature <feature_name>",
            "examples": [
                "python .vibe/tools/cli/vibe_sync.py report S01 --action start --feature my_feature",
                "python .vibe/tools/cli/vibe_sync.py report S01 --action pending --feature my_feature",
                "python .vibe/tools/cli/vibe_sync.py report S01 --action approve --feature my_feature --turns 5"
            ]
        }, ensure_ascii=False, indent=2))
        sys.exit(1)
    
    try:
        # 同步模式
        if args.sync:
            if not all([args.service_status, args.hub_service_dir, args.service_name]):
                print(json.dumps({
                    "success": False,
                    "message": "同步模式需要 --service-status, --hub-service-dir, --service-name"
                }, ensure_ascii=False, indent=2))
                sys.exit(1)
            
            result = sync_to_hub(
                args.service_status,
                args.hub_service_dir,
                args.service_name
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(0 if result["success"] else 1)
        
        # 批量更新模式
        if args.batch:
            if not args.status:
                print(json.dumps({
                    "success": False,
                    "message": "批量更新需要 --status 参数"
                }, ensure_ascii=False, indent=2))
                sys.exit(1)
            
            updates = json.loads(args.batch)
            result = batch_update_status(args.status, updates)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(0 if result["success"] else 1)
        
        # 单阶段更新模式
        if not all([args.action, args.phase, args.status]):
            print(json.dumps({
                "success": False,
                "message": "需要 --action, --phase, --status 参数"
            }, ensure_ascii=False, indent=2))
            sys.exit(1)
        
        result = update_phase_status(
            args.status,
            args.phase,
            args.action,
            args.comment,
            args.token_usage,
            args.turns,
            args.model
        )
        
        # Service 模式：自动同步到 Hub（传入 phase 和 action，只在 approve 时同步产物）
        if result["success"]:
            sync_result = auto_sync_to_hub_if_service(args.status, args.phase, args.action)
            if sync_result:
                result["hub_sync"] = sync_result
        
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0 if result["success"] else 1)
    
    except json.JSONDecodeError as e:
        print(json.dumps({
            "success": False,
            "message": f"JSON 解析错误: {str(e)}"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({
            "success": False,
            "message": f"执行错误: {str(e)}"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()

