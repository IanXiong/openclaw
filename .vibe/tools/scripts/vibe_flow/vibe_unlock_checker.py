#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Vibe Flow 阶段解锁检查脚本

功能：
- 检查所有阶段的解锁状态
- 输出详细的解锁条件满足情况
- 用于 P06 系列阶段的复杂依赖判断
- 自动从 execution_manifest.json 读取模式和 P06 门禁状态

使用方式：
    # Hub 模式 - 检查所有 P 阶段解锁状态
    python vibe_unlock_checker.py --mode hub --feature <feature_name>
    
    # Service 模式 - 检查所有 S 阶段解锁状态
    python vibe_unlock_checker.py --mode service --feature <feature_name>
    
    # 检查单个阶段
    python vibe_unlock_checker.py --mode hub --feature <feature_name> --phase P06-A

输出：
    JSON 格式的解锁检查结果
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

# 导入 vibe_helpers 通用工具函数
from vibe_helpers import (
    load_json_file,
    find_project_root,
    load_workspace_type,
    resolve_paths,
)


# ============ 阶段定义 ============

P_PHASES = ["P01", "P02", "P03", "P04", "P05", "P06-A", "P06-B", "P06-C"]
S_PHASES = ["S01", "S02", "S03", "S04", "S05", "S06", "S07", "T01"]


# ============ 路径推导函数 ============

def get_hub_manifest_path(feature: str) -> Optional[str]:
    """
    Service 模式下，通过 manifest_local.json 获取 Hub 的 execution_manifest.json 路径
    
    Args:
        feature: 需求目录名（如 feature_flash-sale，来自目录扫描结果）
    
    Returns:
        Hub 的 manifest 路径，或 None
    """
    try:
        root = find_project_root()
        ws_type = load_workspace_type()
        
        if ws_type == "service":
            manifest_local = root / "docs" / "features" / feature / "manifest_local.json"
            if manifest_local.exists():
                data = json.loads(manifest_local.read_text(encoding='utf-8'))
                hub_path = data.get("hub_path")
                if hub_path:
                    return str(Path(hub_path) / "features" / feature / "docs" / "P05_preliminary_design" / "execution_manifest.json")
    except Exception:
        pass
    return None


def _load_p06_flags(manifest_path: str, hub_status: dict = None) -> dict:
    """
    从 execution_manifest.json 和 hub_status 读取 P06 门禁标记
    
    Args:
        manifest_path: execution_manifest.json 路径
        hub_status: Hub 状态（用于读取 P06-A/B/C 阶段状态）
    
    Returns:
        {
            "is_multi_mode": bool,      # 是否多服务模式
            "p06a_approved": bool,      # P06-A 已放行（S02 依赖）
            "p06b_approved": bool,      # P06-B 已放行（S06 依赖）
            "p06c_approved": bool       # P06-C 已放行（T01 依赖）
        }
    """
    manifest = load_json_file(manifest_path)
    
    # P06-A/B/C 状态从 hub_status 读取（阶段状态），而不是从 manifest 的 all_sXX_approved 字段
    p06a_approved = False
    p06b_approved = False
    p06c_approved = False
    if hub_status:
        hub_phases = hub_status.get("phases", {})
        p06a_approved = hub_phases.get("P06-A", {}).get("status") == "approved"
        p06b_approved = hub_phases.get("P06-B", {}).get("status") == "approved"
        p06c_approved = hub_phases.get("P06-C", {}).get("status") == "approved"
    
    return {
        "is_multi_mode": manifest.get("mode") == "multi",
        "p06a_approved": p06a_approved,
        "p06b_approved": p06b_approved,
        "p06c_approved": p06c_approved
    }


# ============ 工具函数 ============

def load_service_statuses(services_dir: str) -> list[dict]:
    """加载所有服务状态文件"""
    services_path = Path(services_dir)
    statuses = []
    
    if not services_path.exists():
        return statuses
    
    for json_file in services_path.glob("*.json"):
        status = load_json_file(str(json_file))
        if status:
            # 从文件名推断服务名
            if "service" not in status:
                status["service"] = json_file.stem
            statuses.append(status)
    
    return statuses


# ============ P 阶段解锁检查 ============

