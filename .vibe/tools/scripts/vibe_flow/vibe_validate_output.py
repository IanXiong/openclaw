#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Vibe Output Validator - 统一产物校验脚本

功能：
- 校验各阶段产出物的存在性
- 校验产出物的基本内容完整性
- 返回结构化的校验结果

使用方式：
    python vibe_validate_output.py --phase <phase_id> --feature <feature_name>

输出：
    JSON 格式的校验结果
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
import re
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

# 导入 vibe_helpers 通用工具函数
from vibe_helpers import (
    find_project_root,
    get_workspace_type,
    load_json_file,
)


# ============ 阶段产出物定义 ============

PHASE_OUTPUTS = {
    # P 阶段（Hub）
    "P01": {
        "name": "需求采集",
        "output_dir": "P01_req_intake",
        "files": [
            {
                "name": "PRD.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 20, "desc": "PRD 内容不少于 20 行"},
                    {"type": "heading_count", "value": 3, "desc": "至少包含 3 个章节标题"},
                ]
            },
            {
                "name": "source_export",
                "required": True,
                "is_dir": True,
                "checks": [
                    {"type": "dir_not_empty", "desc": "source_export 目录非空"},
                ]
            },
            {
                "name": "assets_summary.md",
                "required": False,  # 可选：无图片时可能不存在
                "checks": [
                    {"type": "min_lines", "value": 5, "desc": "图片汇总表不少于 5 行"},
                ]
            },
            {
                "name": "metadata.json",
                "required": True,
                "checks": [
                    {"type": "valid_json", "desc": "metadata.json 是有效 JSON"},
                    {"type": "json_has_key", "value": "doc_url", "desc": "包含 doc_url 字段"},
                ]
            },
        ]
    },
    "P02": {
        "name": "知识库下载",
        "output_dir": "P02_context_analysis",
        "files": [
            {
                "name": "source_export",
                "required": False,
                "is_dir": True,
                "checks": [
                    {"type": "dir_not_empty", "desc": "source_export 目录非空（飞书文档已下载）"},
                ]
            },
            {
                "name": "knowledge_repos",
                "required": False,
                "is_dir": True,
                "checks": [
                    {"type": "dir_not_empty", "desc": "knowledge_repos 目录非空（本地/Git 知识库已拷贝）"},
                ]
            },
        ]
    },
    "P03": {
        "name": "PRD评审",
        "output_dir": "P03_prd_review",
        "files": [
            {
                "name": "PRD_standardized.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 80, "desc": "标准化 PRD 不少于 80 行"},
                    {"type": "heading_count", "value": 8, "desc": "至少包含 8 个章节标题"},
                    {"type": "contains", "value": "需求背景", "desc": "包含'需求背景'章节"},
                    {"type": "contains", "value": "产品方案", "desc": "包含'产品方案'章节"},
                ]
            },
            {
                "name": "atomic_functions.toon",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 50, "desc": "原子功能清单（v3.1）不少于 50 行"},
                    {"type": "contains", "value": "FUNCTIONS[", "desc": "必须包含 FUNCTIONS 表格定义"},
                    {"type": "contains", "value": "QUESTIONS[", "desc": "必须包含 QUESTIONS 表格定义"},
                    {"type": "contains", "value": "DOMAIN_CONTEXT:", "desc": "必须包含 DOMAIN_CONTEXT 章节"},
                    {"type": "contains", "value": "prd_evidence", "desc": "FUNCTIONS 表必须包含 prd_evidence 字段"},
                ]
            },
            {
                "name": "PRD_review_report.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 20, "desc": "评审报告不少于 20 行"},
                    {"type": "contains", "value": "服务", "desc": "包含服务匹配信息"},
                ]
            },
        ]
    },
    "P04": {
        "name": "服务分解",
        "output_dir": "P04_service_split",
        "files": [
            {
                "name": "service_split_decision.toon",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 30, "desc": "服务拆分决策记录不少于 30 行"},
                    {"type": "contains", "value": "function_assignments[", "desc": "包含功能归属映射表"},
                    {"type": "contains", "value": "services[", "desc": "包含服务列表"},
                    {"type": "contains", "value": "service_dependencies[", "desc": "包含服务依赖关系表"},
                    {"type": "contains", "value": "WORKFLOW_ANALYSIS:", "desc": "包含业务流程环节归属分析"},
                ]
            },
            {
                "name": "execution_manifest.json",
                "required": True,
                "checks": [
                    {"type": "valid_json", "desc": "execution_manifest.json 是有效 JSON"},
                    {"type": "json_has_key", "value": "mode", "desc": "包含 mode 字段"},
                    {"type": "json_has_key", "value": "feature", "desc": "包含 feature 字段"},
                ]
            },
            {
                "name": "services",
                "required": False,  # 条件性产出：仅多服务场景需要
                "is_dir": True,
                "checks": [
                    {"type": "dir_not_empty", "desc": "services 目录非空（含服务输入包）"},
                ]
            },
        ]
    },
    "P05": {
        "name": "概要设计",
        "output_dir": "P05_preliminary_design",
        "files": [
            {
                "name": "service_split_decision.toon",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 30, "desc": "服务拆分决策记录不少于 30 行"},
                    {"type": "contains", "value": "function_assignments[", "desc": "包含功能归属映射表"},
                ]
            },
            {
                "name": "execution_manifest.json",
                "required": True,
                "checks": [
                    {"type": "valid_json", "desc": "execution_manifest.json 是有效 JSON"},
                    {"type": "json_has_key", "value": "mode", "desc": "包含 mode 字段"},
                ]
            },
            {
                "name": "services",
                "required": True,
                "is_dir": True,
                "checks": [
                    {"type": "dir_not_empty", "desc": "services 目录非空"},
                    {"type": "has_service_inputs", "desc": "各服务包含 service_inputs.toon 且已更新 CHAPTER_2"},
                ]
            },
        ]
    },
    "P06-A": {
        "name": "方案协调",
        "output_dir": "P06_coordination",
        "files": [
            {
                "name": "review_opinion.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 20, "desc": "评审意见不少于 20 行"},
                    {"type": "contains", "value": "评审", "desc": "包含评审关键字"},
                ]
            },
        ]
    },
    "P06-B": {
        "name": "测试协调",
        "output_dir": "P06_coordination",
        "files": [
            {
                "name": "cross_service_test_plan.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 30, "desc": "跨服务测试计划不少于 30 行"},
                    {"type": "contains", "value": "测试", "desc": "包含测试关键字"},
                    {"type": "contains", "value": "用例", "desc": "包含用例关键字"},
                ]
            },
        ]
    },
    "P06-C": {
        "name": "测试验收",
        "output_dir": "P06_coordination",
        "files": [
            {
                "name": "test_acceptance_report.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 20, "desc": "测试验收报告不少于 20 行"},
                    {"type": "contains", "value": "通过", "desc": "包含通过/结果关键字"},
                ]
            },
        ]
    },
    
    # S 阶段（Service）
    "S01": {
        "name": "技术方案设计",
        "output_dir": "S01_tech_design",
        "files": [
            {
                "name": "tech_design.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 150, "desc": "技术设计文档不少于 150 行"},
                    {"type": "heading_count", "value": 12, "desc": "至少包含 12 个章节标题"},
                    {"type": "contains_all", "value": ["## 1.", "## 2.", "## 3.", "## 4."], "desc": "包含前 4 个编号章节"},
                    {"type": "contains", "value": "接口", "desc": "包含接口设计"},
                ]
            },
        ]
    },
    "S02": {
        "name": "任务拆解",
        "output_dir": "S02_task_breakdown",
        "files": [
            {
                "name": "task_plan.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 30, "desc": "任务清单不少于 30 行"},
                    {"type": "contains", "value": "任务", "desc": "包含任务关键字"},
                    {"type": "contains", "value": "|", "desc": "包含表格结构"},
                ]
            },
        ]
    },
    "S03": {
        "name": "编码实现",
        "output_dir": "S03_implementation",
        "files": [
            {
                "name": "Task_*_Log.md",  # 动态文件名，支持通配符匹配
                "required": True,
                "pattern": r"Task_[\w-]+_Log\.md",  # 正则匹配 Task_<ID>_Log.md（兼容连字符）
                "checks": [
                    {"type": "min_lines", "value": 20, "desc": "实现日志不少于 20 行"},
                    {"type": "contains", "value": "变更", "desc": "包含变更记录"},
                ]
            },
        ]
    },
    "S04": {
        "name": "单元测试",
        "output_dir": "S04_unit_test",
        "files": [
            {
                "name": "unit_test_report.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 20, "desc": "单测报告不少于 20 行"},
                    {"type": "contains", "value": "测试", "desc": "包含测试关键字"},
                    {"type": "contains", "value": "覆盖", "desc": "包含覆盖率信息"},
                ]
            },
        ]
    },
    "S05": {
        "name": "集成测试计划",
        "output_dir": "S05_integration_plan",
        "files": [
            {
                "name": "integration_plan.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 30, "desc": "集成测试计划不少于 30 行"},
                    {"type": "contains", "value": "测试", "desc": "包含测试关键字"},
                    {"type": "contains", "value": "场景", "desc": "包含测试场景"},
                    {"type": "contains", "value": "TraceID", "desc": "包含 TraceID 定义"},
                ]
            },
            {
                "name": "assets",
                "required": False,  # 可选：简单需求可能无脚本
                "is_dir": True,
                "checks": [
                    {"type": "dir_not_empty", "desc": "assets 目录非空（含测试脚本）"},
                ]
            },
        ]
    },
    "S06": {
        "name": "代码审查",
        "output_dir": "S06_code_review",
        "files": [
            {
                "name": "CR_Report.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 20, "desc": "代码审查报告不少于 20 行"},
                    {"type": "contains", "value": "审查", "desc": "包含审查关键字"},
                    {"type": "contains", "value": "问题", "desc": "包含问题记录"},
                ]
            },
            {
                "name": "Deployment_Report.md",
                "required": False,  # 条件性产出：仅当触发部署时
                "checks": [
                    {"type": "min_lines", "value": 10, "desc": "部署报告不少于 10 行"},
                    {"type": "contains", "value": "部署", "desc": "包含部署关键字"},
                ]
            },
        ]
    },
    "S07": {
        "name": "集成测试执行",
        "output_dir": "S07_integration_execute",
        "files": [
            {
                "name": "Test_Report.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 20, "desc": "集成测试报告不少于 20 行"},
                    {"type": "contains", "value": "|", "desc": "包含表格结构（测试结果）"},
                    {"type": "contains_any", "value": ["通过", "PASS", "✅"], "desc": "包含测试结果标识"},
                ]
            },
        ]
    },
    
    # T 阶段
    "T01": {
        "name": "E2E测试",
        "output_dir": "T01_e2e_testing",
        "files": [
            {
                "name": "E2E_Test_Report.md",
                "required": True,
                "checks": [
                    {"type": "min_lines", "value": 30, "desc": "E2E 测试报告不少于 30 行"},
                    {"type": "contains", "value": "|", "desc": "包含表格结构（测试用例）"},
                    {"type": "contains", "value": "覆盖", "desc": "包含 PRD 功能覆盖追溯"},
                ]
            },
            {
                "name": "测试用例.md",
                "required": False,  # 可选：可能合并到报告中
                "checks": [
                    {"type": "min_lines", "value": 20, "desc": "测试用例不少于 20 行"},
                    {"type": "contains", "value": "|", "desc": "包含表格结构"},
                ]
            },
        ]
    },
}


