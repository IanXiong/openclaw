#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Vibe Flow 菜单渲染脚本

功能：
- 读取状态文件，生成固定格式的 ASCII 菜单
- AI 只需原样输出，不做任何修改
- 确保菜单格式 100% 一致

使用方式：
    python vibe_render_menu.py --mode progress --feature <feature_name>

输出：
    直接输出 ASCII 格式的菜单字符串，AI 应原样展示
    
说明：
    progress 模式会自动检测工作区类型（Hub/Service）并渲染对应菜单
"""

import json
import argparse
import sys
import io
import subprocess
from pathlib import Path
from typing import Optional, Tuple, List

# 跨平台 Unicode 输出支持 (Windows GBK 兼容)
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

# 导入 vibe_helpers 通用工具函数
from vibe_helpers import (
    get_vaf_version,
    load_json_file,
    find_project_root,
    calc_duration,
    resolve_paths,
    get_workspace_type,
)

# 导入 vibe_unlock_checker 的函数（复用解锁逻辑）
from vibe_unlock_checker import (
    check_p_unlock,   # 简化版 P 阶段解锁检查
    check_s_unlock,   # 简化版 S 阶段解锁检查
)



# ============ Git 同步函数 ============

def run_git_command(cmd: List[str], cwd: Optional[str] = None) -> Tuple[int, str, str]:
    """执行 Git 命令并返回 (返回码, stdout, stderr)"""
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


def is_git_repo(path: Optional[str] = None) -> bool:
    """检测指定目录是否为 Git 仓库"""
    code, _, _ = run_git_command(["git", "rev-parse", "--is-inside-work-tree"], cwd=path)
    return code == 0


def git_pull_repo(path: Optional[str] = None, repo_name: str = "当前仓库") -> Tuple[bool, str]:
    """
    在指定目录执行 git pull
    
    Args:
        path: 目标目录路径，None 表示当前目录
        repo_name: 仓库名称（用于日志显示）
    
    Returns:
        (success, message)
    """
    if not is_git_repo(path):
        return True, f"⚠️ {repo_name} 非 Git 仓库，跳过同步"
    
    code, stdout, stderr = run_git_command(["git", "pull", "--rebase=false"], cwd=path)
    
    if code == 0:
        if "Already up to date" in stdout or "已经是最新" in stdout:
            return True, f"✅ {repo_name} 已是最新"
        else:
            return True, f"✅ {repo_name} 同步完成"
    else:
        # 非致命错误，继续流程
        return False, f"⚠️ {repo_name} 同步失败: {stderr[:80]}"


def auto_git_pull(feature: str, verbose: bool = False) -> dict:
    """
    根据工作区类型自动执行 git pull
    
    - Hub 模式：拉取当前仓库
    - Service 模式：只拉取 Hub（通过 manifest_local.json 的 hub_path）
    
    Args:
        feature: 需求名称
        verbose: 是否输出详细日志
    
    Returns:
        {"success": bool, "messages": [str], "hub_pulled": bool}
    """
    result = {"success": True, "messages": [], "hub_pulled": False}
    ws_type = get_workspace_type()
    
    if ws_type == "hub":
        # Hub 模式：拉取当前仓库
        success, msg = git_pull_repo(None, "Hub")
        result["messages"].append(msg)
        result["hub_pulled"] = success
        if not success:
            result["success"] = False
    
    elif ws_type == "service":
        # Service 模式：只拉取 Hub
        root = find_project_root()
        manifest_local_path = root / "docs" / "features" / feature / "manifest_local.json"
        
        if manifest_local_path.exists():
            manifest_local = load_json_file(str(manifest_local_path))
            hub_path = manifest_local.get("hub_path")
            
            if hub_path:
                success, msg = git_pull_repo(hub_path, "Hub")
                result["messages"].append(msg)
                result["hub_pulled"] = success
                if not success:
                    result["success"] = False
            else:
                result["messages"].append("⚠️ manifest_local.json 中未配置 hub_path，跳过 Hub 同步")
        else:
            result["messages"].append("⚠️ 未找到 manifest_local.json，跳过 Hub 同步")
    
    return result


# ============ 阶段定义 ============

P_PHASES = [
    ("P01", "需求采集"),
    ("P02", "知识库加载"),
    ("P03", "PRD评审"),
    ("P04", "服务分解"),
    ("P05", "概要设计"),
    ("P06-A", "方案协调"),
    ("P06-B", "测试协调"),
    ("P06-C", "测试验收"),
]

S_PHASES = [
    ("S01", "技术方案设计"),
    ("S02", "任务拆解"),
    ("S03", "编码实现"),
    ("S04", "单元测试"),
    ("S05", "集成测试计划"),
    ("S06", "代码审查"),
    ("S07", "集成测试执行"),
]

T_PHASES = [
    ("T01", "E2E测试"),
]


# ============ 阶段详情定义（用于执行确认面板）============

PHASE_DETAILS = {
    "P01": {
        "name": "需求采集",
        "tasks": [
            "下载飞书需求文档",
            "解析图片资产（Qwen-VL）",
            "生成整合后的 PRD",
        ],
        "outputs": [
            "PRD.md（整合后的 PRD）",
            "source_export/（飞书文档原稿 + 图片）",
            "assets_analysis/（图片解析结果）",
        ],
    },
    "P02": {
        "name": "知识库加载",
        "tasks": [
            "加载知识库文档",
            "检测多源冲突",
            "整合输出知识库",
        ],
        "outputs": [
            "knowledge_base.md（整合后的知识库）",
            "context_analysis_report.md（上下文分析报告）",
        ],
    },
    "P03": {
        "name": "PRD评审",
        "tasks": [
            "PRD 目录映射与标准化",
            "技术可行性评估",
            "服务改造点拆解",
        ],
        "outputs": [
            "PRD_standardized.md（标准化 PRD）",
            "atomic_functions.toon（原子功能清单 v3.0）",
            "PRD_review_report.md（评审报告）",
        ],
    },
    "P04": {
        "name": "服务分解",
        "tasks": [
            "深度分析知识库",
            "识别服务边界",
            "生成执行清单",
        ],
        "outputs": [
            "service_split_report.md（服务分解报告）",
            "execution_manifest.json（执行清单）",
        ],
    },
    "P05": {
        "name": "概要设计",
        "tasks": [
            "从知识库提取接口索引",
            "分析服务依赖关系",
            "更新 service_inputs.toon",
        ],
        "outputs": [
            "service_inputs.toon CHAPTER_2（接口索引+依赖）",
        ],
    },
    "P06-A": {
        "name": "方案协调",
        "tasks": [
            "汇总各服务 S01 方案",
            "7 维度跨服务评审",
            "问题下发与修复追踪",
        ],
        "outputs": [
            "review_opinion.md（评审意见）",
            "review_approval.md（方案评审放行单）",
        ],
    },
    "P06-B": {
        "name": "测试协调",
        "tasks": [
            "汇总各服务 S05 测试计划",
            "识别跨服务测试场景",
            "规划测试环境",
        ],
        "outputs": [
            "cross_service_test_plan.md（跨服务测试计划）",
        ],
    },
    "P06-C": {
        "name": "测试验收",
        "tasks": [
            "汇总各服务 S07 测试结果",
            "通过率门槛判定",
            "质量门禁把控",
        ],
        "outputs": [
            "test_acceptance_report.md（测试验收报告）",
            "t01_approval.md（T01 放行证明）",
        ],
    },
    "S01": {
        "name": "技术方案设计",
        "tasks": [
            "数据库 Schema 设计",
            "API 接口定义",
            "技术难点攻克方案",
        ],
        "outputs": [
            "tech_design.md（技术设计文档）",
        ],
    },
    "S02": {
        "name": "任务拆解",
        "tasks": [
            "任务原子化拆解",
            "依赖关系分析",
            "工时预估",
        ],
        "outputs": [
            "task_plan.md（开发任务清单）",
        ],
    },
    "S03": {
        "name": "编码实现",
        "tasks": [
            "按任务清单编码",
            "遵循编码规范",
            "处理异常边界",
        ],
        "outputs": [
            "源代码文件",
            "Task_<ID>_Log.md（实现日志）",
        ],
    },
    "S04": {
        "name": "单元测试",
        "tasks": [
            "编写单元测试代码",
            "Mock/Stub 编写",
            "覆盖率分析",
        ],
        "outputs": [
            "单元测试代码",
            "unit_test_report.md（单测报告）",
        ],
    },
    "S05": {
        "name": "集成测试计划",
        "tasks": [
            "识别模块间接口风险",
            "设计集成测试场景",
            "规划测试数据",
        ],
        "outputs": [
            "integration_plan.md（集成测试计划）",
        ],
    },
    "S06": {
        "name": "代码审查",
        "tasks": [
            "静态代码分析",
            "安全性检查",
            "规范符合性检查",
        ],
        "outputs": [
            "code_review_report.md（代码审查报告）",
        ],
    },
    "S07": {
        "name": "集成测试执行",
        "tasks": [
            "执行集成测试脚本",
            "验证测试结果",
            "缺陷定位",
        ],
        "outputs": [
            "Test_Report.md（集成测试报告）",
        ],
    },
    "T01": {
        "name": "E2E测试",
        "tasks": [
            "黑盒测试用例设计",
            "Playwright UI 自动化",
            "PRD 功能点覆盖追溯",
        ],
        "outputs": [
            "测试用例.md（用例设计）",
            "E2E_Test_Report.md（测试报告）",
            "Playwright 脚本 + 截图",
        ],
    },
}


# ============ 阶段人工审核清单 ============
# 每个阶段放行前，用户应逐项确认的具体检查项

PHASE_REVIEW_CHECKLIST = {
    "P01": [
        "PRD 内容完整覆盖原始需求文档，无功能点遗漏",
        "图片资产已正确解析，描述与原图一致",
    ],
    "P02": [
        "知识库文档完整加载，无遗漏文件",
    ],
    "P03": [
        "标准化 PRD 功能点与原始需求一一对应",
        "原子功能拆解粒度合理（每个功能可独立开发/测试）",
        "技术可行性评估结论合理，风险已识别",
        "服务改造点拆解准确，未虚构不存在的改造",
    ],
    "P04": [
        "服务边界划分合理，职责清晰无交叉",
        "功能归属映射与原子功能清单一一对应，无遗漏",
        "执行模式（单/多服务）判定正确",
    ],
    "P05": [
        "接口索引与知识库记录一致，无虚构接口",
        "服务间依赖关系完整，无遗漏",
        "外部系统依赖已正确识别",
    ],
    "P06-A": [
        "跨服务接口契约（入参/出参/协议）一致",
        "数据流无断裂，端到端链路完整",
        "各服务技术方案不冲突，版本兼容",
    ],
    "P06-B": [
        "跨服务测试场景覆盖所有集成点",
        "测试环境和数据准备方案可行",
        "测试优先级排序合理",
    ],
    "P06-C": [
        "各服务测试通过率达到放行标准",
        "遗留缺陷已评估影响范围和风险等级",
        "质量门禁判定结论合理",
    ],
    "S01": [
        "技术方案与 PRD 需求点一一对齐",
        "API 接口定义清晰，入参/出参/错误码完整",
        "难点攻克方案可行，无过度设计",
    ],
    "S02": [
        "任务粒度适当（建议单任务 ≤ 4h 工时）",
        "任务依赖关系无循环，执行顺序合理",
        "无遗漏的功能实现任务（与 S01 设计对齐）",
    ],
    "S03": [
        "代码实现与 S01 技术设计一致",
        "异常处理和边界条件已覆盖",
        "变更日志与实际代码修改对应",
        "无硬编码、无安全隐患",
    ],
    "S04": [
        "测试用例覆盖核心业务逻辑路径",
        "边界条件和异常路径已有对应测试",
        "Mock/Stub 模拟合理，不掩盖真实问题",
    ],
    "S05": [
        "模块间所有接口交互场景已覆盖",
        "测试前置条件和数据准备方案明确",
        "TraceID 关联完整，可追溯",
    ],
    "S06": [
        "BLOCKER/MAJOR 级别问题已全部修复",
        "安全漏洞和性能瓶颈已识别并处理",
        "代码符合项目编码规范",
    ],
    "S07": [
        "测试用例执行结果与预期一致",
        "失败用例已定位根因并记录",
        "测试覆盖率满足集成测试计划要求",
    ],
    "T01": [
        "PRD 功能点覆盖追溯完整，无遗漏",
        "测试场景覆盖主要用户操作路径",
        "截图/录屏与预期行为一致",
        "自动化脚本可重复执行",
    ],
}


# ============ 状态图标映射 ============

STATUS_ICONS = {
    "not_started": "🔒",
    "ready": "👉",
    "in_progress": "🔄",
    "pending_review": "⏸️",
    "approved": "✅",
    "rejected": "❌",
    "blocked": "⛔",
}


# ============ next_action 配置 ============
# 用于 JSON 输出时告诉 AI 下一步应该做什么

NEXT_ACTION_CONFIG = {
    # === wait_input 类型（等待用户输入）===
    "progress": {
        "type": "wait_input",
        "instruction": "等待用户输入 1-N 或 g/b/q",
        "handlers": {
            "number": "执行阶段",
            "g": "--mode global",
            "b": "--mode list",
            "q": "--mode exit-summary"
        }
    },
    "list": {
        "type": "wait_input",
        "instruction": "等待用户选择需求",
        "handlers": {
            "number": "进入需求",
            "n": "新建需求"
        }
    },
    "feature-list": {
        "type": "wait_input",
        "instruction": "等待用户选择需求",
        "handlers": {
            "number": "进入需求",
            "n": "新建需求"
        }
    },
    "global": {
        "type": "wait_input",
        "instruction": "等待用户输入 b",
        "handlers": {
            "b": "--mode progress"
        }
    },
    "confirm": {
        "type": "wait_input",
        "instruction": "等待用户输入 y/n",
        "handlers": {
            "y": "执行阶段",
            "n": "--mode progress"
        }
    },
    "approval": {
        "type": "wait_input",
        "instruction": "等待用户输入 y/n/v",
        "handlers": {
            "y": "vibe_sync.py --action approve -> vibe_git_flow.py",
            "n": "--mode defer-approval -> --mode progress",
            "v": "读取产出文件 -> 重新显示"
        }
    },
    "phase-detail": {
        "type": "wait_input",
        "instruction": "等待用户输入 r/b",
        "handlers": {
            "r": "重新执行",
            "b": "--mode progress"
        }
    },
    "p03-select": {
        "type": "wait_input",
        "instruction": "等待用户输入 1/2",
        "handlers": {
            "1": "标准模式",
            "2": "快速模式"
        }
    },
    "git-confirm": {
        "type": "wait_input",
        "instruction": "等待用户输入 y/e/s",
        "handlers": {
            "y": "执行提交",
            "e": "编辑 message",
            "s": "跳过"
        }
    },
    "git-push-error": {
        "type": "wait_input",
        "instruction": "等待用户输入 r/s/m",
        "handlers": {
            "r": "重试",
            "s": "跳过",
            "m": "手动"
        }
    },
    "init-select": {
        "type": "wait_input",
        "instruction": "等待用户输入 1/2",
        "handlers": {
            "1": "Hub",
            "2": "Service"
        }
    },
    "interaction-select": {
        "type": "wait_input",
        "instruction": "等待用户输入 1/2",
        "handlers": {
            "1": "高级模式",
            "2": "引导模式"
        }
    },
    "hub-select": {
        "type": "wait_input",
        "instruction": "等待用户选择需求池",
        "handlers": {
            "number": "选择需求池"
        }
    },
    "service-select": {
        "type": "wait_input",
        "instruction": "等待用户选择服务",
        "handlers": {
            "number": "选择服务",
            "a": "全选"
        }
    },
    "upgrade-confirm": {
        "type": "wait_input",
        "instruction": "等待用户输入 y/n",
        "handlers": {
            "y": "升级",
            "n": "跳过"
        }
    },
    "advanced-prompt": {
        "type": "wait_input",
        "instruction": "等待用户输入命令",
        "handlers": {
            "command": "执行用户命令"
        }
    },

    # === auto_continue 类型（自动继续）===
    "banner": {
        "type": "auto_continue",
        "instruction": "继续执行 startup 流程"
    },
    "phase-start": {
        "type": "auto_continue",
        "instruction": "加载并执行提示词"
    },
    "context-preload": {
        "type": "auto_continue",
        "instruction": "继续执行 P01"
    },
    "defer-approval": {
        "type": "auto_continue",
        "instruction": "刷新菜单 (--mode progress)"
    },
    "git-sync": {
        "type": "auto_continue",
        "instruction": "继续后续流程"
    },
    "init-complete": {
        "type": "auto_continue",
        "instruction": "刷新菜单"
    },
    "refresh-status": {
        "type": "auto_continue",
        "instruction": "继续后续流程"
    },
    "path-summary": {
        "type": "auto_continue",
        "instruction": "继续后续流程"
    },
    "exit-summary": {
        "type": "auto_continue",
        "instruction": "结束会话"
    },

    # === wait_complete 类型（等待命令完成）===
    "git-progress": {
        "type": "wait_complete",
        "instruction": "等待 Git 命令完成"
    },
    "git-retry": {
        "type": "wait_complete",
        "instruction": "等待重试完成"
    },

    # === conditional 类型（条件判断）===
    "git-check": {
        "type": "conditional",
        "instruction": "根据 is_git_repo 判断",
        "condition_field": "is_git_repo"
    },
    "self-check": {
        "type": "conditional",
        "instruction": "根据 check_passed 判断",
        "condition_field": "check_passed"
    },
    "unlock-status": {
        "type": "conditional",
        "instruction": "根据 unlocked 判断",
        "condition_field": "unlocked"
    },
    "error": {
        "type": "conditional",
        "instruction": "根据 error_type 判断",
        "condition_field": "error_type"
    },
}


def get_next_action(mode: str) -> Optional[dict]:
    """获取指定 mode 的 next_action 配置"""
    return NEXT_ACTION_CONFIG.get(mode)


# ============ 工具函数 ============

def format_phase_status(phase_data: dict, is_unlocked: bool = True) -> Tuple[str, str]:
    """
    格式化阶段状态
    
    Returns:
        (图标, 状态文本)
    """
    status = phase_data.get("status", "not_started")
    
    if status == "approved":
        duration = calc_duration(
            phase_data.get("started_at"),
            phase_data.get("updated_at")
        )
        turns = phase_data.get("conversation_turns")
        turns_text = f", {turns}轮" if turns else ""
        return STATUS_ICONS["approved"], f"已放行 ({duration}{turns_text})"
    elif status == "pending_review":
        return STATUS_ICONS["pending_review"], "待确认"
    elif status == "in_progress":
        return STATUS_ICONS["in_progress"], "执行中"
    elif status == "not_started":
        if is_unlocked:
            return STATUS_ICONS["ready"], "待执行"
        else:
            return STATUS_ICONS["not_started"], "锁定"
    elif status == "blocked":
        return STATUS_ICONS["blocked"], "阻塞"
    else:
        return "❓", status


def get_mode_display(hub_status: dict, manifest: dict) -> str:
    """
    获取模式显示文本
    
    规则：
    - P04 未放行：显示 "未确定"
    - P04 已放行 + 单服务：显示 "单服务"
    - P04 已放行 + 多服务：显示 "多服务 (N个)"
    """
    p04_status = hub_status.get("phases", {}).get("P04", {}).get("status", "not_started")
    
    if p04_status != "approved":
        return "未确定"
    
    mode = manifest.get("mode", "single")
    
    if mode == "single":
        return "单服务"
    elif mode == "multi":
        services = manifest.get("services_multi_mode", [])
        if not services:
            services = manifest.get("services", [])
        count = len(services) if services else 0
        return f"多服务 ({count}个)"
    else:
        return "未确定"


# check_phase_unlock 已废弃，改用 check_p_unlock(feature, phase) 或 check_s_unlock(feature, phase)


def find_recommended_phase(phases_status: dict, phase_list: List[Tuple[str, str]], 
                           feature_name: str) -> Optional[str]:
    """找到推荐执行的下一个阶段（P 阶段）"""
    for phase_id, _ in phase_list:
        phase_data = phases_status.get(phase_id, {})
        status = phase_data.get("status", "not_started")
        
        # 跳过已完成的阶段
        if status == "approved":
            continue
        
        # 检查是否解锁
        is_unlocked = check_p_unlock(feature_name, phase_id)
        
        if is_unlocked and status in ["not_started", "pending_review"]:
            return phase_id
    
    return None


# ============ Hub 模式菜单渲染 ============

def render_hub_menu(
    hub_status: dict,
    service_statuses: list[dict],
    manifest: dict,
    feature_name: str,
    vaf_version: str = "v2.0.0",
    verbose: bool = False
) -> str:
    """
    渲染 Hub 模式（P 阶段）菜单
    
    Args:
        hub_status: hub_status.json 内容
        service_statuses: .service_status/*.json 内容列表（字典格式 {service_name: status}）
        manifest: execution_manifest.json 内容（可选）
        feature_name: 需求名称
        vaf_version: VAF 版本号
    
    Returns:
        完整的 ASCII 菜单字符串
    
    注意：
        - 多服务模式下（P04 已放行），会显示服务状态概览（只读）
        - 服务状态概览不提供执行入口，只显示状态
    """
    phases_status = hub_status.get("phases", {})
    mode_display = get_mode_display(hub_status, manifest)
    
    # 判断是否为多服务模式且 P04 已放行
    # 从 manifest 读取 mode（P04 产出），默认为 single
    mode = manifest.get("mode", "single")
    p04_approved = phases_status.get("P04", {}).get("status") == "approved"
    # 服务状态概览仅在详细模式（verbose=True）下显示
    show_service_overview = (mode == "multi" and p04_approved and verbose)
    
    # 找推荐阶段
    recommended = find_recommended_phase(
        phases_status, P_PHASES, feature_name
    )
    
    # 统计进度
    approved_count = sum(
        1 for p, _ in P_PHASES 
        if phases_status.get(p, {}).get("status") == "approved"
    )
    total_count = len(P_PHASES)
    progress_bar = "█" * approved_count + "░" * (total_count - approved_count)
    
    # 构建菜单
    lines = [
        f"📋 需求: {feature_name}  |  模式: {mode_display}  |  VAF {vaf_version}",
    ]
    
    # P04 未放行时增加模式提示
    if mode_display == "未确定":
        lines.append("💡 提示：服务模式将在完成 P04 服务分解后确定（单服务/多服务）")
    
    lines.extend([
        "",
        "┌─────────────────────────────────────────────────────────────────┐",
        "│  P 阶段 (需求池)                                                 │",
        "├─────────────────────────────────────────────────────────────────┤",
    ])
    
    for i, (phase_id, phase_name) in enumerate(P_PHASES):
        phase_data = phases_status.get(phase_id, {"status": "not_started"})
        is_unlocked = check_p_unlock(feature_name, phase_id)
        
        icon, status_text = format_phase_status(phase_data, is_unlocked)
        
        # 推荐标记
        recommend_mark = " <- 推荐下一步" if phase_id == recommended else ""
        
        # 格式化行内容
        line_content = f"[{i+1}] {icon} {phase_id} {phase_name:<10} {status_text}{recommend_mark}"
        
        # 填充到固定宽度（考虑中文字符宽度）
        # 简化处理：直接使用固定格式
        lines.append(f"│  {line_content:<59} │")
    
    # 多服务模式：显示服务状态概览（只读）
    if show_service_overview and service_statuses:
        lines.extend([
            "├─────────────────────────────────────────────────────────────────┤",
            "│  📊 服务状态概览（只读）                                         │",
            "├─────────────────────────────────────────────────────────────────┤",
        ])
        
        # 处理 service_statuses（可能是 list 或 dict）
        if isinstance(service_statuses, dict):
            service_items = service_statuses.items()
        else:
            # 兼容旧格式：list of dicts
            service_items = [(s.get("service", "unknown"), s) for s in service_statuses]
        
        for service_name, service_status in service_items:
            phases = service_status.get("phases", {})
            
            # 计算服务进度
            all_phases = S_PHASES + T_PHASES
            approved = sum(1 for p, _ in all_phases if phases.get(p, {}).get("status") == "approved")
            total = len(all_phases)
            
            # 找到当前阶段
            current_phase = "完成" if approved == total else "未开始"
            for p, _ in all_phases:
                status = phases.get(p, {}).get("status", "not_started")
                if status == "pending_review":
                    current_phase = f"{p} 待确认"
                    break
                elif status != "approved":
                    current_phase = f"{p} {'进行中' if status == 'in_progress' else '未开始'}"
                    break
            
            # 进度条
            svc_progress = "█" * approved + "░" * (total - approved)
            
            # 截断服务名（最多28字符）
            svc_display = service_name[:28] if len(service_name) > 28 else service_name
            
            line = f"│  📦 {svc_display:<28} {current_phase:<10} {svc_progress} {approved}/{total}"
            lines.append(f"{line:<65}│")
        
        # 底部提示
        lines.extend([
            "├─────────────────────────────────────────────────────────────────┤",
            "│  💡 S 阶段请在各服务目录中执行，Hub 不提供执行入口              │",
        ])
    
    # 显示模式切换文本
    mode_text = "详细" if verbose else "简洁"
    
    lines.extend([
        "├─────────────────────────────────────────────────────────────────┤",
        f"│  进度: {progress_bar} {approved_count}/{total_count}                                      │",
        f"│  [v] 切换显示({mode_text})  [g] 全局进度  [b] 返回  [q] 退出    │",
        "└─────────────────────────────────────────────────────────────────┘",
        "",
        "请选择：",
    ])
    
    return "\n".join(lines)


# ============ Service 模式菜单渲染 ============

def render_service_menu(
    phase_status: dict,
    service_name: str,
    feature_name: str,
    vaf_version: str = "v2.0.0",
    verbose: bool = False
) -> str:
    """
    渲染 Service 模式（S 阶段）菜单（简化版，P06 门禁状态内部自动获取）
    
    Args:
        phase_status: phase_status.json 内容
        service_name: 服务名称
        feature_name: 需求名称
        vaf_version: VAF 版本号
        verbose: 是否详细模式
    
    Returns:
        完整的 ASCII 菜单字符串
    """
    phases_status = phase_status.get("phases", {})
    
    # 合并 S 阶段和 T 阶段
    all_phases = S_PHASES + T_PHASES
    
    # 找推荐阶段
    recommended = None
    for phase_id, _ in all_phases:
        phase_data = phases_status.get(phase_id, {"status": "not_started"})
        status = phase_data.get("status", "not_started")
        
        if status == "approved":
            continue
        
        is_unlocked = check_s_unlock(feature_name, phase_id)
        if is_unlocked and status in ["not_started", "pending_review"]:
            recommended = phase_id
            break
    
    # 统计进度
    approved_count = sum(
        1 for p, _ in all_phases 
        if phases_status.get(p, {}).get("status") == "approved"
    )
    total_count = len(all_phases)
    progress_bar = "█" * approved_count + "░" * (total_count - approved_count)
    
    # 构建菜单
    lines = [
        f"📋 服务: {service_name}  |  需求: {feature_name}  |  VAF {vaf_version}",
        "",
        "┌─────────────────────────────────────────────────────────────────┐",
        "│  S 阶段 (服务开发)                                               │",
        "├─────────────────────────────────────────────────────────────────┤",
    ]
    
    for i, (phase_id, phase_name) in enumerate(all_phases):
        phase_data = phases_status.get(phase_id, {"status": "not_started"})
        is_unlocked = check_s_unlock(feature_name, phase_id)
        
        icon, status_text = format_phase_status(phase_data, is_unlocked)
        
        # 推荐标记
        recommend_mark = " <- 推荐下一步" if phase_id == recommended else ""
        
        # 格式化行内容
        line_content = f"[{i+1}] {icon} {phase_id} {phase_name:<12} {status_text}{recommend_mark}"
        
        lines.append(f"│  {line_content:<59} │")
    
    # 显示模式切换文本
    mode_text = "详细" if verbose else "简洁"
    
    lines.extend([
        "├─────────────────────────────────────────────────────────────────┤",
        f"│  进度: {progress_bar} {approved_count}/{total_count}                                      │",
        f"│  [v] 切换显示({mode_text})  [g] 全局进度  [h] Hub  [q] 退出     │",
        "└─────────────────────────────────────────────────────────────────┘",
        "",
        "请选择：",
    ])
    
    return "\n".join(lines)


# ============ 需求列表渲染 ============

def render_feature_list(features: list[str], mode: str = "hub") -> str:
    """
    渲染需求列表菜单
    
    Args:
        features: 需求名称列表
        mode: 模式（hub/service）
    
    Returns:
        ASCII 格式的需求列表
    """
    mode_name = "需求池" if mode == "hub" else "服务目录"
    
    lines = [
        f"🚀 Vibe Flow - {mode_name}模式",
        "",
        "检测到以下需求：",
    ]
    
    for i, feature in enumerate(features):
        lines.append(f"  [{i+1}] {feature}")
    
    lines.extend([
        "  [0] 创建新需求",
        "",
        "请选择：",
    ])
    
    return "\n".join(lines)


# ============ 阶段执行确认面板 ============

def render_phase_confirm(
    phase: str,
    status: str = "not_started"
) -> str:
    """
    渲染阶段执行确认面板
    
    Args:
        phase: 阶段 ID（如 P01, S01）
        status: 当前状态
    
    Returns:
        ASCII 格式的确认面板
    
    注意：不包含预计耗时（因为不准确）
    """
    # 获取阶段详情
    details = PHASE_DETAILS.get(phase)
    if not details:
        return f"❌ 未知阶段: {phase}"
    
    phase_name = details["name"]
    tasks = details["tasks"]
    outputs = details["outputs"]
    
    # 状态显示
    if status == "not_started":
        status_text = "待执行"
    elif status == "pending_review":
        status_text = "待确认"
    elif status == "in_progress":
        status_text = "执行中"
    elif status == "approved":
        status_text = "已放行"
    else:
        status_text = status
    
    # 固定宽度（65 字符内容区）
    BOX_WIDTH = 65
    
    def pad_line(content: str) -> str:
        """填充行到固定宽度，考虑中文字符宽度"""
        # 计算实际显示宽度（中文字符算2，ASCII算1）
        display_width = 0
        for char in content:
            if ord(char) > 127:
                display_width += 2
            else:
                display_width += 1
        
        padding = BOX_WIDTH - display_width
        if padding > 0:
            return content + " " * padding
        return content[:BOX_WIDTH]  # 截断
    
    # 构建面板
    border = "─" * BOX_WIDTH
    lines = [
        f"┌{border}┐",
        f"│{' ' * BOX_WIDTH}│",
        f"│  📋 {phase} {phase_name} [{status_text}]" + " " * (BOX_WIDTH - len(f"  📋 {phase} {phase_name} [{status_text}]") - 2) + "│",
        f"│{' ' * BOX_WIDTH}│",
        f"├{border}┤",
        f"│{' ' * BOX_WIDTH}│",
        f"│{pad_line('  核心任务：')}│",
    ]
    
    for task in tasks:
        lines.append(f"│{pad_line(f'    • {task}')}│")
    
    lines.extend([
        f"│{' ' * BOX_WIDTH}│",
        f"│{pad_line('  预计产出：')}│",
    ])
    
    for output in outputs:
        lines.append(f"│{pad_line(f'    ✅ {output}')}│")
    
    lines.extend([
        f"│{' ' * BOX_WIDTH}│",
        f"├{border}┤",
        f"│{pad_line('  [y] ✅ 确认执行')}│",
        f"│{pad_line('  [n] ❌ 返回菜单')}│",
        f"└{border}┘",
        "",
        "请选择：",
    ])
    
    return "\n".join(lines)


# ============ 已放行阶段详情 ============

def render_phase_detail(
    phase: str,
    status: str = "approved",
    duration: str = "--",
    approved_at: str = "",
    outputs: list[str] = None,
    output_path: str = "",
    token_usage: int = None,
    conversation_turns: int = None
) -> str:
    """
    渲染已放行阶段的详情视图
    
    Args:
        phase: 阶段 ID
        status: 阶段状态
        duration: 执行耗时
        approved_at: 放行时间
        outputs: 产出文件列表
        output_path: 产出物路径
        token_usage: Token 消耗（可选，已废弃）
        conversation_turns: 对话轮次（可选）
    
    Returns:
        ASCII 格式的阶段详情
    """
    details = PHASE_DETAILS.get(phase, {})
    phase_name = details.get("name", phase)
    
    # 状态显示
    if status == "approved":
        status_display = "[已放行 ✅]"
    elif status == "pending_review":
        status_display = "[待确认 ⏸️]"
    elif status == "in_progress":
        status_display = "[执行中 🔄]"
    else:
        status_display = f"[{status}]"
    
    BOX_WIDTH = 65
    border = "─" * BOX_WIDTH
    
    lines = [
        f"┌{border}┐",
        f"│  📋 {phase} {phase_name} {status_display}" + " " * (BOX_WIDTH - len(f"  📋 {phase} {phase_name} {status_display}") + 2) + "│",
        f"├{border}┤",
        f"│" + " " * BOX_WIDTH + "│",
    ]
    
    # 执行信息
    if duration and duration != "--":
        lines.append(f"│  ⏱️ 执行时间：{duration}" + " " * (BOX_WIDTH - len(f"  ⏱️ 执行时间：{duration}") - 4) + "│")
    
    if conversation_turns is not None:
        lines.append(f"│  🔄 对话轮次：{conversation_turns}" + " " * (BOX_WIDTH - len(f"  🔄 对话轮次：{conversation_turns}") - 4) + "│")
    
    if token_usage is not None:
        token_display = f"{token_usage:,}"  # 千分位格式
        lines.append(f"│  🔢 Token 消耗：{token_display}" + " " * (BOX_WIDTH - len(f"  🔢 Token 消耗：{token_display}") - 4) + "│")
    
    if approved_at:
        lines.append(f"│  📅 放行时间：{approved_at}" + " " * (BOX_WIDTH - len(f"  📅 放行时间：{approved_at}") - 4) + "│")
    
    lines.append(f"│" + " " * BOX_WIDTH + "│")
    
    # 产出物
    if outputs:
        lines.append(f"│  📦 产出物：" + " " * (BOX_WIDTH - len("  📦 产出物：") - 4) + "│")
        for output in outputs:
            line = f"│     ✅ {output}"
            padding = BOX_WIDTH - len(f"     ✅ {output}") - 2
            lines.append(line + " " * max(0, padding) + "│")
    
    lines.append(f"│" + " " * BOX_WIDTH + "│")
    
    # 路径
    if output_path:
        lines.append(f"│  📁 路径：{output_path}" + " " * max(0, BOX_WIDTH - len(f"  📁 路径：{output_path}") - 4) + "│")
        lines.append(f"│" + " " * BOX_WIDTH + "│")
    
    # 选项
    lines.extend([
        f"├{border}┤",
        f"│  [v] 👁️ 查看产出" + " " * (BOX_WIDTH - len("  [v] 👁️ 查看产出") - 2) + "│",
        f"│  [e] ✏️ 修改产物" + " " * (BOX_WIDTH - len("  [e] ✏️ 修改产物") - 2) + "│",
        f"│  [r] 🔄 重新执行" + " " * (BOX_WIDTH - len("  [r] 🔄 重新执行") - 2) + "│",
        f"│  [b] ↩️ 返回菜单" + " " * (BOX_WIDTH - len("  [b] ↩️ 返回菜单") - 2) + "│",
        f"└{border}┘",
        "",
        "请选择：",
    ])
    
    return "\n".join(lines)


# ============ 产物导读配置 ============
# 为每个阶段的关键产物提供阅读指引，帮助用户快速定位重点
# 尤其是 TOON 格式文件，标注核心区域和审查要点

PHASE_READING_GUIDE = {
    "P03": {
        "title": "PRD评审产物导读",
        "files": [
            {
                "name": "atomic_functions.toon",
                "format": "TOON",
                "purpose": "原子功能清单 — PRD 拆解为可开发的最小功能单元",
                "focus_areas": [
                    "⭐ FUNCTIONS[] → 必看！重点看 func_name、acceptance_criteria(验收标准)",
                ],
                "quick_check": "META.total_functions 是否与 PRD 功能点数量匹配",
            },
        ],
    },
    "P04": {
        "title": "服务分解产物导读",
        "files": [
            {
                "name": "service_split_decision.toon",
                "format": "TOON",
                "purpose": "服务拆分决策 — 功能到服务的归属映射",
                "focus_areas": [
                    "⭐ function_assignments[] → 每个功能归属哪个服务、承担什么角色(orchestrator/provider)",
                    "services[] → 服务列表及角色(primary/collaborator)、分层(gateway/core)",
                    "service_dependencies[] → 服务间调用关系",
                ],
                "quick_check": "function_assignments 数量 ≥ atomic_functions 的功能数",
            },
        ],
    },
    "P05": {
        "title": "概要设计产物导读",
        "files": [
            {
                "name": "service_inputs.toon (CHAPTER_2)",
                "format": "TOON",
                "purpose": "服务依赖补充 — P05 填充接口索引和依赖信息",
                "focus_areas": [
                    "⭐ CH2 interfaces[] → 从知识库提取的已有接口清单，确认接口是否准确",
                    "CH2 services[] → 服务间协作关系(upstream/downstream)",
                ],
                "quick_check": "interfaces[] 不应为空(除非是全新服务)",
            },
        ],
    },
}


# ============ 放行确认框 ============

def render_approval_confirm(
    phase: str,
    outputs: list[str],
    duration: str = "--"
) -> str:
    """
    渲染放行确认框
    
    Args:
        phase: 阶段 ID
        outputs: 产出文件列表
        duration: 耗时字符串
    
    Returns:
        ASCII 格式的放行确认框
    """
    details = PHASE_DETAILS.get(phase, {})
    phase_name = details.get("name", phase)
    
    BOX_WIDTH = 65
    border = "─" * BOX_WIDTH
    heavy_border = "━" * (BOX_WIDTH - 2)  # 减去左右 ┃ 各占 1 列
    INNER_WIDTH = BOX_WIDTH - 2  # ┃ 内部可用宽度
    
    def _display_width(text: str) -> int:
        """计算终端显示宽度（CJK/emoji 算 2，ASCII 算 1）"""
        return sum(2 if ord(c) > 127 else 1 for c in text)
    
    def pad_box(content: str) -> str:
        """填充到 INNER_WIDTH 并包裹 ┃"""
        dw = _display_width(content)
        padding = INNER_WIDTH - dw
        return f"┃{content}{' ' * max(0, padding)}┃"
    
    # 获取当前 IDE 和可选模型列表
    ide_tool = _get_current_ide()
    model_options = _get_model_options_for_ide(ide_tool)
    
    lines = [
        border,
        f"✅ {phase} {phase_name} 执行完成！",
        "",
        "📤 产出物：",
    ]
    
    for output in outputs:
        lines.append(f"   ✅ {output}")
    
    lines.extend([
        "",
        f"⏱️ 耗时: {duration}",
        border,
        "",
        f"┏{heavy_border}┓",
        pad_box("  ⛔ 请勿盲目放行 — 您是质量的最终负责人，而非 AI。"),
        f"┗{heavy_border}┛",
        "",
    ])
    
    # 阶段专属人工审核清单
    checklist = PHASE_REVIEW_CHECKLIST.get(phase, [])
    if checklist:
        lines.append(f"📋 {phase} {phase_name} — 放行前请逐项确认：")
        for i, item in enumerate(checklist, 1):
            if i <= 9:
                num_emoji = f"{i}\ufe0f\u20e3"  # 1️⃣ 2️⃣ ... 9️⃣
            else:
                num_emoji = f"{i}."  # 10. 11. ...
            lines.append(f"   {num_emoji} {item}")
        lines.append("")
    else:
        lines.extend([
            "📋 放行前请确认：",
            "   1️⃣ 阅读并理解产出文档",
            "   2️⃣ 确认业务逻辑正确、完整",
            "   3️⃣ 确认技术方案可行、无重大风险",
            "",
        ])
    
    # 产物导读（仅含 TOON 文件的阶段才显示）
    reading_guide = PHASE_READING_GUIDE.get(phase)
    if reading_guide:
        lines.append(f"📖 {reading_guide['title']}")
        lines.append(f"   （TOON 格式速读：key[N]{{字段列表}}: 表示表格数组，每行一条记录）")
        lines.append("")
        for file_info in reading_guide["files"]:
            fmt_tag = f"[{file_info['format']}]" if file_info.get("format") else ""
            lines.append(f"   📄 {file_info['name']} {fmt_tag}")
            lines.append(f"      用途: {file_info['purpose']}")
            lines.append(f"      重点关注:")
            for area in file_info.get("focus_areas", []):
                lines.append(f"        · {area}")
            if file_info.get("quick_check"):
                lines.append(f"      ⚡ 快速校验: {file_info['quick_check']}")
            lines.append("")
    
    # 仅当 IDE 识别准确时才展示（不是"其他"）
    if ide_tool and ide_tool != "其他":
        lines.append(f"🤖 当前 IDE: {ide_tool}")
    lines.append("🤖 您运行当前阶段使用的模型：")
    
    # 模型选项横向排列（每行3个）
    model_lines = []
    for i, model in enumerate(model_options, 1):
        model_lines.append(f"[{i}] {model}")
    model_lines.append("[0] 其他(直接输入模型名称)")
    
    # 每行3个模型
    for i in range(0, len(model_lines), 3):
        row = "  " + "  ".join(model_lines[i:i+3])
        lines.append(row)
    
    # 操作选项横向排列
    lines.extend([
        "",
        "  [y] ✅ 确认放行  [n] ⏸️ 暂不放行  [v] 👁️ 查看产出  [e] ✏️ 修改  [r] 🔄 重新执行",
        "",
        "💡 放行格式: y,3 或 y（默认第1个）",
        "",
        "请选择：",
    ])
    
    return "\n".join(lines)


def _get_current_ide() -> str:
    """获取当前 IDE 工具名称"""
    try:
        from vaf_telemetry import get_ide_tool
        return get_ide_tool()
    except Exception:
        return "其他"


def _get_model_options_for_ide(ide_tool: str) -> list[str]:
    """获取指定 IDE 的可选模型列表"""
    try:
        from vaf_telemetry import get_ide_model_options, _DEFAULT_MODELS
        return get_ide_model_options(ide_tool)
    except Exception:
        # 兜底：与 vaf_telemetry._DEFAULT_MODELS 保持一致
        return [
            "GPT-5.1-Codex",
            "GPT-5.2-Codex",
            "GPT-5.1",
            "GPT-5.2",
            "Claude Opus 4.5",
            "Claude Sonnet 4.5",
            "Gemini 3 Pro",
            "Gemini-2.5-Pro",
            "GLM 4.7",
            "MiMo-V2-Flash",
            "DeepSeek-V3.1",
        ]


# ============ Git 仓库检测 ============

def check_git_repo(path: str = None) -> dict:
    """
    检测指定路径是否为 Git 仓库
    
    Args:
        path: 要检测的路径，默认为当前目录
    
    Returns:
        {
            "is_git_repo": bool,
            "git_root": str | null,
            "current_branch": str | null
        }
    """
    cwd = Path(path) if path else Path.cwd()
    
    result = {
        "is_git_repo": False,
        "git_root": None,
        "current_branch": None
    }
    
    try:
        # 检测是否在 Git 仓库内
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        if proc.returncode != 0:
            return result
        
        result["is_git_repo"] = True
        
        # 获取 Git 根目录
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        if proc.returncode == 0:
            result["git_root"] = proc.stdout.strip()
        
        # 获取当前分支
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            encoding='utf-8',
            errors='replace',
            capture_output=True,
            text=True
        )
        if proc.returncode == 0:
            result["current_branch"] = proc.stdout.strip()
        
    except Exception:
        pass
    
    return result


# ============ Git 提交确认框 ============

def render_git_confirm(
    phase: str,
    feature: str,
    changed_files: list[str],
    commit_message: str,
    is_service_mode: bool = False,
    service_name: str = "",
    hub_changed_files: list[str] = None,
    is_git_repo: bool = True,
    hub_is_git_repo: bool = True
) -> str:
    """
    渲染 Git 提交确认框
    
    Args:
        phase: 阶段 ID
        feature: 需求名称
        changed_files: 变更文件列表
        commit_message: AI 生成的提交信息
        is_service_mode: 是否为服务模式
        service_name: 服务名称
        hub_changed_files: Hub 变更文件列表（服务模式用）
        is_git_repo: 当前目录是否为 Git 仓库
        hub_is_git_repo: Hub 目录是否为 Git 仓库（服务模式用）
    """
    details = PHASE_DETAILS.get(phase, {})
    phase_name = details.get("name", phase)
    
    # 判断是否需要显示 Git 提交选项
    # Hub 模式：检查 is_git_repo
    # Service 模式：检查 is_git_repo 和 hub_is_git_repo
    show_git_options = is_git_repo
    if is_service_mode and hub_changed_files:
        # 服务模式下，如果有 Hub 变更，需要两边都是 Git 仓库
        show_git_options = is_git_repo and hub_is_git_repo
    
    # 如果不是 Git 仓库，直接返回简洁的放行成功信息
    if not show_git_options:
        lines = [
            f"✅ 已放行 {phase} {phase_name}，下一阶段已解锁",
            "",
        ]
        return "\n".join(lines)
    
    BOX_WIDTH = 65
    border = "─" * BOX_WIDTH
    
    lines = [
        f"✅ 已放行 {phase} {phase_name}，下一阶段已解锁",
        "",
        border,
        "📦 提交变更",
        "",
    ]
    
    if is_service_mode and service_name:
        lines.append(f"📁 {service_name} (当前服务):")
        for f in changed_files:
            lines.append(f"  M  {f}")
        
        if hub_changed_files:
            lines.extend([
                "",
                "📁 Hub (状态同步):",
            ])
            for f in hub_changed_files:
                lines.append(f"  M  {f}")
    else:
        lines.append(f"变更文件 ({len(changed_files)}):")
        for f in changed_files:
            lines.append(f"  M  {f}")
    
    lines.extend([
        "",
        "提交信息（AI 生成）：",
        "┌─────────────────────────────────────────────────────────────────┐",
    ])
    
    for line in commit_message.split("\n"):
        lines.append(f"│  {line:<61} │")
    
    lines.extend([
        "└─────────────────────────────────────────────────────────────────┘",
        "",
        "  [y] ✅ 提交（使用上方 message）",
        "  [e] ✏️ 编辑 commit message 后提交",
        "  [s] ⏭️ 跳过提交",
        "",
        "请选择：",
    ])
    
    return "\n".join(lines)


# ============ 全局进度视图 ============

def render_global_progress(
    hub_status: dict,
    service_statuses: dict,
    feature: str,
    vaf_version: str = "v2.0.0",
    mode: str = "single"
) -> str:
    """
    渲染全局进度视图
    
    Args:
        hub_status: Hub 状态
        service_statuses: 服务状态字典 {service_name: phase_status}
        feature: 需求名称
        vaf_version: VAF 版本号
        mode: 执行模式 (single/multi)，单服务不显示协调阶段
    """
    hub_phases = hub_status.get("phases", {})
    
    # 截断需求名（最多25字符）
    feature_display = feature[:25] if len(feature) > 25 else feature
    
    # 辅助函数：创建带边框的行
    def row(content: str) -> str:
        # 固定宽度 60 字符（不含边框）
        return f"│  {content:<56}│"
    
    def separator() -> str:
        return "├" + "─" * 60 + "┤"
    
    lines = [
        f"🌐 全局进度视图  |  需求: {feature_display}  |  VAF {vaf_version}",
        "",
        "┌" + "─" * 60 + "┐",
        row("📋 需求池 (Hub)"),
        separator(),
    ]
    
    # P01-P05 阶段进度
    p_phases_main = [("P01", "需求采集"), ("P02", "知识库加载"), ("P03", "PRD评审"), 
                     ("P04", "服务分解"), ("P05", "概要设计")]
    
    for phase_id, phase_name in p_phases_main:
        phase_data = hub_phases.get(phase_id, {"status": "not_started"})
        status = phase_data.get("status", "not_started")
        icon = STATUS_ICONS.get(status, "❓")
        
        if status == "approved":
            duration = calc_duration(phase_data.get("started_at"), phase_data.get("updated_at"))
            status_text = f"已放行 ({duration})"
        elif status == "pending_review":
            status_text = "待确认"
        elif status == "not_started":
            status_text = "未开始"
        else:
            status_text = status
        
        lines.append(row(f"{icon} {phase_id} {phase_name}  {status_text}"))
    
    # 协调阶段（仅多服务模式显示）
    if mode == "multi":
        lines.append(separator())
        lines.append(row("🔗 协调阶段"))
        lines.append(separator())
        
        coord_phases = [
            ("P06-A", "方案协调", "所有服务 S01 完成"),
            ("P06-B", "测试协调", "P06-A + 所有服务 S05"),
            ("P06-C", "验收协调", "所有服务 S07 完成"),
        ]
        
        for phase_id, phase_name, condition in coord_phases:
            phase_data = hub_phases.get(phase_id, {"status": "not_started"})
            status = phase_data.get("status", "not_started")
            icon = "🔒" if status == "not_started" else STATUS_ICONS.get(status, "🔒")
            
            if status == "approved":
                duration = calc_duration(phase_data.get("started_at"), phase_data.get("updated_at"))
                status_text = f"已放行 ({duration})"
            elif status == "pending_review":
                status_text = "待确认"
            else:
                status_text = f"等待: {condition}"
            
            lines.append(row(f"{icon} {phase_id} {phase_name}  {status_text}"))
    
    # T01
    t01_data = hub_phases.get("T01", {"status": "not_started"})
    t01_status = t01_data.get("status", "not_started")
    t01_icon = "🔒" if t01_status == "not_started" else STATUS_ICONS.get(t01_status, "🔒")
    
    if t01_status == "approved":
        t01_duration = calc_duration(t01_data.get("started_at"), t01_data.get("updated_at"))
        t01_text = f"已放行 ({t01_duration})"
    elif t01_status == "pending_review":
        t01_text = "待确认"
    else:
        # 单服务只需等待 S07，多服务需要 S07 + P06-C
        t01_text = "等待: S07 + P06-C" if mode == "multi" else "等待: S07"
    
    lines.append(row(f"{t01_icon} T01 端到端测试  {t01_text}"))
    
    # 服务开发进度
    lines.append(separator())
    lines.append(row("📦 服务开发进度"))
    lines.append(separator())
    
    if not service_statuses:
        lines.append(row("(暂无服务状态)"))
    else:
        # 兼容 dict 和 list 格式
        if isinstance(service_statuses, dict):
            service_items = list(service_statuses.items())
        else:
            service_items = [(s.get("service", "unknown"), s) for s in service_statuses]
        
        for idx, (service_name, phase_status) in enumerate(service_items):
            phases = phase_status.get("phases", {})
            
            # 服务名
            svc_label = f"{service_name} (主服务)" if idx == 0 else service_name
            lines.append(row(""))
            lines.append(row(svc_label))
            
            # S01-S07 流程线
            s_flow_parts = []
            for s_phase, _ in S_PHASES:
                s_status = phases.get(s_phase, {}).get("status", "not_started")
                if s_status == "approved":
                    s_icon = "✅"
                elif s_status == "pending_review":
                    s_icon = "🟡"
                elif s_status == "in_progress":
                    s_icon = "🔵"
                else:
                    s_icon = "⚪"
                s_flow_parts.append(f"{s_phase} {s_icon}")
            
            flow_line = " → ".join(s_flow_parts)
            lines.append(row(flow_line))
        
        lines.append(row(""))
    
    # 进度统计
    lines.append(separator())
    lines.append(row("📊 进度统计"))
    lines.append(separator())
    
    # 计算各项进度
    p_approved = sum(1 for p, _ in P_PHASES if hub_phases.get(p, {}).get("status") == "approved")
    p_total = len(P_PHASES)
    p_pct = int(p_approved / p_total * 100) if p_total > 0 else 0
    
    s_approved = 0
    s_total = 0
    if service_statuses:
        if isinstance(service_statuses, dict):
            service_items = list(service_statuses.values())
        else:
            service_items = service_statuses
        for svc in service_items:
            phases = svc.get("phases", {})
            for s_phase, _ in S_PHASES:
                s_total += 1
                if phases.get(s_phase, {}).get("status") == "approved":
                    s_approved += 1
    s_pct = int(s_approved / s_total * 100) if s_total > 0 else 0
    
    total_approved = p_approved + s_approved
    total_all = p_total + s_total
    total_pct = int(total_approved / total_all * 100) if total_all > 0 else 0
    
    def make_bar(done, total, width=16):
        filled = int(done / total * width) if total > 0 else 0
        return "█" * filled + "░" * (width - filled)
    
    p_bar = make_bar(p_approved, p_total)
    s_bar = make_bar(s_approved, s_total) if s_total > 0 else "░" * 16
    t_bar = make_bar(total_approved, total_all)
    
    lines.append(row(f"Hub P阶段:  {p_bar}  {p_approved}/{p_total} ({p_pct}%)"))
    lines.append(row(f"服务 S阶段: {s_bar}  {s_approved}/{s_total} ({s_pct}%)"))
    lines.append(row(f"整体进度:   {t_bar}  {total_approved}/{total_all} ({total_pct}%)"))
    
    lines.extend([
        "└" + "─" * 60 + "┘",
        "",
        "图例: ✅ 已放行  🟡 待确认  ⚪ 未开始  🔒 锁定",
        "",
        "[B] 返回主菜单",
        "",
        "请选择：",
    ])
    
    return "\n".join(lines)


# ============ P03 模式选择界面 ============

def render_p03_mode_select() -> str:
    """
    渲染 P03 执行模式选择界面
    """
    BOX_WIDTH = 65
    border = "─" * BOX_WIDTH
    
    lines = [
        "执行模式选择：",
        f"┌{border}┐",
        f"│  🔹 标准模式（推荐）                                           │",
        f"│                                                                 │",
        f"│  适用场景：                                                     │",
        f"│    • PRD 结构复杂，章节较多                                     │",
        f"│    • 首次执行 P03，需要确保目录映射准确                         │",
        f"│    • 对产出质量要求高的正式项目                                 │",
        f"│                                                                 │",
        f"│  执行特点：                                                     │",
        f"│    • 完整执行 4 个步骤                                          │",
        f"│    • Step 1 会展示目录映射对账表，人工确认 UNMATCHED 项         │",
        f"│    • 耗时较长，但映射准确率高                                   │",
        f"├{border}┤",
        f"│  ⚡ 快速模式                                                    │",
        f"│                                                                 │",
        f"│  适用场景：                                                     │",
        f"│    • PRD 结构简单，章节与模板高度匹配                           │",
        f"│    • 迭代执行 P03，之前已确认过目录映射                         │",
        f"│    • 对执行速度有要求的场景                                     │",
        f"│                                                                 │",
        f"│  执行特点：                                                     │",
        f"│    • 跳过 Step 1 的人工确认环节                                 │",
        f"│    • UNMATCHED 项自动归入「其他说明」章节                       │",
        f"│    • 耗时较短，但可能存在映射偏差                               │",
        f"└{border}┘",
        "",
        "╭─────────────────────────────────────────────────────────────────╮",
        "│  [1] 标准模式 - 需人工确认目录映射，适合首次执行或复杂 PRD      │",
        "│  [2] 快速模式 - 跳过人工确认，自动处理未匹配项，适合迭代执行    │",
        "╰─────────────────────────────────────────────────────────────────╯",
        "",
        "请选择 1 或 2：",
    ]
    
    return "\n".join(lines)


# ============ 工作区类型选择界面 ============

def render_init_select() -> str:
    """
    渲染工作区类型选择界面（首次初始化）
    """
    BOX_WIDTH = 65
    border = "─" * BOX_WIDTH
    
    lines = [
        "🚀 Vibe Agentic Flow 初始化",
        "",
        f"┌{border}┐",
        f"│  请选择工作区类型：                                              │",
        f"│                                                                 │",
        f"│  [1] 📋 需求池 (Hub)                                             │",
        f"│      • 管理需求文档和状态                                        │",
        f"│      • 执行 P 阶段（需求分析、PRD 评审等）                        │",
        f"│      • 协调多服务开发进度                                        │",
        f"│                                                                 │",
        f"│  [2] 💻 服务仓库 (Service)                                       │",
        f"│      • 开发具体服务代码                                          │",
        f"│      • 执行 S 阶段（技术设计、编码、测试等）                      │",
        f"│      • 状态自动同步回 Hub                                        │",
        f"│                                                                 │",
        f"└{border}┘",
        "",
        "请选择（输入 1 或 2）：",
    ]
    
    return "\n".join(lines)


# ============ 交互模式选择界面 ============

def render_interaction_select() -> str:
    """
    渲染交互模式选择界面（高级模式/引导模式）
    
    用于首次使用或 local_config.json 不存在时
    """
    BOX_WIDTH = 65
    border = "─" * BOX_WIDTH
    
    lines = [
        "🎛️ 交互模式选择",
        "",
        f"┌{border}┐",
        f"│  请选择您偏好的交互模式：                                        │",
        f"│                                                                 │",
        f"│  [1] 🎮 高级模式                                                 │",
        f"│      • 直接输入命令（P01/S01/status/menu）                       │",
        f"│      • 无菜单引导，适合熟悉流程的用户                            │",
        f"│      • 精简输出，提高效率                                        │",
        f"│                                                                 │",
        f"│  [2] 📋 引导模式（推荐）                                         │",
        f"│      • 完整菜单，选择编号执行                                    │",
        f"│      • 逐步引导，适合新手                                        │",
        f"│      • 详细提示，降低出错率                                      │",
        f"│                                                                 │",
        f"└{border}┘",
        "",
        "请选择 1 或 2：",
    ]
    
    return "\n".join(lines)


# ============ 启动 Banner ============

def render_banner(version: str = "2.0.0") -> str:
    """
    渲染 VAF 启动 Banner
    
    Args:
        version: VAF 版本号
    """
    lines = [
        "┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓",
        "┃                                                  ╭━━━━╮ ┃",
        "┃        [∙]        ██╗   ██╗  █████╗  ███████╗    ┃ MI ┃ ┃",
        "┃       ╭───╮       ██║   ██║ ██╔══██╗ ██╔════╝    ╰━━━━╯ ┃",
        "┃       │⊙ ⊙│       ██║   ██║ ███████║ █████╗              ┃",
        "┃       │ ═ │       ╚██╗ ██╔╝ ██╔══██║ ██╔══╝              ┃",
        "┃       ╰┬─┬╯        ╚████╔╝  ██║  ██║ ██║                 ┃",
        "┃       ═╪═╪═         ╚═══╝   ╚═╝  ╚═╝ ╚═╝                 ┃",
        "┃                                                          ┃",
        f"┃              Vibe Agentic Flow  {version:<21}┃",
        "┃                 🎉 集团信息技术部出品 🎉                   ┃",
        "┃                                                          ┃",
        "┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛",
    ]
    return "\n".join(lines)


# ============ 需求列表展示 ============

def render_feature_list(
    mode: str,
    features: list,
    include_create: bool = True
) -> str:
    """
    渲染需求列表选择界面
    
    Args:
        mode: "hub" 或 "service"
        features: 需求列表 [{"name": "flash-sale", "status": "P03"}]
        include_create: 是否包含创建新需求选项
    """
    mode_text = "需求池" if mode == "hub" else "服务目录"
    
    lines = [
        f"🚀 Vibe Flow - {mode_text}模式",
        "",
        "检测到以下需求：",
    ]
    
    for i, feature in enumerate(features, 1):
        name = feature.get("name", feature) if isinstance(feature, dict) else feature
        status = feature.get("status", "") if isinstance(feature, dict) else ""
        status_text = f" ({status})" if status else ""
        lines.append(f"  [{i}] {name}{status_text}")
    
    if include_create:
        lines.append("  [0] 创建新需求")
    
    lines.extend(["", "请选择："])
    
    return "\n".join(lines)


# ============ 菜单自检清单 ============

def render_self_check(
    check_type: str,
    results: dict,
    verbose: bool = False
) -> str:
    """
    渲染菜单渲染前自检清单
    
    Args:
        check_type: "hub" 或 "service"
        results: {
            "git_sync": "success" | "skipped" | "error",
            "status_file": "success" | "not_found" | "error",
            "service_status": "success" | "not_found" | None,  # 仅 hub 模式
            "hub_connection": "success" | "error" | None,  # 仅 service 模式
            "mode_display": "pending" | "single" | "multi",
            "duration_calc": "success" | "no_approved"
        }
        verbose: 是否详细模式
    """
    # 简洁模式：只在有错误时输出
    if not verbose:
        has_error = any(
            v == "error" for k, v in results.items() 
            if v is not None and k not in ["mode_display", "duration_calc", "service_count"]
        )
        if has_error:
            return "⚠️ 自检发现问题，使用 [v] 切换到详细模式查看"
        return ""  # 简洁模式下无错误时不输出
    
    lines = ["📋 菜单渲染自检："]
    
    # Git 同步
    git_status = results.get("git_sync", "success")
    if git_status == "success":
        lines.append("[1] Git 同步:     ✅ 已执行")
    elif git_status == "skipped":
        lines.append("[1] Git 同步:     ⚠️ 非 Git 仓库已跳过")
    else:
        lines.append("[1] Git 同步:     ❌ 执行失败")
    
    # 状态文件
    status_file = results.get("status_file", "success")
    file_name = "hub_status.json" if check_type == "hub" else "phase_status.json"
    if status_file == "success":
        lines.append(f"[2] 状态文件:     ✅ {file_name} 已读取")
    elif status_file == "not_found":
        lines.append(f"[2] 状态文件:     ⚪ 首次初始化（文件不存在）")
    else:
        lines.append(f"[2] 状态文件:     ❌ {file_name} 读取失败")
    
    # Hub 模式特有：服务状态
    if check_type == "hub":
        service_status = results.get("service_status")
        if service_status == "success":
            count = results.get("service_count", 0)
            lines.append(f"[3] 服务状态:     ✅ .service_status/ 已读取 ({count}个文件)")
        elif service_status == "not_found":
            lines.append("[3] 服务状态:     ⚪ 目录不存在")
        elif service_status:
            lines.append("[3] 服务状态:     ❌ 读取失败")
        
        # 模式显示
        mode_display = results.get("mode_display", "pending")
        if mode_display == "pending":
            lines.append('[4] 模式显示:     ✅ P04 未放行显示"未确定"')
        else:
            lines.append(f'[4] 模式显示:     ✅ 显示"{mode_display}"')
        
        # 耗时计算
        duration_calc = results.get("duration_calc", "success")
        if duration_calc == "success":
            lines.append("[5] 耗时计算:     ✅ 已放行阶段均有耗时")
        else:
            lines.append("[5] 耗时计算:     ⚪ 无已放行阶段")
    
    # Service 模式特有：Hub 连接
    if check_type == "service":
        hub_conn = results.get("hub_connection")
        if hub_conn == "success":
            lines.append("[3] Hub 连接:     ✅ manifest_local.json 已读取")
        elif hub_conn:
            lines.append("[3] Hub 连接:     ❌ manifest_local.json 读取失败")
        
        # 耗时计算
        duration_calc = results.get("duration_calc", "success")
        if duration_calc == "success":
            lines.append("[4] 耗时计算:     ✅ 已放行阶段均有耗时")
        else:
            lines.append("[4] 耗时计算:     ⚪ 无已放行阶段")
    
    return "\n".join(lines)


# ============ 框架更新确认 ============

def render_upgrade_confirm(
    local_version: str,
    vaf_version: str
) -> str:
    """
    渲染框架更新确认对话框
    
    Args:
        local_version: 本地 .vibe 版本
        vaf_version: VAF 最新版本
    """
    lines = [
        "🔍 检测到已初始化的工作区",
        "",
        f"当前 .vibe 版本: {local_version}",
        f"VAF 最新版本:    {vaf_version}",
        "",
        "是否更新 .vibe 框架文件？",
        "  [y] 覆盖更新（保留 workspace.json、local_config.json）",
        "  [n] 跳过",
        "",
        "请选择 y 或 n：",
    ]
    return "\n".join(lines)


# ============ S00 需求池选择 ============

def render_hub_select(
    hub_paths: list,
    no_hub_found: bool = False
) -> str:
    """
    渲染 S00 服务目录初始化时的需求池选择界面
    
    Args:
        hub_paths: [{"path": "/path/to/hub", "from_feature": "feature-a"}]
        no_hub_found: 是否未检测到需求池
    """
    BOX_WIDTH = 65
    border = "─" * BOX_WIDTH
    
    lines = [
        "📋 S00 服务目录初始化 [待执行]",
        "",
        border,
    ]
    
    if no_hub_found:
        lines.extend([
            "未检测到已关联的需求池。",
            "",
            "请输入需求池的绝对路径：",
        ])
    else:
        lines.append("检测到以下已关联的需求池：")
        lines.append("")
        
        for i, hub in enumerate(hub_paths, 1):
            path = hub.get("path", hub) if isinstance(hub, dict) else hub
            from_feature = hub.get("from_feature", "") if isinstance(hub, dict) else ""
            from_text = f" (来自: {from_feature})" if from_feature else ""
            lines.append(f"  [{i}] {path}{from_text}")
        
        lines.extend([
            "  [0] 手动输入新的需求池路径",
            "",
            "请选择需求池 [0-N]：",
        ])
    
    return "\n".join(lines)


# ============ 初始化完成提示 ============

def render_init_complete(
    init_type: str,
    feature_name: str,
    created_items: list
) -> str:
    """
    渲染初始化完成提示
    
    Args:
        init_type: "hub" 或 "service"
        feature_name: 需求名称
        created_items: 已创建的项目列表
    """
    type_text = "需求池" if init_type == "hub" else "服务目录"
    
    lines = [
        f"✅ {type_text}初始化完成！",
        "",
        "已创建：",
    ]
    
    for item in created_items:
        lines.append(f"  ✅ {item}")
    
    return "\n".join(lines)


# ============ 阶段开始提示 ============

def render_phase_start(
    phase: str,
    phase_name: str,
    start_time: str,
    prompt_file: str
) -> str:
    """
    渲染阶段开始提示
    
    Args:
        phase: 阶段编号，如 "P01"
        phase_name: 阶段名称，如 "需求采集"
        start_time: 开始时间
        prompt_file: 提示词文件路径
    """
    BOX_WIDTH = 65
    border = "─" * BOX_WIDTH
    
    lines = [
        f"🚀 开始执行 {phase} {phase_name}",
        f"⏱️ 开始时间: {start_time}",
        "",
        f"正在加载提示词: {prompt_file}",
        border,
    ]
    return "\n".join(lines)


# ============ P01 上下文预加载提示 ============

def render_context_preload(
    feature_name: str,
    source: str
) -> str:
    """
    渲染 P01 上下文预加载提示
    
    Args:
        feature_name: 功能名称
        source: 来源（如 "hub_status.json"）
    """
    BOX_WIDTH = 65
    border = "─" * BOX_WIDTH
    
    lines = [
        "🚀 开始执行 P01 需求采集",
        "",
        "📌 检测到功能上下文：",
        f"   ├─ 功能名称: {feature_name}",
        f"   └─ 来源: {source}",
        "",
        "💡 已自动填充功能目录，将跳过「交互 1：目录扫描」",
        border,
    ]
    return "\n".join(lines)


# ============ P04 服务路径配置汇总 ============

def render_path_summary(
    services: list
) -> str:
    """
    渲染 P04 服务路径配置汇总
    
    Args:
        services: [{"name": "xmstore-api", "path": "/path", "configured": True}]
    """
    lines = [
        "📋 服务路径配置汇总：",
        "",
    ]
    
    for svc in services:
        name = svc.get("name", "")
        path = svc.get("path", "")
        configured = svc.get("configured", False)
        
        if configured and path:
            lines.append(f"   ✅ {name}:  {path}")
        else:
            lines.append(f"   ⚪ {name}:  未配置 (仅远程跟踪)")
    
    lines.extend([
        "",
        "确认配置？",
        "  [y] 确认并继续",
        "  [r] 重新配置",
    ])
    
    return "\n".join(lines)


# ============ 高级模式命令提示 ============

def render_advanced_prompt(
    feature_name: str,
    version: str = "v2.0.0"
) -> str:
    """
    渲染高级模式命令提示
    
    Args:
        feature_name: 当前需求名称
        version: VAF 版本号
    """
    lines = [
        f"🎮 高级模式  |  需求: {feature_name}  |  VAF {version}",
        "",
        "可用命令：",
        "  P01-P06, S01-S07, T01  执行阶段",
        "  status                  查看当前状态",
        "  menu                    临时显示完整菜单",
        "  mode                    切换交互模式",
        "  q                       退出",
        "",
        "> ",
    ]
    return "\n".join(lines)


# ============ Git 提交执行进度 ============

def render_git_progress(
    steps: list,
    is_multi_repo: bool = False,
    service_name: str = None,
    hub_path: str = None
) -> str:
    """
    渲染 Git 提交执行进度
    
    Args:
        steps: [{"action": "git add .", "status": "success", "result": ""}]
        is_multi_repo: 是否多仓库模式
        service_name: 服务名称（多仓库时）
        hub_path: Hub 路径（多仓库时）
    """
    BOX_WIDTH = 65
    border = "─" * BOX_WIDTH
    
    lines = ["🚀 提交中..."]
    
    if is_multi_repo and service_name:
        lines.extend([
            "",
            f"📁 {service_name} (当前服务):",
        ])
    
    for step in steps:
        action = step.get("action", "")
        status = step.get("status", "pending")
        result = step.get("result", "")
        
        if status == "success":
            status_icon = "✅"
        elif status == "error":
            status_icon = "❌"
        else:
            status_icon = "⏳"
        
        result_text = f" [{result}]" if result else ""
        lines.append(f"   {status_icon} {action}{result_text}")
    
    if is_multi_repo and hub_path:
        lines.extend([
            "",
            f"📁 Hub (状态同步，使用 -C 参数):",
        ])
        # Hub 的步骤通常在 steps 的后半部分，这里简化处理
    
    lines.extend([
        "",
        border,
        "✅ 已推送，协作者可 git pull 同步",
        border,
    ])
    
    return "\n".join(lines)


# ============ 推送失败重试选项 ============

def render_git_push_error(
    error_message: str,
    branch: str
) -> str:
    """
    渲染推送失败重试选项
    
    Args:
        error_message: 错误信息
        branch: 分支名
    """
    lines = [
        "❌ 推送失败",
        "",
        "错误信息：",
        f"  {error_message}",
        "",
        "可能原因：远程有新的提交，需要先拉取",
        "",
        "  [r] 🔄 执行 git pull 后重试",
        "  [m] 🔀 手动解决（跳过推送）",
        "",
        "请选择：",
    ]
    return "\n".join(lines)


# ============ 暂不放行提示 ============

def render_defer_approval(
    phase: str,
    phase_name: str
) -> str:
    """
    渲染暂不放行提示
    
    Args:
        phase: 阶段编号
        phase_name: 阶段名称
    """
    lines = [
        f"⏸️ {phase} {phase_name} 已标记为「待确认」，下一阶段暂不解锁",
        "",
        "💡 您可以：",
        "   - 手动修改产出文件后，重新选择该阶段进行放行确认",
        "   - 选择该阶段重新执行",
    ]
    return "\n".join(lines)


# ============ 刷新菜单状态输出 ============

def render_refresh_status(
    status_file: str,
    phases: dict
) -> str:
    """
    渲染刷新菜单状态输出
    
    Args:
        status_file: 状态文件名
        phases: {"P00": "approved", "P01": "approved", ...}
    """
    lines = [
        "🔄 正在刷新菜单状态...",
        "",
        f"读取 {status_file}：",
    ]
    
    status_icons = {
        "approved": "✅",
        "pending_review": "⏸️",
        "in_progress": "🔄",
        "not_started": "⏳",
    }
    
    for phase, status in phases.items():
        icon = status_icons.get(status, "⏳")
        lines.append(f"  {icon} {phase}: {status}")
    
    return "\n".join(lines)


# ============ 退出会话提示 ============

def render_exit_summary(
    feature_name: str,
    progress: list
) -> str:
    """
    渲染退出会话提示
    
    Args:
        feature_name: 需求名称
        progress: 已完成的阶段列表，如 ["P01", "P02", "P03"]
    """
    BOX_WIDTH = 65
    border = "═" * BOX_WIDTH
    
    progress_text = " → ".join([f"{p} ✓" for p in progress]) if progress else "无"
    
    lines = [
        "👋 Vibe Flow 会话结束",
        "",
        border,
        f"  需求: {feature_name}",
        "",
        f"  本次进度：{progress_text}",
        "",
        "  下次继续：",
        "    • 需求池: @vaf_starter",
        "    • 服务目录: 在各服务目录执行 @vaf_starter",
        "",
        border,
    ]
    return "\n".join(lines)


# ============ 错误提示 ============

def render_error(
    error_type: str,
    details: dict
) -> str:
    """
    渲染错误提示
    
    Args:
        error_type: "invalid_path" | "dir_not_found" | "dependency_not_met"
        details: 错误详情
    """
    if error_type == "invalid_path":
        user_input = details.get("input", "")
        lines = [
            f"⚠️ 路径格式无效：{user_input}",
            "",
            "请输入绝对路径，例如：",
            "- Mac/Linux: /Users/xxx/Projects/xxx",
            "- Windows: C:\\Projects\\xxx",
        ]
    elif error_type == "dir_not_found":
        path = details.get("path", "")
        lines = [
            f"❌ 目录不存在：{path}",
            "",
            "请检查路径是否正确，或先创建该目录。",
        ]
    elif error_type == "dependency_not_met":
        phase = details.get("phase", "")
        phase_name = details.get("phase_name", "")
        dependencies = details.get("dependencies", [])
        lines = [
            f"⚠️ 无法执行 {phase} {phase_name}",
            "",
            "前置条件未满足：",
        ]
        for dep in dependencies:
            lines.append(f"  ❌ {dep}")
        lines.extend(["", "请先完成上述步骤。"])
    else:
        lines = [f"❌ 未知错误：{error_type}"]
    
    return "\n".join(lines)


# ============ P06 解锁检查展示 ============

def render_unlock_status(
    hub_status: dict,
    service_statuses: dict,
    manifest: dict = None
) -> str:
    """
    渲染 P06 解锁检查状态展示
    
    Args:
        hub_status: Hub 状态
        service_statuses: 服务状态字典 {service_name: phase_status}
        manifest: 执行清单
    """
    phases = hub_status.get("phases", {})
    # 从 manifest 读取 mode（P04 产出），默认为 single
    manifest = manifest or {}
    mode = manifest.get("mode", "single")
    
    # 获取服务列表
    services = list(service_statuses.keys()) if service_statuses else []
    service_count = len(services)
    
    lines = [
        "🔍 P06 解锁检查...",
        f"   ├─ 模式: {'多服务' if mode == 'multi' else '单服务'}",
    ]
    
    # 单服务模式：无需 P06 协调
    if mode != "multi":
        lines.append("   └─ ✅ 单服务模式，无需 P06 协调阶段")
        return "\n".join(lines)
    
    if service_count > 0:
        lines.append(f"   ├─ 服务数量: {service_count} ({', '.join(services)})")
    
    # 检查 P06-A
    p05_approved = phases.get("P05", {}).get("status") == "approved"
    all_s01_approved = all(
        ss.get("phases", {}).get("S01", {}).get("status") == "approved"
        for ss in service_statuses.values()
    ) if service_statuses else True
    
    p06a_unlocked = p05_approved and all_s01_approved
    p06a_icon = "✅ 已解锁" if p06a_unlocked else "🔒 锁定"
    p06a_detail = []
    if not p05_approved:
        p06a_detail.append("等待 P05")
    if not all_s01_approved and service_statuses:
        pending = [s for s, ss in service_statuses.items() if ss.get("phases", {}).get("S01", {}).get("status") != "approved"]
        p06a_detail.append(f"等待 {', '.join(pending)} S01")
    p06a_reason = f" ({', '.join(p06a_detail)})" if p06a_detail else " (P05 ✓, 所有 S01 ✓)"
    
    lines.append(f"   ├─ P06-A: {p06a_icon}{p06a_reason}")
    
    # 检查 P06-B
    p06a_approved = phases.get("P06-A", {}).get("status") == "approved"
    all_s05_approved = all(
        ss.get("phases", {}).get("S05", {}).get("status") == "approved"
        for ss in service_statuses.values()
    ) if service_statuses else True
    
    p06b_unlocked = p06a_approved and all_s05_approved
    p06b_icon = "✅ 已解锁" if p06b_unlocked else "🔒 锁定"
    p06b_detail = []
    if not p06a_approved:
        p06b_detail.append("等待 P06-A")
    if not all_s05_approved and service_statuses:
        pending = [s for s, ss in service_statuses.items() if ss.get("phases", {}).get("S05", {}).get("status") != "approved"]
        p06b_detail.append(f"等待 {', '.join(pending)} S05")
    p06b_reason = f" ({', '.join(p06b_detail)})" if p06b_detail else ""
    
    lines.append(f"   ├─ P06-B: {p06b_icon}{p06b_reason}")
    
    # 检查 P06-C
    all_s07_approved = all(
        ss.get("phases", {}).get("S07", {}).get("status") == "approved"
        for ss in service_statuses.values()
    ) if service_statuses else True
    
    p06c_unlocked = all_s07_approved
    p06c_icon = "✅ 已解锁" if p06c_unlocked else "🔒 锁定"
    p06c_detail = []
    if not all_s07_approved and service_statuses:
        pending = [s for s, ss in service_statuses.items() if ss.get("phases", {}).get("S07", {}).get("status") != "approved"]
        p06c_detail.append(f"等待 {', '.join(pending)} S07")
    p06c_reason = f" ({', '.join(p06c_detail)})" if p06c_detail else ""
    
    lines.append(f"   └─ P06-C: {p06c_icon}{p06c_reason}")
    
    return "\n".join(lines)


# ============ Git 同步状态展示 ============

def render_git_sync(
    sync_results: list[dict],
    verbose: bool = False
) -> str:
    """
    渲染 Git 同步状态展示
    
    Args:
        sync_results: 同步结果列表 [{"name": "Hub", "status": "success", "message": "git pull ✓"}, ...]
        verbose: 是否详细模式
    """
    # 简洁模式：一行摘要
    if not verbose:
        success_count = sum(1 for r in sync_results if r.get("status") == "success")
        total_count = len(sync_results)
        has_error = any(r.get("status") == "failed" for r in sync_results)
        
        if has_error:
            return f"🔄 同步: {success_count}/{total_count} ⚠️ 部分失败"
        elif total_count > 0:
            return f"🔄 同步完成 ✓"
        else:
            return ""
    
    lines = ["🔄 同步中..."]
    
    for i, result in enumerate(sync_results):
        name = result.get("name", "unknown")
        status = result.get("status", "pending")
        message = result.get("message", "")
        
        # 确定前缀符号
        prefix = "└─" if i == len(sync_results) - 1 else "├─"
        
        # 确定状态图标
        if status == "success":
            icon = "✓"
        elif status == "failed":
            icon = "✗"
        elif status == "skipped":
            icon = "⚪"
        else:
            icon = "..."
        
        # 格式化行
        line = f"   {prefix} {name:<16} {message} {icon}"
        lines.append(line)
    
    return "\n".join(lines)


# ============ 服务选择表格 ============

def render_service_select(
    services: list[dict]
) -> str:
    """
    渲染服务选择表格
    
    Args:
        services: 服务列表 [{"name": "xmstore-api", "scope": "库存模块优化"}, ...]
    """
    if not services:
        return "⚠️ 无服务可选择"
    
    # 计算列宽
    max_name_len = max(len(s.get("name", "")) for s in services)
    max_name_len = max(max_name_len, 10)  # 最小宽度
    
    max_scope_len = max(len(s.get("scope", "")) for s in services)
    max_scope_len = max(max_scope_len, 15)  # 最小宽度
    
    # 表头
    lines = [
        "请选择要执行的服务：",
        "",
        f"┌──────┬{'─' * (max_name_len + 2)}┬{'─' * (max_scope_len + 2)}┐",
        f"│ 序号 │ {'服务名称':<{max_name_len}} │ {'改造范围':<{max_scope_len}} │",
        f"├──────┼{'─' * (max_name_len + 2)}┼{'─' * (max_scope_len + 2)}┤",
    ]
    
    # 数据行
    for i, service in enumerate(services, 1):
        name = service.get("name", "")
        scope = service.get("scope", "")
        # 处理中文字符宽度
        name_display = name + " " * (max_name_len - sum(2 if ord(c) > 127 else 1 for c in name))
        scope_display = scope + " " * (max_scope_len - sum(2 if ord(c) > 127 else 1 for c in scope))
        lines.append(f"│  {i:<3} │ {name_display} │ {scope_display} │")
    
    lines.extend([
        f"└──────┴{'─' * (max_name_len + 2)}┴{'─' * (max_scope_len + 2)}┘",
        "",
        "请输入序号（多个用逗号分隔，如 1,2）或 [a] 全选：",
    ])
    
    return "\n".join(lines)


# ============ Git 推送重试展示 ============

def render_git_retry(
    steps: list[dict]
) -> str:
    """
    渲染 Git 推送重试展示
    
    Args:
        steps: 步骤列表 [{"action": "git pull", "status": "success"}, {"action": "git push", "status": "retry", "attempt": 1}, ...]
    """
    lines = ["🔄 同步状态到 Hub..."]
    
    for i, step in enumerate(steps):
        action = step.get("action", "")
        status = step.get("status", "pending")
        attempt = step.get("attempt", 0)
        message = step.get("message", "")
        
        # 确定前缀
        is_last = i == len(steps) - 1
        is_sub = step.get("is_sub", False)
        
        if is_sub:
            prefix = "      └─" if is_last else "      ├─"
        else:
            prefix = "   └─" if is_last else "   ├─"
        
        # 确定状态图标
        if status == "success":
            icon = "✓"
        elif status == "failed":
            icon = "✗"
        elif status == "retry":
            icon = f"尝试 {attempt}/3"
        else:
            icon = "..."
        
        # 格式化行
        if message:
            line = f"{prefix} {action} {icon} ({message})"
        else:
            line = f"{prefix} {action} {icon}"
        lines.append(line)
    
    return "\n".join(lines)


# ============ 主函数 ============

def main():
    parser = argparse.ArgumentParser(
        description="Vibe Flow 菜单渲染工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 进度菜单（自动检测工作区类型）
  python vibe_render_menu.py --mode progress --feature flash-sale
  
  # Service 模式需要指定服务名
  python vibe_render_menu.py --mode progress --feature flash-sale --service xmstore-api
  
  # 需求列表
  python vibe_render_menu.py --mode list --features "flash-sale,user-upgrade"
  
  # 阶段执行确认
  python vibe_render_menu.py --mode confirm --phase P01
  
  # 放行确认框
  python vibe_render_menu.py --mode approval --phase P02 --outputs "file1.md,file2.md" --duration "25分钟"
  
  # 已放行阶段详情（含重新执行入口）
  python vibe_render_menu.py --mode phase-detail --phase P02 --duration "23分钟" --approved-at "2025-12-20 12:39" --outputs "knowledge_base.md,context_analysis_report.md" --output-path "docs/P02_context_analysis/"
  
  # Git 提交确认（默认显示提交选项）
  python vibe_render_menu.py --mode git-confirm --phase P02 --feature flash-sale --changed-files "a.md,b.json" --commit-msg "feat: xxx"
  
  # Git 提交确认（非 Git 仓库，跳过提交选项）
  python vibe_render_menu.py --mode git-confirm --phase P02 --feature flash-sale --no-git-repo
  
  # 全局进度视图
  python vibe_render_menu.py --mode global --feature flash-sale
  
  # P03 模式选择
  python vibe_render_menu.py --mode p03-select
  
  # 工作区类型选择
  python vibe_render_menu.py --mode init-select
  
  # P06 解锁检查
  python vibe_render_menu.py --mode unlock-status --feature flash-sale
  
  # Git 同步状态
  python vibe_render_menu.py --mode git-sync --sync-results '[{"name":"Hub","status":"success","message":"git pull"}]'
  
  # 服务选择
  python vibe_render_menu.py --mode service-select --services '[{"name":"xmstore-api","scope":"库存模块"}]'
  
  # Git 推送重试
  python vibe_render_menu.py --mode git-retry --retry-steps '[{"action":"git pull","status":"success"}]'
"""
    )
    
    parser.add_argument(
        "--mode", "-m",
        choices=[
            # 核心菜单
            "progress", "list", "global",
            # 阶段相关
            "confirm", "approval", "phase-detail", "p03-select", "phase-start", "context-preload",
            # 初始化
            "init-select", "interaction-select", "hub-select", "init-complete", "upgrade-confirm",
            # Git 操作
            "git-check", "git-confirm", "git-sync", "git-retry", "git-progress", "git-push-error",
            # 状态展示
            "unlock-status", "self-check", "refresh-status", "path-summary",
            # 其他
            "service-select", "banner", "feature-list", "advanced-prompt", "defer-approval",
            "exit-summary", "error"
        ],
        required=True,
        help="渲染模式"
    )
    
    parser.add_argument(
        "--phase", "-p",
        help="阶段 ID (confirm 模式必填，如 P01, S01)"
    )
    
    parser.add_argument(
        "--phase-status",
        default="not_started",
        help="阶段状态 (confirm 模式用，默认: not_started)"
    )
    
    parser.add_argument(
        "--outputs",
        help="产出文件列表，逗号分隔 (approval 模式用)"
    )
    
    parser.add_argument(
        "--duration",
        default="--",
        help="耗时字符串 (approval/phase-detail 模式用)"
    )
    
    parser.add_argument(
        "--approved-at",
        help="放行时间 (phase-detail 模式用)"
    )
    
    parser.add_argument(
        "--output-path",
        help="产出物路径 (phase-detail 模式用)"
    )
    
    parser.add_argument(
        "--token-usage",
        type=int,
        help="Token 消耗 (phase-detail 模式用，已废弃)"
    )
    
    parser.add_argument(
        "--conversation-turns",
        type=int,
        help="对话轮次 (phase-detail 模式用)"
    )
    
    parser.add_argument(
        "--changed-files",
        help="变更文件列表，逗号分隔 (git-confirm 模式用)"
    )
    
    parser.add_argument(
        "--commit-msg",
        help="提交信息 (git-confirm 模式用)"
    )
    
    parser.add_argument(
        "--hub-changed-files",
        help="Hub 变更文件列表，逗号分隔 (git-confirm 服务模式用)"
    )
    
    parser.add_argument(
        "--is-git-repo",
        action="store_true",
        default=True,
        help="当前目录是否为 Git 仓库 (git-confirm 模式用，默认 True)"
    )
    
    parser.add_argument(
        "--no-git-repo",
        action="store_true",
        help="当前目录不是 Git 仓库 (git-confirm 模式用)"
    )
    
    parser.add_argument(
        "--hub-is-git-repo",
        action="store_true",
        default=True,
        help="Hub 目录是否为 Git 仓库 (git-confirm 服务模式用，默认 True)"
    )
    
    parser.add_argument(
        "--hub-no-git-repo",
        action="store_true",
        help="Hub 目录不是 Git 仓库 (git-confirm 服务模式用)"
    )
    
    parser.add_argument(
        "--path",
        help="目标路径 (git-check 模式用，默认当前目录)"
    )
    
    parser.add_argument(
        "--feature", "-f",
        help="需求名称 (hub/service 模式必填，用于自动推导状态文件路径)"
    )
    
    parser.add_argument(
        "--service",
        help="服务名称 (service 模式必填)"
    )
    
    parser.add_argument(
        "--features",
        help="需求列表，逗号分隔 (list 模式使用)"
    )
    
    
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="详细模式：显示自检清单、Git 同步详情等过程信息（默认简洁模式）"
    )
    
    parser.add_argument(
        "--json",
        action="store_true",
        help="JSON 输出模式：输出紧凑 JSON 而非 ASCII 菜单（AI 调用时使用）"
    )
    
    parser.add_argument(
        "--sync-results",
        help="Git 同步结果 JSON 字符串 (git-sync 模式用)"
    )
    
    parser.add_argument(
        "--services-json",
        help="服务列表 JSON 字符串 (service-select 模式用)"
    )
    
    parser.add_argument(
        "--retry-steps",
        help="重试步骤 JSON 字符串 (git-retry 模式用)"
    )
    
    # ============ 新增参数 ============
    
    parser.add_argument(
        "--check-results",
        help="""自检结果 JSON 字符串 (self-check 模式用)
格式: {
  "git_sync": "success" | "skipped" | "error",
  "status_file": "success" | "not_found" | "error",
  "service_status": "success" | "not_found" | null,  # hub 模式
  "hub_connection": "success" | "error" | null,      # service 模式
  "mode_display": "pending" | "single" | "multi",
  "duration_calc": "success" | "no_approved"
}
示例: --check-results '{"git_sync":"skipped","status_file":"not_found",...}'
"""
    )
    
    parser.add_argument(
        "--local-version",
        help="本地 .vibe 版本 (upgrade-confirm 模式用)"
    )
    
    parser.add_argument(
        "--hub-paths",
        help="需求池路径列表 JSON 字符串 (hub-select 模式用)"
    )
    
    parser.add_argument(
        "--created-items",
        help="已创建项目列表 JSON 字符串 (init-complete 模式用)"
    )
    
    parser.add_argument(
        "--phase-name",
        help="阶段名称 (phase-start, defer-approval 模式用)"
    )
    
    parser.add_argument(
        "--start-time",
        help="开始时间 (phase-start 模式用)"
    )
    
    parser.add_argument(
        "--prompt-file",
        help="提示词文件路径 (phase-start 模式用)"
    )
    
    parser.add_argument(
        "--context-source",
        help="上下文来源 (context-preload 模式用)"
    )
    
    parser.add_argument(
        "--git-steps",
        help="Git 步骤 JSON 字符串 (git-progress 模式用)"
    )
    
    parser.add_argument(
        "--hub-path-display",
        help="Hub 路径显示 (git-progress 模式用)"
    )
    
    parser.add_argument(
        "--error-message",
        help="错误信息 (git-push-error 模式用)"
    )
    
    parser.add_argument(
        "--branch",
        help="分支名 (git-push-error 模式用)"
    )
    
    parser.add_argument(
        "--phases-json",
        help="阶段状态 JSON 字符串 (refresh-status 模式用)"
    )
    
    parser.add_argument(
        "--progress",
        help="已完成阶段列表 JSON 字符串 (exit-summary 模式用)"
    )
    
    parser.add_argument(
        "--error-type",
        help="错误类型: invalid_path | dir_not_found | dependency_not_met (error 模式用)"
    )
    
    parser.add_argument(
        "--error-details",
        help="错误详情 JSON 字符串 (error 模式用)"
    )
    
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="交互模式：输出菜单后等待用户输入并返回选择结果 (approval 模式用)"
    )

    args = parser.parse_args()
    
    try:
        if args.mode == "progress":
            # 进度菜单模式
            if not args.feature:
                print("错误: progress 模式需要 --feature 参数", file=sys.stderr)
                sys.exit(1)
            
            # 自动执行 git pull（Hub 拉取当前仓库，Service 只拉取 Hub）
            git_result = auto_git_pull(args.feature, args.verbose)
            git_sync_msg = None
            if git_result["messages"]:
                git_sync_msg = git_result["messages"][0]
                if args.verbose:
                    print(git_sync_msg, file=sys.stderr)
            
            paths = resolve_paths(args.feature)
            feature_name = args.feature
            ws_mode = paths["mode"]  # "hub" 或 "service"
            
            if ws_mode == "hub":
                # Hub 工作区
                hub_status = {}
                status_path = Path(paths["status_path"])
                if status_path.exists():
                    hub_status = load_json_file(paths["status_path"])
                
                # 加载服务状态
                service_statuses = []
                if paths.get("services_dir"):
                    services_path = Path(paths["services_dir"])
                    if services_path.exists():
                        for json_file in services_path.glob("*.json"):
                            service_statuses.append(load_json_file(str(json_file)))
                
                # 加载 manifest
                manifest = {}
                manifest_path = Path(paths["manifest_path"])
                if manifest_path.exists():
                    manifest = load_json_file(paths["manifest_path"])
                
                menu = render_hub_menu(
                    hub_status=hub_status,
                    service_statuses=service_statuses,
                    manifest=manifest,
                    feature_name=feature_name,
                    vaf_version=get_vaf_version(),
                    verbose=args.verbose
                )
                
                if args.json:
                    output = {
                        "menu": menu,
                        "mode": "hub",
                        "feature": feature_name,
                        "status": hub_status,
                        "services": service_statuses,
                        "next_action": get_next_action("progress")
                    }
                    print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
                else:
                    print(menu)
            
            else:
                # Service 工作区（简化版，P06 门禁状态由 check_s_unlock 内部获取）
                phase_status = {"phases": {}}
                status_path = Path(paths["status_path"])
                if status_path.exists():
                    phase_status = load_json_file(paths["status_path"])
                
                # 获取项目根目录
                root = find_project_root()
                
                # 从 manifest_local.json 读取服务名（正确路径：不含 P04_service_split）
                manifest_local_path = root / "docs" / "features" / feature_name / "manifest_local.json"
                manifest_local = load_json_file(str(manifest_local_path)) if manifest_local_path.exists() else {}
                service_name = args.service or manifest_local.get("service") or phase_status.get("service", "unknown")
                
                # 从 workspace.json 读取 VAF 版本
                workspace_path = root / ".vibe" / "workspace.json"
                workspace = load_json_file(str(workspace_path)) if workspace_path.exists() else {}
                vaf_version = workspace.get("vaf_version", get_vaf_version())
                # 格式化版本号：如果不以 v 开头，添加 v 前缀
                if vaf_version and not vaf_version.startswith("v"):
                    vaf_version = f"v{vaf_version}"
                
                menu = render_service_menu(
                    phase_status=phase_status,
                    service_name=service_name,
                    feature_name=feature_name,
                    vaf_version=vaf_version,
                    verbose=args.verbose
                )
                
                if args.json:
                    output = {
                        "menu": menu,
                        "mode": "service",
                        "service": service_name,
                        "feature": feature_name,
                        "status": phase_status,
                        "next_action": get_next_action("progress")
                    }
                    print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
                else:
                    print(menu)
        
        elif args.mode == "list":
            # 需求列表模式
            if args.features:
                features = [f.strip() for f in args.features.split(",") if f.strip()]
            else:
                features = []
            
            list_mode = "hub" if not args.service else "service"
            menu = render_feature_list(features, list_mode)
            if args.json:
                output = {
                    "panel": menu,
                    "mode": "list",
                    "features": features,
                    "next_action": get_next_action("list")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(menu)
        
        elif args.mode == "confirm":
            # 阶段执行确认模式
            if not args.phase:
                print("错误: confirm 模式需要 --phase 参数", file=sys.stderr)
                sys.exit(1)
            
            panel = render_phase_confirm(
                phase=args.phase,
                status=args.phase_status
            )
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "confirm",
                    "phase": args.phase,
                    "next_action": get_next_action("confirm")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "approval":
            # 放行确认框模式
            if not args.phase:
                print("错误: approval 模式需要 --phase 参数", file=sys.stderr)
                sys.exit(1)
            
            outputs = []
            if args.outputs:
                outputs = [o.strip() for o in args.outputs.split(",") if o.strip()]
            
            panel = render_approval_confirm(
                phase=args.phase,
                outputs=outputs,
                duration=args.duration
            )

            if args.json:
                output = {
                    "panel": panel,
                    "mode": "approval",
                    "phase": args.phase,
                    "outputs": outputs,
                    "next_action": get_next_action("approval")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)

            # 交互模式：等待用户输入并返回选择结果
            if getattr(args, 'interactive', False):
                try:
                    choice = input().strip().lower()
                    # 输出用户选择到 stdout（AI 可以捕获）
                    print(f"USER_CHOICE:{choice}")
                except (EOFError, KeyboardInterrupt):
                    print("USER_CHOICE:n")
        elif args.mode == "phase-detail":
            # 已放行阶段详情模式
            if not args.phase:
                print("错误: phase-detail 模式需要 --phase 参数", file=sys.stderr)
                sys.exit(1)
            
            outputs = []
            if args.outputs:
                outputs = [o.strip() for o in args.outputs.split(",") if o.strip()]
            
            panel = render_phase_detail(
                phase=args.phase,
                status=args.phase_status or "approved",
                duration=args.duration or "--",
                approved_at=args.approved_at or "",
                outputs=outputs,
                output_path=args.output_path or "",
                token_usage=args.token_usage,
                conversation_turns=args.conversation_turns
            )
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "phase-detail",
                    "phase": args.phase,
                    "next_action": get_next_action("phase-detail")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "git-check":
            # Git 仓库检测模式
            result = check_git_repo(args.path)
            result["next_action"] = get_next_action("git-check")
            print(json.dumps(result, ensure_ascii=False))
        
        elif args.mode == "git-confirm":
            # Git 提交确认框模式
            if not args.phase or not args.feature:
                # 容错处理：缺少参数时输出跳过提示（常见于用户选择 [s] 跳过后的误调用）
                skip_msg = """
⏭️ 已跳过 Git 提交

💡 您可以稍后手动执行：
   git pull
   git add .
   git commit -m "feat: 阶段已放行"
   git push
"""
                print(skip_msg)
                sys.exit(0)
            
            changed_files = []
            if args.changed_files:
                changed_files = [f.strip() for f in args.changed_files.split(",") if f.strip()]
            
            hub_changed_files = []
            if args.hub_changed_files:
                hub_changed_files = [f.strip() for f in args.hub_changed_files.split(",") if f.strip()]
            
            commit_msg = args.commit_msg or f"feat({args.feature}): {args.phase} 已放行"
            
            # 处理 Git 仓库标记（--no-git-repo 优先级高于 --is-git-repo）
            is_git = not args.no_git_repo
            hub_is_git = not args.hub_no_git_repo
            
            # 通过 workspace type 判断是否为服务模式
            is_service_mode = get_workspace_type() == "service"
            
            panel = render_git_confirm(
                phase=args.phase,
                feature=args.feature,
                changed_files=changed_files,
                commit_message=commit_msg,
                is_service_mode=is_service_mode,
                service_name=args.service or "",
                hub_changed_files=hub_changed_files,
                is_git_repo=is_git,
                hub_is_git_repo=hub_is_git
            )
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "git-confirm",
                    "phase": args.phase,
                    "feature": args.feature,
                    "next_action": get_next_action("git-confirm")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "global":
            # 全局进度视图模式（通过 --feature 内部获取路径）
            if not args.feature:
                print("错误: global 模式需要 --feature 参数", file=sys.stderr)
                sys.exit(1)
            
            paths = resolve_paths(args.feature)
            feature_name = args.feature
            workspace_mode = paths.get("mode", "hub")
            
            # 加载 hub_status
            hub_status = {}
            if workspace_mode == "service" and paths.get("hub_status_path"):
                # Service 模式：从 Hub 路径读取 hub_status.json
                hub_status_path = Path(paths["hub_status_path"])
                if hub_status_path.exists():
                    hub_status = load_json_file(str(hub_status_path))
            else:
                # Hub 模式：直接使用 status_path
                status_path = Path(paths["status_path"])
                if status_path.exists():
                    hub_status = load_json_file(paths["status_path"])
            
            # 加载服务状态
            service_statuses = {}
            if workspace_mode == "service":
                # Service 模式：混合加载
                # 1. 本服务用本地 phase_status.json
                # 2. 其他服务从 Hub 的 .service_status/ 读取
                
                # 获取本服务名称（从 manifest_local.json）
                local_service_name = None
                root = find_project_root()
                manifest_local_path = root / "docs" / "features" / feature_name / "manifest_local.json"
                if manifest_local_path.exists():
                    manifest_local = load_json_file(str(manifest_local_path))
                    local_service_name = manifest_local.get("service")
                    
                    # 从 Hub 获取 .service_status 目录路径
                    hub_path = manifest_local.get("hub_path")
                    if hub_path:
                        hub_services_dir = Path(hub_path) / "features" / feature_name / "docs" / ".service_status"
                        if hub_services_dir.exists():
                            for json_file in hub_services_dir.glob("*.json"):
                                svc_name = json_file.stem
                                if svc_name != local_service_name:
                                    # 其他服务：从 Hub 读取
                                    service_statuses[svc_name] = load_json_file(str(json_file))
                
                # 本服务：用本地 phase_status.json
                if local_service_name:
                    local_status_path = Path(paths["status_path"])
                    if local_status_path.exists():
                        service_statuses[local_service_name] = load_json_file(str(local_status_path))
            else:
                # Hub 模式：直接从 .service_status/ 读取
                if paths.get("services_dir"):
                    services_path = Path(paths["services_dir"])
                    if services_path.exists():
                        for json_file in services_path.glob("*.json"):
                            service_name = json_file.stem
                            service_statuses[service_name] = load_json_file(str(json_file))
            
            # 加载 execution_manifest 获取 mode
            exec_mode = "single"
            manifest_path = paths.get("manifest_path")
            if manifest_path and Path(manifest_path).exists():
                manifest = load_json_file(manifest_path)
                exec_mode = manifest.get("mode", "single")
            
            panel = render_global_progress(
                hub_status=hub_status,
                service_statuses=service_statuses,
                feature=feature_name,
                vaf_version=get_vaf_version(),
                mode=exec_mode
            )
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "global",
                    "feature": feature_name,
                    "next_action": get_next_action("global")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "p03-select":
            # P03 模式选择界面
            panel = render_p03_mode_select()
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "p03-select",
                    "next_action": get_next_action("p03-select")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "init-select":
            # 工作区类型选择界面
            panel = render_init_select()
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "init-select",
                    "next_action": get_next_action("init-select")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "interaction-select":
            # 交互模式选择界面（高级/引导）
            panel = render_interaction_select()
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "interaction-select",
                    "next_action": get_next_action("interaction-select")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "unlock-status":
            # P06 解锁检查展示（通过 --feature 内部获取路径）
            if not args.feature:
                print("错误: unlock-status 模式需要 --feature 参数", file=sys.stderr)
                sys.exit(1)
            
            paths = resolve_paths(args.feature)
            
            # 加载 hub_status
            hub_status = {}
            status_path = Path(paths["status_path"])
            if status_path.exists():
                hub_status = load_json_file(paths["status_path"])
            
            # 加载服务状态
            service_statuses = {}
            if paths.get("services_dir"):
                services_path = Path(paths["services_dir"])
                if services_path.exists():
                    for json_file in services_path.glob("*.json"):
                        service_name = json_file.stem
                        service_statuses[service_name] = load_json_file(str(json_file))
            
            # 加载 manifest
            manifest = {}
            manifest_path = paths.get("manifest_path")
            if manifest_path and Path(manifest_path).exists():
                manifest = load_json_file(manifest_path)
            
            panel = render_unlock_status(
                hub_status=hub_status,
                service_statuses=service_statuses,
                manifest=manifest
            )
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "unlock-status",
                    "next_action": get_next_action("unlock-status")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "git-sync":
            # Git 同步状态展示
            sync_results = []
            if args.sync_results:
                sync_results = json.loads(args.sync_results)
            
            panel = render_git_sync(sync_results, verbose=args.verbose)
            if args.json:
                output = {
                    "panel": panel or "",
                    "mode": "git-sync",
                    "next_action": get_next_action("git-sync")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            elif panel:  # 简洁模式下可能返回空字符串
                print(panel)
        
        elif args.mode == "service-select":
            # 服务选择表格
            services = []
            if args.services_json:
                services = json.loads(args.services_json)
            
            panel = render_service_select(services)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "service-select",
                    "services": services,
                    "next_action": get_next_action("service-select")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "git-retry":
            # Git 推送重试展示
            steps = []
            if args.retry_steps:
                steps = json.loads(args.retry_steps)
            
            panel = render_git_retry(steps)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "git-retry",
                    "next_action": get_next_action("git-retry")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "banner":
            # 启动 Banner（自动从 workspace.json 获取版本号）
            version = get_vaf_version()
            panel = render_banner(version)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "banner",
                    "version": version,
                    "next_action": get_next_action("banner")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "feature-list":
            # 需求列表展示
            features = []
            if args.features:
                features = json.loads(args.features)
            mode_type = "hub"  # 默认 hub 模式
            if args.service:
                mode_type = "service"
            panel = render_feature_list(mode_type, features)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "feature-list",
                    "features": features,
                    "next_action": get_next_action("feature-list")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "self-check":
            # 菜单自检清单
            check_type = "hub"  # 默认 hub
            if args.service:
                check_type = "service"
            results = {}
            if args.check_results:
                results = json.loads(args.check_results)
            panel = render_self_check(check_type, results, verbose=args.verbose)
            if args.json:
                # 计算 check_passed
                check_passed = all(v.get("status") == "ok" for v in results.values()) if results else True
                output = {
                    "panel": panel or "",
                    "mode": "self-check",
                    "check_passed": check_passed,
                    "next_action": get_next_action("self-check")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            elif panel:  # 简洁模式下可能返回空字符串
                print(panel)
        
        elif args.mode == "upgrade-confirm":
            # 框架更新确认
            local_version = args.local_version or "unknown"
            vaf_version = get_vaf_version()
            panel = render_upgrade_confirm(local_version, vaf_version)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "upgrade-confirm",
                    "next_action": get_next_action("upgrade-confirm")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "hub-select":
            # S00 需求池选择
            hub_paths = []
            if args.hub_paths:
                hub_paths = json.loads(args.hub_paths)
            no_hub_found = len(hub_paths) == 0
            panel = render_hub_select(hub_paths, no_hub_found)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "hub-select",
                    "hub_paths": hub_paths,
                    "next_action": get_next_action("hub-select")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "init-complete":
            # 初始化完成提示
            init_type = "hub"
            if args.service:
                init_type = "service"
            feature_name = args.feature or ""
            created_items = []
            if args.created_items:
                created_items = json.loads(args.created_items)
            panel = render_init_complete(init_type, feature_name, created_items)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "init-complete",
                    "feature": feature_name,
                    "next_action": get_next_action("init-complete")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "phase-start":
            # 阶段开始提示
            phase = args.phase or ""
            phase_name = args.phase_name or ""
            start_time = args.start_time or ""
            prompt_file = args.prompt_file or ""
            panel = render_phase_start(phase, phase_name, start_time, prompt_file)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "phase-start",
                    "phase": phase,
                    "next_action": get_next_action("phase-start")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "context-preload":
            # P01 上下文预加载提示
            feature_name = args.feature or ""
            source = args.context_source or "hub_status.json"
            panel = render_context_preload(feature_name, source)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "context-preload",
                    "feature": feature_name,
                    "next_action": get_next_action("context-preload")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "path-summary":
            # P04 服务路径配置汇总
            services = []
            if args.services_json:
                services = json.loads(args.services_json)
            panel = render_path_summary(services)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "path-summary",
                    "services": services,
                    "next_action": get_next_action("path-summary")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "advanced-prompt":
            # 高级模式命令提示
            feature_name = args.feature or "unknown"
            version = get_vaf_version()
            panel = render_advanced_prompt(feature_name, version)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "advanced-prompt",
                    "feature": feature_name,
                    "next_action": get_next_action("advanced-prompt")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "git-progress":
            # Git 提交执行进度
            steps = []
            if args.git_steps:
                steps = json.loads(args.git_steps)
            is_multi_repo = get_workspace_type() == "service"
            service_name = args.service
            hub_path = args.hub_path_display
            panel = render_git_progress(steps, is_multi_repo, service_name, hub_path)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "git-progress",
                    "next_action": get_next_action("git-progress")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "git-push-error":
            # 推送失败重试选项
            error_message = args.error_message or "unknown error"
            branch = args.branch or "unknown"
            panel = render_git_push_error(error_message, branch)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "git-push-error",
                    "next_action": get_next_action("git-push-error")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "defer-approval":
            # 暂不放行提示
            phase = args.phase or ""
            phase_name = args.phase_name or ""
            panel = render_defer_approval(phase, phase_name)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "defer-approval",
                    "phase": phase,
                    "next_action": get_next_action("defer-approval")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "refresh-status":
            # 刷新菜单状态输出
            status_file = "hub_status.json"
            if args.service:
                status_file = "phase_status.json"
            phases = {}
            if args.phases_json:
                phases = json.loads(args.phases_json)
            panel = render_refresh_status(status_file, phases)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "refresh-status",
                    "next_action": get_next_action("refresh-status")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "exit-summary":
            # 退出会话提示
            feature_name = args.feature or "unknown"
            progress = []
            if args.progress:
                progress = json.loads(args.progress)
            panel = render_exit_summary(feature_name, progress)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "exit-summary",
                    "feature": feature_name,
                    "next_action": get_next_action("exit-summary")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
        elif args.mode == "error":
            # 错误提示
            error_type = args.error_type or "unknown"
            details = {}
            if args.error_details:
                details = json.loads(args.error_details)
            panel = render_error(error_type, details)
            if args.json:
                output = {
                    "panel": panel,
                    "mode": "error",
                    "error_type": error_type,
                    "next_action": get_next_action("error")
                }
                print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
            else:
                print(panel)
        
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