def _check_p_phase_unlock_detail(
    phase: str, 
    hub_status: dict, 
    service_statuses: list[dict],
    manifest: dict = None
) -> dict:
    """
    详细检查 P 阶段解锁条件
    
    Args:
        phase: 阶段 ID
        hub_status: Hub 状态
        service_statuses: 服务状态列表（来自 .service_status/ 目录）
        manifest: execution_manifest.json 内容（用于获取服务列表）
    
    Returns:
        {
            "phase": str,
            "unlocked": bool,
            "conditions": [
                {"name": str, "satisfied": bool, "detail": str}
            ],
            "blocking_reason": str | None
        }
    """
    phases = hub_status.get("phases", {})
    manifest = manifest or {}
    # 从 manifest 读取 mode（P04 产出），默认为 single
    mode = manifest.get("mode", "single")
    
    def is_approved(p: str) -> bool:
        return phases.get(p, {}).get("status") == "approved"
    
    def get_service_phase_status(service_status: dict, s_phase: str) -> str:
        return service_status.get("phases", {}).get(s_phase, {}).get("status", "not_started")
    
    def get_expected_services() -> list[str]:
        """从 manifest 获取预期的服务列表"""
        services = manifest.get("services_multi_mode", [])
        if not services:
            services = manifest.get("services", [])
        # 兼容 name 和 service_name 两种字段名
        result = []
        for s in services:
            if isinstance(s, dict):
                name = s.get("name") or s.get("service_name") or str(s)
                result.append(name)
            else:
                result.append(s)
        return result
    
    def get_service_status_by_name(service_name: str) -> dict:
        """根据服务名获取服务状态"""
        for s in service_statuses:
            if s.get("service") == service_name:
                return s
        return {}
    
    result = {
        "phase": phase,
        "unlocked": False,
        "conditions": [],
        "blocking_reason": None
    }
    
    # P01: 默认解锁
    if phase == "P01":
        result["unlocked"] = True
        result["conditions"] = [
            {"name": "默认解锁", "satisfied": True, "detail": "P01 无前置条件"}
        ]
        return result
    
    # P02: P01 approved
    if phase == "P02":
        p01_approved = is_approved("P01")
        result["conditions"] = [
            {"name": "P01 已放行", "satisfied": p01_approved, "detail": f"P01 状态: {phases.get('P01', {}).get('status', 'not_started')}"}
        ]
        result["unlocked"] = p01_approved
        if not p01_approved:
            result["blocking_reason"] = "需先完成 P01 需求采集"
        return result
    
    # P03: P02 approved
    if phase == "P03":
        p02_approved = is_approved("P02")
        result["conditions"] = [
            {"name": "P02 已放行", "satisfied": p02_approved, "detail": f"P02 状态: {phases.get('P02', {}).get('status', 'not_started')}"}
        ]
        result["unlocked"] = p02_approved
        if not p02_approved:
            result["blocking_reason"] = "需先完成 P02 上下文分析"
        return result
    
    # P04: P03 approved
    if phase == "P04":
        p03_approved = is_approved("P03")
        result["conditions"] = [
            {"name": "P03 已放行", "satisfied": p03_approved, "detail": f"P03 状态: {phases.get('P03', {}).get('status', 'not_started')}"}
        ]
        result["unlocked"] = p03_approved
        if not p03_approved:
            result["blocking_reason"] = "需先完成 P03 PRD评审"
        return result
    
    # P05: P04 approved（单服务/多服务都需要执行）
    if phase == "P05":
        p04_approved = is_approved("P04")
        
        result["conditions"] = [
            {"name": "P04 已放行", "satisfied": p04_approved, "detail": f"P04 状态: {phases.get('P04', {}).get('status', 'not_started')}"}
        ]
        
        if not p04_approved:
            result["blocking_reason"] = "需先完成 P04 服务分解"
        else:
            result["unlocked"] = True
        
        return result
    
    # P06-A: 仅多服务模式 + P05 approved + 所有服务 S01 approved
    if phase == "P06-A":
        # 单服务模式不需要 P06-A
        if mode != "multi":
            result["conditions"] = [
                {"name": "多服务模式", "satisfied": False, "detail": f"当前模式: {mode}"}
            ]
            result["blocking_reason"] = "单服务模式无需 P06-A 协调阶段"
            return result
        
        p05_approved = is_approved("P05")
        
        # 从 manifest 获取预期的服务列表
        expected_services = get_expected_services()
        
        service_s01_status = []
        all_s01_approved = True
        missing_services = []
        
        # 如果没有预期服务列表，则未解锁
        if not expected_services:
            all_s01_approved = False
            missing_services = ["未找到服务列表"]
        else:
            # 遍历预期服务列表，检查每个服务的 S01 状态
            for service_name in expected_services:
                service_status = get_service_status_by_name(service_name)
                s01_status = get_service_phase_status(service_status, "S01") if service_status else "not_started"
                s01_approved = s01_status == "approved"
                
                service_s01_status.append({
                    "service": service_name,
                    "status": s01_status,
                    "approved": s01_approved
                })
                
                if not s01_approved:
                    all_s01_approved = False
                    missing_services.append(service_name)
        
        result["conditions"] = [
            {"name": "P05 已放行", "satisfied": p05_approved, "detail": f"P05 状态: {phases.get('P05', {}).get('status', 'not_started')}"},
            {"name": "所有服务 S01 已放行", "satisfied": all_s01_approved, "detail": json.dumps(service_s01_status, ensure_ascii=False)}
        ]
        
        if not p05_approved:
            result["blocking_reason"] = "需先完成 P05 概要设计"
        elif not all_s01_approved:
            result["blocking_reason"] = f"等待服务 S01 完成: {', '.join(missing_services)}"
        else:
            result["unlocked"] = True
        
        return result
    
    # P06-B: 仅多服务模式 + P06-A approved + 所有服务 S05 approved
    if phase == "P06-B":
        # 单服务模式不需要 P06-B
        if mode != "multi":
            result["conditions"] = [
                {"name": "多服务模式", "satisfied": False, "detail": f"当前模式: {mode}"}
            ]
            result["blocking_reason"] = "单服务模式无需 P06-B 协调阶段"
            return result
        
        p06a_approved = is_approved("P06-A")
        
        # 从 manifest 获取预期的服务列表
        expected_services = get_expected_services()
        
        service_s05_status = []
        all_s05_approved = True
        missing_services = []
        
        # 如果没有预期服务列表，则未解锁
        if not expected_services:
            all_s05_approved = False
            missing_services = ["未找到服务列表"]
        else:
            # 遍历预期服务列表，检查每个服务的 S05 状态
            for service_name in expected_services:
                service_status = get_service_status_by_name(service_name)
                s05_status = get_service_phase_status(service_status, "S05") if service_status else "not_started"
                s05_approved = s05_status == "approved"
                
                service_s05_status.append({
                    "service": service_name,
                    "status": s05_status,
                    "approved": s05_approved
                })
                
                if not s05_approved:
                    all_s05_approved = False
                    missing_services.append(service_name)
        
        result["conditions"] = [
            {"name": "P06-A 已放行", "satisfied": p06a_approved, "detail": f"P06-A 状态: {phases.get('P06-A', {}).get('status', 'not_started')}"},
            {"name": "所有服务 S05 已放行", "satisfied": all_s05_approved, "detail": json.dumps(service_s05_status, ensure_ascii=False)}
        ]
        
        if not p06a_approved:
            result["blocking_reason"] = "需先完成 P06-A 方案协调"
        elif not all_s05_approved:
            result["blocking_reason"] = f"等待服务 S05 完成: {', '.join(missing_services)}"
        else:
            result["unlocked"] = True
        
        return result
    
    # P06-C: 仅多服务模式 + P06-B approved + 所有服务 S07 approved
    if phase == "P06-C":
        # 单服务模式不需要 P06-C
        if mode != "multi":
            result["conditions"] = [
                {"name": "多服务模式", "satisfied": False, "detail": f"当前模式: {mode}"}
            ]
            result["blocking_reason"] = "单服务模式无需 P06-C 协调阶段"
            return result
        
        p06b_approved = is_approved("P06-B")
        
        # 从 manifest 获取预期的服务列表
        expected_services = get_expected_services()
        
        service_s07_status = []
        all_s07_approved = True
        missing_services = []
        
        # 如果没有预期服务列表，则未解锁
        if not expected_services:
            all_s07_approved = False
            missing_services = ["未找到服务列表"]
        else:
            # 遍历预期服务列表，检查每个服务的 S07 状态
            for service_name in expected_services:
                service_status = get_service_status_by_name(service_name)
                s07_status = get_service_phase_status(service_status, "S07") if service_status else "not_started"
                s07_approved = s07_status == "approved"
                
                service_s07_status.append({
                    "service": service_name,
                    "status": s07_status,
                    "approved": s07_approved
                })
                
                if not s07_approved:
                    all_s07_approved = False
                    missing_services.append(service_name)
        
        result["conditions"] = [
            {"name": "P06-B 已放行", "satisfied": p06b_approved, "detail": f"P06-B 状态: {phases.get('P06-B', {}).get('status', 'not_started')}"},
            {"name": "所有服务 S07 已放行", "satisfied": all_s07_approved, "detail": json.dumps(service_s07_status, ensure_ascii=False)}
        ]
        
        if not p06b_approved:
            result["blocking_reason"] = "需先完成 P06-B 测试协调"
        elif not all_s07_approved:
            result["blocking_reason"] = f"等待服务 S07 完成: {', '.join(missing_services)}"
        else:
            result["unlocked"] = True
        
        return result
    
    # 未知阶段
    result["blocking_reason"] = f"未知阶段: {phase}"
    return result