# ============ 校验函数 ============

def check_min_lines(content: str, min_lines: int) -> Tuple[bool, str]:
    """检查最小行数"""
    lines = [l for l in content.split('\n') if l.strip()]
    actual = len(lines)
    if actual >= min_lines:
        return True, f"✅ 行数检查通过（{actual} 行）"
    return False, f"❌ 行数不足（实际 {actual} 行，要求 ≥{min_lines} 行）"


def check_contains(content: str, keyword: str) -> Tuple[bool, str]:
    """检查是否包含关键字"""
    if keyword in content:
        return True, f"✅ 包含关键字 '{keyword}'"
    return False, f"❌ 未找到关键字 '{keyword}'"


def check_contains_all(content: str, keywords: List[str]) -> Tuple[bool, str]:
    """检查是否包含所有关键字"""
    missing = [k for k in keywords if k not in content]
    if not missing:
        return True, f"✅ 包含所有必需关键字"
    return False, f"❌ 缺少关键字: {', '.join(missing)}"


def check_contains_any(content: str, keywords: List[str]) -> Tuple[bool, str]:
    """检查是否包含任一关键字"""
    found = [k for k in keywords if k in content]
    if found:
        return True, f"✅ 包含关键字 '{found[0]}'"
    return False, f"❌ 未找到任一关键字: {', '.join(keywords)}"