def _check_all_p_phases(hub_status: dict, service_statuses: list[dict], manifest: dict = None) -> dict:
    """
    检查所有 P 阶段的解锁状态
    
    Args:
        hub_status: Hub 状态
        service_statuses: 服务状态列表
        manifest: execution_manifest.json 内容
    
    Returns:
        {
            "mode": str,
            "service_count": int,
            "phases": {
                "P01": {...},
                "P02": {...},
                ...
            },
            "summary": {
                "unlocked": [...],
                "locked": [...],
                "next_recommended": str | None
            }
        }
    """
    manifest = manifest or {}
    # 从 manifest 读取 mode（P04 产出），默认为 single
    mode = manifest.get("mode", "single")
    
    result = {
        "mode": mode,
        "service_count": len(service_statuses),
        "phases": {},
        "summary": {
            "unlocked": [],
            "locked": [],
            "next_recommended": None
        }
    }
    
    for phase in P_PHASES:
        phase_result = _check_p_phase_unlock_detail(phase, hub_status, service_statuses, manifest)
        result["phases"][phase] = phase_result
        
        if phase_result["unlocked"]:
            result["summary"]["unlocked"].append(phase)
        else:
            result["summary"]["locked"].append(phase)
    
    # 找推荐的下一个阶段
    phases_status = hub_status.get("phases", {})
    for phase in P_PHASES:
        status = phases_status.get(phase, {}).get("status", "not_started")
        if status == "approved":
            continue
        if result["phases"][phase]["unlocked"]:
            result["summary"]["next_recommended"] = phase
            break
    
    return result


# ============ S 阶段解锁检查 ============

def _check_s_phase_unlock_detail(
    phase: str,
    phase_status: dict,
    is_multi_mode: bool = False,
    p04_approved: bool = False,
    p05_approved: bool = False,
    p06a_approved: bool = False,
    p06b_approved: bool = False,
    p06c_approved: bool = False
) -> dict:
    """
    详细检查 S 阶段解锁条件
    
    Args:
        phase: 阶段 ID
        phase_status: 服务的阶段状态
        is_multi_mode: 是否多服务模式
        p04_approved: P04 是否已放行
        p05_approved: P05 是否已放行（S01 依赖，单/多模式统一）
        p06a_approved: P06-A 是否已放行（多模式 S02 依赖）
        p06b_approved: P06-B 是否已放行（多模式 S06 依赖）
        p06c_approved: P06-C 是否已放行（多模式 T01 依赖）
    
    Returns:
        与 check_p_phase_unlock_detail 相同的结构
    """
    phases = phase_status.get("phases", {})
    
    def is_approved(p: str) -> bool:
        return phases.get(p, {}).get("status") == "approved"
    
    result = {
        "phase": phase,
        "unlocked": False,
        "conditions": [],
        "blocking_reason": None
    }
    
    # S01: P05 approved（单服务/多服务统一依赖 P05 概要设计）
    if phase == "S01":
        result["conditions"] = [
            {"name": "P05 已放行", "satisfied": p05_approved, "detail": f"P05 状态: {'approved' if p05_approved else 'pending'}"}
        ]
        if p05_approved:
            result["unlocked"] = True
        else:
            result["blocking_reason"] = "需等待 P05 概要设计完成"
        return result
    
    # S02: 本服务 S01 approved（多服务模式需要 P06-A approved）
    if phase == "S02":
        s01_approved = is_approved("S01")
        
        conditions = [
            {"name": "本服务 S01 已放行", "satisfied": s01_approved, "detail": f"S01 状态: {phases.get('S01', {}).get('status', 'not_started')}"}
        ]
        
        if is_multi_mode:
            conditions.append({
                "name": "P06-A 已放行", 
                "satisfied": p06a_approved, 
                "detail": f"P06-A 状态: {'approved' if p06a_approved else 'pending'}"
            })
        
        result["conditions"] = conditions
        
        if not s01_approved:
            result["blocking_reason"] = "需先完成本服务 S01 技术方案设计"
        elif is_multi_mode and not p06a_approved:
            result["blocking_reason"] = "多服务模式需等待 P06-A 方案协调完成"
        else:
            result["unlocked"] = True
        
        return result
    
    # S03-S05: 前一阶段 approved
    if phase in ["S03", "S04", "S05"]:
        phase_order = ["S01", "S02", "S03", "S04", "S05"]
        idx = phase_order.index(phase)
        prev_phase = phase_order[idx - 1]
        prev_approved = is_approved(prev_phase)
        
        result["conditions"] = [
            {"name": f"{prev_phase} 已放行", "satisfied": prev_approved, "detail": f"{prev_phase} 状态: {phases.get(prev_phase, {}).get('status', 'not_started')}"}
        ]
        
        if not prev_approved:
            result["blocking_reason"] = f"需先完成 {prev_phase}"
        else:
            result["unlocked"] = True
        
        return result
    
    # S06: 本服务 S05 approved（多服务模式需要 P06-B approved）
    if phase == "S06":
        s05_approved = is_approved("S05")
        
        conditions = [
            {"name": "本服务 S05 已放行", "satisfied": s05_approved, "detail": f"S05 状态: {phases.get('S05', {}).get('status', 'not_started')}"}
        ]
        
        if is_multi_mode:
            conditions.append({
                "name": "P06-B 已放行", 
                "satisfied": p06b_approved, 
                "detail": f"P06-B 状态: {'approved' if p06b_approved else 'pending'}"
            })
        
        result["conditions"] = conditions
        
        if not s05_approved:
            result["blocking_reason"] = "需先完成本服务 S05 集成测试计划"
        elif is_multi_mode and not p06b_approved:
            result["blocking_reason"] = "多服务模式需等待 P06-B 测试协调完成"
        else:
            result["unlocked"] = True
        
        return result
    
    # S07: S06 approved
    if phase == "S07":
        s06_approved = is_approved("S06")
        
        result["conditions"] = [
            {"name": "S06 已放行", "satisfied": s06_approved, "detail": f"S06 状态: {phases.get('S06', {}).get('status', 'not_started')}"}
        ]
        
        if not s06_approved:
            result["blocking_reason"] = "需先完成 S06 代码审查"
        else:
            result["unlocked"] = True
        
        return result
    
    # T01: 本服务 S07 approved（多服务模式需要 P06-C approved）
    if phase == "T01":
        s07_approved = is_approved("S07")
        
        conditions = [
            {"name": "本服务 S07 已放行", "satisfied": s07_approved, "detail": f"S07 状态: {phases.get('S07', {}).get('status', 'not_started')}"}
        ]
        
        if is_multi_mode:
            conditions.append({
                "name": "P06-C 已放行", 
                "satisfied": p06c_approved, 
                "detail": f"P06-C 状态: {'approved' if p06c_approved else 'pending'}"
            })
        
        result["conditions"] = conditions
        
        if not s07_approved:
            result["blocking_reason"] = "需先完成本服务 S07 集成测试执行"
        elif is_multi_mode and not p06c_approved:
            result["blocking_reason"] = "多服务模式需等待 P06-C 测试验收完成"
        else:
            result["unlocked"] = True
        
        return result
    
    result["blocking_reason"] = f"未知阶段: {phase}"
    return result