def check_heading_count(content: str, min_count: int) -> Tuple[bool, str]:
    """检查标题数量"""
    headings = re.findall(r'^#{1,6}\s+.+$', content, re.MULTILINE)
    actual = len(headings)
    if actual >= min_count:
        return True, f"✅ 章节数量检查通过（{actual} 个标题）"
    return False, f"❌ 章节数量不足（实际 {actual} 个，要求 ≥{min_count} 个）"


def check_valid_json(content: str) -> Tuple[bool, str]:
    """检查是否为有效 JSON"""
    try:
        json.loads(content)
        return True, "✅ JSON 格式有效"
    except json.JSONDecodeError as e:
        return False, f"❌ JSON 格式无效: {str(e)[:50]}"


def check_json_has_key(content: str, key: str) -> Tuple[bool, str]:
    """检查 JSON 是否包含指定 key"""
    try:
        data = json.loads(content)
        if key in data:
            return True, f"✅ 包含字段 '{key}'"
        return False, f"❌ 缺少字段 '{key}'"
    except json.JSONDecodeError:
        return False, f"❌ 无法解析 JSON"


def check_dir_not_empty(dir_path: Path) -> Tuple[bool, str]:
    """检查目录是否非空"""
    if not dir_path.exists():
        return False, f"❌ 目录不存在"
    items = list(dir_path.iterdir())
    if items:
        return True, f"✅ 目录非空（{len(items)} 个项目）"
    return False, f"❌ 目录为空"