def _check_all_s_phases(
    phase_status: dict,
    is_multi_mode: bool = False,
    p04_approved: bool = False,
    p05_approved: bool = False,
    p06a_approved: bool = False,
    p06b_approved: bool = False,
    p06c_approved: bool = False
) -> dict:
    """
    检查所有 S 阶段的解锁状态
    
    Returns:
        与 check_all_p_phases 类似的结构
    """
    result = {
        "is_multi_mode": is_multi_mode,
        "p04_approved": p04_approved,
        "p05_approved": p05_approved,
        "p06a_approved": p06a_approved,
        "p06b_approved": p06b_approved,
        "p06c_approved": p06c_approved,
        "phases": {},
        "summary": {
            "unlocked": [],
            "locked": [],
            "next_recommended": None
        }
    }
    
    for phase in S_PHASES:
        phase_result = _check_s_phase_unlock_detail(
            phase=phase,
            phase_status=phase_status,
            is_multi_mode=is_multi_mode,
            p04_approved=p04_approved,
            p05_approved=p05_approved,
            p06a_approved=p06a_approved,
            p06b_approved=p06b_approved,
            p06c_approved=p06c_approved
        )
        result["phases"][phase] = phase_result
        
        if phase_result["unlocked"]:
            result["summary"]["unlocked"].append(phase)
        else:
            result["summary"]["locked"].append(phase)
    
    # 找推荐的下一个阶段
    phases_status = phase_status.get("phases", {})
    for phase in S_PHASES:
        status = phases_status.get(phase, {}).get("status", "not_started")
        if status == "approved":
            continue
        if result["phases"][phase]["unlocked"]:
            result["summary"]["next_recommended"] = phase
            break
    
    return result


# ============ 高层便捷函数 ============

def check_unlock(feature: str, phase: str = None) -> dict:
    """
    一站式解锁检查（简化入参，内部自动获取所有依赖数据）
    
    Args:
        feature: 需求名称
        phase: 阶段 ID（可选，不传则检查所有阶段）
    
    Returns:
        单阶段检查返回:
        {
            "phase": str,
            "unlocked": bool,
            "conditions": [...],
            "blocking_reason": str | None
        }
        
        所有阶段检查返回:
        {
            "mode": str,
            "phases": {...},
            "summary": {"unlocked": [...], "locked": [...], "next_recommended": str}
        }
    """
    # 1. 自动解析路径
    paths = resolve_paths(feature)
    mode = paths["mode"]
    
    # 2. 加载状态文件
    status = load_json_file(paths["status_path"]) if paths["status_path"] else {}
    
    # 3. 加载 manifest
    manifest = load_json_file(paths["manifest_path"]) if paths.get("manifest_path") else {}
    
    # 4. 根据模式执行检查
    if mode == "hub":
        # Hub 模式：检查 P 阶段
        service_statuses = []
        if paths.get("services_dir"):
            service_statuses = load_service_statuses(paths["services_dir"])
        
        if phase:
            return _check_p_phase_unlock_detail(phase, status, service_statuses, manifest)
        else:
            return _check_all_p_phases(status, service_statuses, manifest)
    
    else:
        # Service 模式：检查 S 阶段
        # 读取 Hub 状态获取 P04/P05/P06-A/B/C 放行状态
        hub_status_path = paths.get("hub_status_path")
        hub_status = None
        p04_approved = False
        p05_approved = False
        if hub_status_path:
            hub_status = load_json_file(hub_status_path)
            hub_phases = hub_status.get("phases", {}) if hub_status else {}
            p04_approved = hub_phases.get("P04", {}).get("status") == "approved"
            p05_approved = hub_phases.get("P05", {}).get("status") == "approved"
        
        # 传入 hub_status 以获取 P06-A/B/C 阶段状态
        p06_flags = _load_p06_flags(paths["manifest_path"], hub_status) if paths["manifest_path"] else {
            "is_multi_mode": False,
            "p06a_approved": False,
            "p06b_approved": False,
            "p06c_approved": False
        }
        
        if phase:
            return _check_s_phase_unlock_detail(
                phase=phase,
                phase_status=status,
                is_multi_mode=p06_flags["is_multi_mode"],
                p04_approved=p04_approved,
                p05_approved=p05_approved,
                p06a_approved=p06_flags["p06a_approved"],
                p06b_approved=p06_flags["p06b_approved"],
                p06c_approved=p06_flags["p06c_approved"]
            )
        else:
            return _check_all_s_phases(
                status,
                p06_flags["is_multi_mode"],
                p04_approved,
                p05_approved,
                p06_flags["p06a_approved"],
                p06_flags["p06b_approved"],
                p06_flags["p06c_approved"]
            )