def check_has_service_inputs(dir_path: Path) -> Tuple[bool, str]:
    """检查 P05 产物：各服务目录是否包含更新后的 service_inputs.toon"""
    if not dir_path.exists():
        return False, f"❌ services 目录不存在"
    
    # 获取所有服务子目录
    service_dirs = [d for d in dir_path.iterdir() if d.is_dir()]
    if not service_dirs:
        return False, f"❌ services 目录下无服务子目录"
    
    # 读取 execution_manifest.json 判断模式
    manifest_path = dir_path.parent / "execution_manifest.json"
    mode = "single"  # 默认单服务
    if manifest_path.exists():
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest = json.load(f)
                mode = manifest.get("mode", "single")
        except:
            pass
    
    issues = []
    valid_count = 0
    
    for service_dir in service_dirs:
        service_name = service_dir.name
        service_input_file = service_dir / "service_inputs.toon"
        
        # 检查文件是否存在
        if not service_input_file.exists():
            issues.append(f"{service_name}: service_inputs.toon 不存在")
            continue
        
        # 读取文件内容
        try:
            with open(service_input_file, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            issues.append(f"{service_name}: 读取失败 - {str(e)[:30]}")
            continue
        
        # 检查 CHAPTER_2_SERVICE_DEPS 章节
        if "CHAPTER_2_SERVICE_DEPS:" not in content:
            issues.append(f"{service_name}: 缺少 CHAPTER_2_SERVICE_DEPS 章节")
            continue
        
        # 检查 interfaces[] 字段（必需）
        if "interfaces[" not in content:
            issues.append(f"{service_name}: CHAPTER_2 缺少 interfaces[] 字段")
            continue
        
        # 多服务模式需检查 external_system_dependencies
        if mode == "multi":
            if "external_system_dependencies" not in content:
                issues.append(f"{service_name}: 多服务模式缺少 external_system_dependencies 字段")
                continue
        
        valid_count += 1
    
    total = len(service_dirs)
    if issues:
        issue_summary = "; ".join(issues[:3])  # 只显示前3个问题
        if len(issues) > 3:
            issue_summary += f" (共{len(issues)}个问题)"
        return False, f"❌ {valid_count}/{total} 个服务验证通过 - {issue_summary}"
    
    mode_desc = "单服务" if mode == "single" else "多服务"
    return True, f"✅ 所有服务({total}个)的 service_inputs.toon 已更新 CHAPTER_2 ({mode_desc}模式)"


def run_check(check: Dict, content: str = None, file_path: Path = None) -> Tuple[bool, str]:
    """执行单个检查"""
    check_type = check["type"]
    
    if check_type == "min_lines":
        return check_min_lines(content, check["value"])
    elif check_type == "contains":
        return check_contains(content, check["value"])
    elif check_type == "contains_all":
        return check_contains_all(content, check["value"])
    elif check_type == "contains_any":
        return check_contains_any(content, check["value"])
    elif check_type == "heading_count":
        return check_heading_count(content, check["value"])
    elif check_type == "valid_json":
        return check_valid_json(content)
    elif check_type == "json_has_key":
        return check_json_has_key(content, check["value"])
    elif check_type == "dir_not_empty":
        return check_dir_not_empty(file_path)
    elif check_type == "has_service_inputs":
        return check_has_service_inputs(file_path)
    else:
        return False, f"❓ 未知检查类型: {check_type}"


# ============ 主校验逻辑 ============

def get_output_base_path(feature_name: str, phase: str) -> Optional[Path]:
    """获取产出物基础路径"""
    root = find_project_root()
    ws_type = get_workspace_type()
    
    phase_config = PHASE_OUTPUTS.get(phase)
    if not phase_config:
        return None
    
    output_dir = phase_config["output_dir"]
    
    if ws_type == "hub":
        # Hub: features/<feature>/docs/<output_dir>/
        return root / "features" / feature_name / "docs" / output_dir
    elif ws_type == "service":
        # Service: docs/features/<feature>/<output_dir>/
        return root / "docs" / "features" / feature_name / output_dir
    
    return None


def validate_phase_output(phase: str, feature_name: str) -> Dict[str, Any]:
    """
    校验指定阶段的产出物
    
    Returns:
        {
            "valid": bool,
            "phase": str,
            "phase_name": str,
            "checks": [
                {
                    "file": str,
                    "exists": bool,
                    "required": bool,
                    "checks": [
                        {"desc": str, "passed": bool, "message": str}
                    ]
                }
            ],
            "summary": {
                "total": int,
                "passed": int,
                "failed": int,
                "warnings": int
            },
            "render_template": str  # 用于 AI 展示的格式化输出
        }
    """
    result = {
        "valid": True,
        "phase": phase,
        "phase_name": "",
        "checks": [],
        "summary": {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "warnings": 0
        },
        "render_template": ""
    }
    
    # 获取阶段配置
    phase_config = PHASE_OUTPUTS.get(phase)
    if not phase_config:
        result["valid"] = False
        result["render_template"] = f"❌ 未知阶段: {phase}"
        return result
    
    result["phase_name"] = phase_config["name"]
    
    # 获取产出物路径
    base_path = get_output_base_path(feature_name, phase)
    if not base_path:
        result["valid"] = False
        result["render_template"] = f"❌ 无法确定产出物路径"
        return result
    
    # 校验每个文件
    render_lines = [
        f"📋 {phase} {phase_config['name']} 产出校验结果",
        f"   路径: {base_path}",
        ""
    ]
    
    for file_config in phase_config["files"]:
        file_name = file_config["name"]
        is_dir = file_config.get("is_dir", False)
        required = file_config.get("required", True)
        checks = file_config.get("checks", [])
        pattern = file_config.get("pattern")  # 正则模式（可选）
        
        # 支持通配符和正则模式匹配
        matched_files = []
        if pattern or "*" in file_name:
            # 使用正则或glob模式查找匹配文件
            if base_path.exists():
                if pattern:
                    regex = re.compile(pattern)
                    matched_files = sorted([f for f in base_path.iterdir() if regex.match(f.name)])
                else:
                    matched_files = sorted(base_path.glob(file_name))
            
            # 多文件匹配：逐个校验所有匹配文件
            if matched_files:
                for mf in matched_files:
                    mf_result = {
                        "file": mf.name,
                        "path": str(mf),
                        "exists": True,
                        "required": required,
                        "checks": []
                    }
                    result["summary"]["total"] += 1
                    result["summary"]["passed"] += 1  # 存在性通过
                    
                    # 读取文件内容（非目录）
                    mf_content = None
                    if not is_dir:
                        try:
                            with open(mf, 'r', encoding='utf-8') as f:
                                mf_content = f.read()
                        except Exception as e:
                            mf_result["checks"].append({
                                "desc": "读取文件",
                                "passed": False,
                                "message": f"❌ 读取失败: {str(e)[:50]}"
                            })
                            result["summary"]["failed"] += 1
                            render_lines.append(f"   ❌ {mf.name} - 读取失败")
                            result["checks"].append(mf_result)
                            continue
                    
                    # 执行内容检查
                    mf_all_passed = True
                    mf_messages = []
                    
                    for check in checks:
                        result["summary"]["total"] += 1
                        passed, message = run_check(check, mf_content, mf)
                        
                        mf_result["checks"].append({
                            "desc": check.get("desc", check["type"]),
                            "passed": passed,
                            "message": message
                        })
                        
                        if passed:
                            result["summary"]["passed"] += 1
                        else:
                            mf_all_passed = False
                            if required:
                                result["summary"]["failed"] += 1
                            else:
                                result["summary"]["warnings"] += 1
                        
                        mf_messages.append(message)
                    
                    # 对于通配符匹配的文件，内容检查失败不阻断整体校验
                    if mf_all_passed:
                        render_lines.append(f"   ✅ {mf.name}")
                        for msg in mf_messages:
                            render_lines.append(f"      {msg}")
                    else:
                        render_lines.append(f"   ⚠️ {mf.name}（部分检查未通过）")
                        for msg in mf_messages:
                            render_lines.append(f"      {msg}")
                    
                    result["checks"].append(mf_result)
                continue  # 已处理所有匹配文件，跳过后续单文件逻辑
            else:
                # 无匹配文件
                file_path = base_path / file_name
                actual_file_name = file_name
        else:
            file_path = base_path / file_name
            actual_file_name = file_name
        
        file_result = {
            "file": actual_file_name,
            "path": str(file_path),
            "exists": False,
            "required": required,
            "checks": []
        }
        
        # 检查存在性
        exists = file_path.exists()
        file_result["exists"] = exists
        result["summary"]["total"] += 1
        
        if not exists:
            if required:
                result["valid"] = False
                result["summary"]["failed"] += 1
                render_lines.append(f"   ❌ {actual_file_name} - 文件不存在")
            else:
                result["summary"]["warnings"] += 1
                render_lines.append(f"   ⚠️ {actual_file_name} - 文件不存在（可选）")
            result["checks"].append(file_result)
            continue
        
        # 读取文件内容（非目录）
        content = None
        if not is_dir:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                file_result["checks"].append({
                    "desc": "读取文件",
                    "passed": False,
                    "message": f"❌ 读取失败: {str(e)[:50]}"
                })
                result["valid"] = False
                result["summary"]["failed"] += 1
                render_lines.append(f"   ❌ {actual_file_name} - 读取失败")
                result["checks"].append(file_result)
                continue
        
        # 执行内容检查
        all_checks_passed = True
        check_messages = []
        
        for check in checks:
            result["summary"]["total"] += 1
            passed, message = run_check(check, content, file_path)
            
            file_result["checks"].append({
                "desc": check.get("desc", check["type"]),
                "passed": passed,
                "message": message
            })
            
            if passed:
                result["summary"]["passed"] += 1
            else:
                all_checks_passed = False
                if required:
                    result["valid"] = False
                    result["summary"]["failed"] += 1
                else:
                    result["summary"]["warnings"] += 1
            
            check_messages.append(message)
        
        # 更新存在性检查的统计
        if exists:
            result["summary"]["passed"] += 1
        
        # 生成渲染行
        if all_checks_passed:
            render_lines.append(f"   ✅ {actual_file_name}")
            for msg in check_messages:
                render_lines.append(f"      {msg}")
        else:
            status_icon = "❌" if required else "⚠️"
            render_lines.append(f"   {status_icon} {actual_file_name}")
            for msg in check_messages:
                render_lines.append(f"      {msg}")
        
        result["checks"].append(file_result)
    
    # 生成汇总
    render_lines.extend([
        "",
        "─" * 50,
        f"📊 汇总: {result['summary']['passed']}/{result['summary']['total']} 项通过",
    ])
    
    if result["summary"]["failed"] > 0:
        render_lines.append(f"   ❌ {result['summary']['failed']} 项失败")
    if result["summary"]["warnings"] > 0:
        render_lines.append(f"   ⚠️ {result['summary']['warnings']} 项警告")
    
    if result["valid"]:
        render_lines.extend([
            "",
            "✅ 产出基础校验通过",
            "",
            "⚠️ 请注意：脚本仅检查产出的存在性和基本完整性。",
            "   您仍需审核产出内容的正确性和业务合理性。",
        ])
    else:
        render_lines.extend([
            "",
            "❌ 产出校验失败，请检查上述问题后重试"
        ])
    
    result["render_template"] = "\n".join(render_lines)
    
    return result


# ============ 命令行入口 ============

def main():
    parser = argparse.ArgumentParser(
        description="Vibe Output Validator - 统一产物校验脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "--phase",
        help="阶段 ID（如 P01, S01, T01）"
    )
    
    parser.add_argument(
        "--feature",
        help="需求名称"
    )
    
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式（默认输出人类可读格式）"
    )
    
    parser.add_argument(
        "--list-phases",
        action="store_true",
        help="列出所有支持的阶段"
    )
    
    args, _ = parser.parse_known_args()
    
    # 列出所有阶段（不需要其他参数）
    if args.list_phases:
        print("支持的阶段：")
        for phase_id, config in PHASE_OUTPUTS.items():
            print(f"  {phase_id}: {config['name']}")
        sys.exit(0)
    
    # 检查必填参数
    if not args.phase or not args.feature:
        parser.error("--phase 和 --feature 参数是必需的")
    
    # 执行校验
    result = validate_phase_output(args.phase, args.feature)
    
    # 添加 next_action 指引
    if result.get("valid"):
        result["next_action"] = {
            "type": "auto_continue",
            "instruction": "产出物校验通过，进入放行确认 (--mode approval)"
        }
    else:
        result["next_action"] = {
            "type": "auto_continue",
            "instruction": "产出物校验失败，展示错误并提示修复"
        }
    
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["render_template"])
    
    # 返回码
    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