def check_p_unlock(feature: str, phase: str) -> bool:
    """
    简化版 P 阶段解锁检查（只需 feature 和 phase）
    
    Args:
        feature: 需求名称
        phase: 阶段 ID
    
    Returns:
        是否解锁
    """
    paths = resolve_paths(feature)
    hub_status = load_json_file(paths["status_path"]) if paths["status_path"] else {}
    service_statuses = load_service_statuses(paths["services_dir"]) if paths.get("services_dir") else []
    manifest = load_json_file(paths["manifest_path"]) if paths.get("manifest_path") else {}
    
    result = _check_p_phase_unlock_detail(phase, hub_status, service_statuses, manifest)
    return result["unlocked"]


def check_s_unlock(feature: str, phase: str) -> bool:
    """
    简化版 S 阶段解锁检查（只需 feature 和 phase）
    
    Args:
        feature: 需求名称
        phase: 阶段 ID
    
    Returns:
        是否解锁
    """
    paths = resolve_paths(feature)
    
    # 读取服务状态
    status = load_json_file(paths["status_path"]) if paths["status_path"] else {}
    
    # 读取 Hub 状态获取 P04/P05/P06-A/B/C 放行状态
    hub_status_path = paths.get("hub_status_path")
    hub_status = None
    p04_approved = False
    p05_approved = False
    if hub_status_path:
        hub_status = load_json_file(hub_status_path)
        hub_phases = hub_status.get("phases", {}) if hub_status else {}
        p04_approved = hub_phases.get("P04", {}).get("status") == "approved"
        p05_approved = hub_phases.get("P05", {}).get("status") == "approved"
    
    # 加载 P06 门禁状态
    p06_flags = _load_p06_flags(paths["manifest_path"], hub_status) if paths.get("manifest_path") else {
        "is_multi_mode": False,
        "p06a_approved": False,
        "p06b_approved": False,
        "p06c_approved": False
    }
    
    result = _check_s_phase_unlock_detail(
        phase,
        status,
        p06_flags["is_multi_mode"],
        p04_approved,
        p05_approved,
        p06_flags["p06a_approved"],
        p06_flags["p06b_approved"],
        p06_flags["p06c_approved"]
    )
    return result["unlocked"]


def check_phase_unlock(feature: str, phase: str) -> Tuple[bool, str]:
    """
    统一的阶段解锁检查（自动识别 P/S 阶段）
    
    Args:
        feature: 需求名称
        phase: 阶段 ID (P01-P06-C 或 S01-T01)
    
    Returns:
        (是否解锁, 原因描述)
    """
    result = check_unlock(feature, phase)
    reason = result.get("blocking_reason") or "已解锁"
    return result["unlocked"], reason


# ============ 主函数 ============

def main():
    parser = argparse.ArgumentParser(
        description="Vibe Flow 阶段解锁检查工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 检查所有阶段（自动检测工作区类型）
  python vibe_unlock_checker.py --feature flash-sale
  
  # 检查单个阶段
  python vibe_unlock_checker.py --feature flash-sale --phase P06-A
  
  # 文本格式输出
  python vibe_unlock_checker.py --feature flash-sale --format text
"""
    )
    
    parser.add_argument(
        "--feature", "-f",
        required=True,
        help="需求名称（自动推导状态文件路径）"
    )
    
    parser.add_argument(
        "--phase", "-p",
        help="检查单个阶段（不指定则检查所有）"
    )
    
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="输出格式 (默认: json)"
    )
    
    args = parser.parse_args()
    
    try:
        # 1. 推导路径（自动检测工作区类型）
        paths = resolve_paths(args.feature)
        mode = paths["mode"]
        if mode not in ["hub", "service"]:
            raise ValueError(f"无法识别工作区类型: {mode}，请确保 .vibe/workspace.json 存在")
        
        status = load_json_file(paths["status_path"])
        
        # 3. 执行检查
        if mode == "hub":
            # 加载服务状态
            service_statuses = []
            if paths["services_dir"]:
                service_statuses = load_service_statuses(paths["services_dir"])
            
            # 加载 manifest（用于获取 mode 和服务列表）
            manifest = load_json_file(paths.get("manifest_path")) if paths.get("manifest_path") else {}
            
            if args.phase:
                # 检查单个阶段
                result = _check_p_phase_unlock_detail(args.phase, status, service_statuses, manifest)
            else:
                # 检查所有阶段
                result = _check_all_p_phases(status, service_statuses, manifest)
        
        else:  # service
            # 读取 Hub 状态获取 P04/P05/P06-A/B/C 放行状态
            hub_status_path = paths.get("hub_status_path")
            hub_status = None
            p04_approved = False
            p05_approved = False
            if hub_status_path:
                hub_status = load_json_file(hub_status_path)
                hub_phases = hub_status.get("phases", {}) if hub_status else {}
                p04_approved = hub_phases.get("P04", {}).get("status") == "approved"
                p05_approved = hub_phases.get("P05", {}).get("status") == "approved"
            
            # 读取 P06 门禁（传入 hub_status 以获取 P06-A/B/C 阶段状态）
            manifest_path = paths["manifest_path"]
            p06_flags = _load_p06_flags(manifest_path, hub_status) if manifest_path else {
                "is_multi_mode": False,
                "p06a_approved": False,
                "p06b_approved": False,
                "p06c_approved": False
            }
            
            if args.phase:
                # 检查单个阶段
                result = _check_s_phase_unlock_detail(
                    phase=args.phase,
                    phase_status=status,
                    is_multi_mode=p06_flags["is_multi_mode"],
                    p04_approved=p04_approved,
                    p05_approved=p05_approved,
                    p06a_approved=p06_flags["p06a_approved"],
                    p06b_approved=p06_flags["p06b_approved"],
                    p06c_approved=p06_flags["p06c_approved"]
                )
            else:
                # 检查所有阶段
                result = _check_all_s_phases(
                    status,
                    p06_flags["is_multi_mode"],
                    p04_approved,
                    p05_approved,
                    p06_flags["p06a_approved"],
                    p06_flags["p06b_approved"],
                    p06_flags["p06c_approved"]
                )
        
        # 5. 添加 next_action 指引
        if "phases" in result:
            # 批量检查模式
            result["next_action"] = {
                "type": "conditional",
                "instruction": "根据 summary.next_recommended 判断下一步",
                "condition_field": "summary.next_recommended"
            }
        else:
            # 单阶段检查模式
            if result.get("unlocked"):
                result["next_action"] = {
                    "type": "auto_continue",
                    "instruction": "阶段已解锁，可执行"
                }
            else:
                result["next_action"] = {
                    "type": "auto_continue",
                    "instruction": "阶段锁定，展示 blocking_reason"
                }
        
        # 6. 输出结果
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            # 文本格式输出
            if "phases" in result:
                print(f"🔍 阶段解锁检查 ({mode} 模式)")
                print("=" * 50)
                for phase, info in result["phases"].items():
                    icon = "✅" if info["unlocked"] else "🔒"
                    print(f"  {icon} {phase}: {'已解锁' if info['unlocked'] else info['blocking_reason']}")
                print("=" * 50)
                print(f"推荐下一步: {result['summary']['next_recommended'] or '无'}")
            else:
                icon = "✅" if result["unlocked"] else "🔒"
                print(f"{icon} {result['phase']}: {'已解锁' if result['unlocked'] else result['blocking_reason']}")
    
    except Exception as e:
        print(json.dumps({
            "error": True,
            "message": f"检查错误: {str(e)}"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()

