#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Vibe Flow 前端菜单渲染脚本（精简版）

- 仅支持前端 P/F 最小能力：hub / service 两种模式
- 渲染前端阶段：P01_F, P02_F, P03_F；F01-F06 + T01
- 不依赖主脚本的其他模式/确认面板
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
from datetime import datetime
from typing import Optional, Tuple, List
import unicodedata

P_PHASES = [
    ("P01_F", "前端需求采集"),
    ("P02_F", "前端知识库加载"),
    ("P03_F", "前端PRD评审"),
]

F_PHASES = [
    ("F01", "前端技术设计"),
    ("F02", "前端任务拆分"),
    ("F03", "前端实现"),
    ("F04", "前端测试"),
    ("F05", "前端代码审查"),
    ("F06", "前端集成执行"),
]

T_PHASES = [("T01", "E2E测试")]

PHASE_DETAILS = {
    "P01_F": {
        "name": "前端需求采集",
        "tasks": ["收集飞书+Figma", "解析资产", "生成 PRD_F"],
        "outputs": ["PRD_F.md", "assets_Figma.json"],
    },
    "P02_F": {
        "name": "前端知识库加载",
        "tasks": ["加载知识库", "冲突检测", "输出 knowledge_base_F"],
        "outputs": ["knowledge_base_F.md", "context_analysis_report_F.md"],
    },
    "P03_F": {
        "name": "前端PRD评审",
        "tasks": ["PRD_F 标准化", "原子功能拆解", "可行性评审"],
        "outputs": ["PRD_standardized_F.md", "atomic_functions_F.md", "PRD_review_report_F.md"],
    },
    "F01": {
        "name": "前端技术设计",
        "tasks": ["视觉/交互/逻辑基线", "状态管理/数据流", "A11y/回退"],
        "outputs": ["tech_design_frontend.md"],
    },
    "F02": {
        "name": "前端任务拆分",
        "tasks": ["页面/组件/数据流拆分", "依赖与 DoD"],
        "outputs": ["task_plan.md"],
    },
    "F03": {
        "name": "前端实现",
        "tasks": ["视觉(A)", "逻辑/数据流(B)", "交互(C)"],
        "outputs": ["tests_visual/tests_logic/tests_interaction"],
    },
    "F04": {
        "name": "前端测试",
        "tasks": ["视觉对比", "逻辑/交互测试", "不通过回 F03"],
        "outputs": ["tests_visual/tests_logic/tests_interaction（结果）"],
    },
    "F05": {
        "name": "前端代码审查",
        "tasks": ["视觉/交互一致性", "逻辑/性能/A11y", "安全/测试完整性"],
        "outputs": ["CR_Report.md"],
    },
    "F06": {
        "name": "前端集成执行",
        "tasks": ["关键用户流回归", "视觉/交互抽检", "缺陷/回退"],
        "outputs": ["Test_Report_FE.md"],
    },
    "T01": {
        "name": "E2E测试",
        "tasks": ["用例设计", "Playwright 自动化", "覆盖追溯"],
        "outputs": ["测试用例", "E2E_Test_Report.md", "脚本+截图"],
    },
}

STATUS_ICONS = {
    "not_started": "🔒",
    "ready": "👉",
    "in_progress": "🔄",
    "pending_review": "⏸️",
    "approved": "✅",
    "rejected": "❌",
    "blocked": "⛔",
}

BOX_BORDER = "┌─────────────────────────────────────────────────────────────────┐"
BOX_MID = "├─────────────────────────────────────────────────────────────────┤"
BOX_BOTTOM = "└─────────────────────────────────────────────────────────────────┘"
BOX_CONTENT_WIDTH = len(BOX_BORDER) - 5  # "│  " + content + " │"

def _is_wide_char(ch: str) -> bool:
    if not ch:
        return False
    return unicodedata.east_asian_width(ch) in ("W", "F")

def display_width(text: str) -> int:
    return sum(2 if _is_wide_char(ch) else 1 for ch in text)

def pad_to_width(text: str, width: int) -> str:
    pad_len = width - display_width(text)
    return text + (" " * max(pad_len, 0))

def box_line(text: str) -> str:
    return f"│  {pad_to_width(text, BOX_CONTENT_WIDTH)} │"

def load_json_file(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def calc_duration(started_at: Optional[str], updated_at: Optional[str]) -> str:
    if not started_at or not updated_at:
        return "--"
    try:
        start = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
        end = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
        diff = end - start
        mins = int(diff.total_seconds() / 60)
        if mins < 1:
            return "<1分钟"
        if mins < 60:
            return f"{mins}分钟"
        h, m = divmod(mins, 60)
        return f"{h}小时{m}分钟" if m else f"{h}小时"
    except Exception:
        return "--"

def format_phase_status(phase_data: dict, is_unlocked: bool) -> Tuple[str, str]:
    status = phase_data.get("status", "not_started")
    if status == "done":
        status = "approved"
    if status == "approved":
        duration = calc_duration(phase_data.get("started_at"), phase_data.get("updated_at"))
        return STATUS_ICONS["approved"], f"已放行 ({duration})"
    if status == "pending_review":
        return STATUS_ICONS["pending_review"], "待确认"
    if status == "in_progress":
        return STATUS_ICONS["in_progress"], "执行中"
    if status == "blocked":
        return STATUS_ICONS["blocked"], "阻塞"
    if status == "not_started":
        return (STATUS_ICONS["ready"], "待执行") if is_unlocked else (STATUS_ICONS["not_started"], "锁定")
    return "❓", status

def check_p_phase_unlock(phase: str, phases: dict) -> bool:
    order = ["P01_F", "P02_F", "P03_F"]
    if phase == "P01_F":
        return True
    if phase in order:
        idx = order.index(phase)
        if idx > 0:
            return phases.get(order[idx - 1], {}).get("status") == "approved"
    return False

def check_f_phase_unlock(phase: str, phases: dict) -> bool:
    order = ["F01", "F02", "F03", "F04", "F05", "F06", "T01"]
    if phase == "F01":
        return True
    if phase in order:
        idx = order.index(phase)
        if idx > 0:
            return phases.get(order[idx - 1], {}).get("status") == "approved"
    return False

def find_recommended(phases: dict, phase_list: List[Tuple[str, str]], unlock_fn) -> Optional[str]:
    for pid, _ in phase_list:
        pdata = phases.get(pid, {})
        status = pdata.get("status", "not_started")
        if status == "approved":
            continue
        if unlock_fn(pid, phases) and status in ["not_started", "pending_review"]:
            return pid
    return None

def render_hub_menu(hub_status: dict, service_statuses: list[dict], feature_name: str, vaf_version: str = "v0.2") -> str:
    phases = hub_status.get("phases", {})
    recommended = find_recommended(phases, P_PHASES, check_p_phase_unlock)
    approved_count = sum(1 for p, _ in P_PHASES if phases.get(p, {}).get("status") == "approved")
    total_count = len(P_PHASES)
    progress_bar = "█" * approved_count + "░" * (total_count - approved_count)

    lines = [f"📋 需求: {feature_name}  |  模式: 前端  |  VAF {vaf_version}", "",
             BOX_BORDER,
             box_line("P 阶段 (前端需求池)"),
             BOX_MID]

    for i, (pid, pname) in enumerate(P_PHASES):
        pdata = phases.get(pid, {"status": "not_started"})
        is_unlocked = check_p_phase_unlock(pid, phases)
        icon, status_text = format_phase_status(pdata, is_unlocked)
        mark = " <- 推荐下一步" if pid == recommended else ""
        line = f"[{i+1}] {icon} {pid} {pname:<10} {status_text}{mark}"
        lines.append(box_line(line))

    lines.extend([
        BOX_MID,
        box_line(f"进度: {progress_bar} {approved_count}/{total_count}"),
        box_line("[q] 退出"),
        BOX_BOTTOM,
        "",
        "请选择：",
    ])
    return "\n".join(lines)

def render_service_menu(phase_status: dict, service_name: str, feature_name: str, vaf_version: str = "v0.2") -> str:
    phases = phase_status.get("phases", {})
    all_phases = F_PHASES + T_PHASES
    recommended = find_recommended(phases, all_phases, check_f_phase_unlock)
    approved_count = sum(1 for p, _ in all_phases if phases.get(p, {}).get("status") == "approved")
    total_count = len(all_phases)
    progress_bar = "█" * approved_count + "░" * (total_count - approved_count)

    lines = [f"📋 服务: {service_name}  |  需求: {feature_name}  |  VAF {vaf_version}", "",
             BOX_BORDER,
             box_line("F 阶段 (前端交付)"),
             BOX_MID]

    for i, (pid, pname) in enumerate(all_phases):
        pdata = phases.get(pid, {"status": "not_started"})
        is_unlocked = check_f_phase_unlock(pid, phases)
        icon, status_text = format_phase_status(pdata, is_unlocked)
        mark = " <- 推荐下一步" if pid == recommended else ""
        line = f"[{i+1}] {icon} {pid} {pname:<12} {status_text}{mark}"
        lines.append(box_line(line))

    lines.extend([
        BOX_MID,
        box_line(f"进度: {progress_bar} {approved_count}/{total_count}"),
        box_line("[q] 退出"),
        BOX_BOTTOM,
        "",
        "请选择：",
    ])
    return "\n".join(lines)


def render_unified_menu(status: dict, feature_name: str, vaf_version: str = "v0.2") -> str:
    phases = status.get("phases", {})
    all_phases = P_PHASES + F_PHASES + T_PHASES
    recommended = find_recommended(phases, all_phases, lambda pid, ph: (check_p_phase_unlock(pid, ph) if pid.startswith("P0") else check_f_phase_unlock(pid, ph)))
    approved_count = sum(1 for p, _ in all_phases if phases.get(p, {}).get("status") == "approved")
    total_count = len(all_phases)
    progress_bar = "█" * approved_count + "░" * (total_count - approved_count)

    lines = [
        f"📋 需求: {feature_name}  |  模式: 前端（统一） |  VAF {vaf_version}",
        "",
        BOX_BORDER,
        box_line("P/F 阶段 (前端一体化)"),
        BOX_MID,
    ]

    for i, (pid, pname) in enumerate(all_phases):
        pdata = phases.get(pid, {"status": "not_started"})
        is_unlocked = check_p_phase_unlock(pid, phases) if pid.startswith("P0") else check_f_phase_unlock(pid, phases)
        icon, status_text = format_phase_status(pdata, is_unlocked)
        mark = " <- 推荐下一步" if pid == recommended else ""
        line = f"[{i+1}] {icon} {pid} {pname:<12} {status_text}{mark}"
        lines.append(box_line(line))

    lines.extend([
        BOX_MID,
        box_line(f"进度: {progress_bar} {approved_count}/{total_count}"),
        box_line("[q] 退出"),
        BOX_BOTTOM,
        "",
        "请选择：",
    ])
    return "\n".join(lines)


def format_menu_output(menu_str: str) -> str:
    lines = menu_str.split("\n")
    formatted = [lines[0], ""]
    formatted.extend([f"    {ln}" if ln else "" for ln in lines[1:]])
    return "\n".join(formatted)


def init_status_file(path: Path, feature: str):
    minimal = {
        "feature": feature,
        "frontend_flow": True,
        "p_frontend": True,
        "phases": {
            "P01_F": {"status": "not_started", "started_at": None, "updated_at": None, "comment": ""},
            "P02_F": {"status": "not_started", "started_at": None, "updated_at": None, "comment": ""},
            "P03_F": {"status": "not_started", "started_at": None, "updated_at": None, "comment": ""},
            "F01": {"status": "not_started", "started_at": None, "updated_at": None, "comment": ""},
            "F02": {"status": "not_started", "started_at": None, "updated_at": None, "comment": ""},
            "F03": {"status": "not_started", "started_at": None, "updated_at": None, "comment": ""},
            "F04": {"status": "not_started", "started_at": None, "updated_at": None, "comment": ""},
            "F05": {"status": "not_started", "started_at": None, "updated_at": None, "comment": ""},
            "F06": {"status": "not_started", "started_at": None, "updated_at": None, "comment": ""},
            "T01": {"status": "not_started", "started_at": None, "updated_at": None, "comment": ""}
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(minimal, f, ensure_ascii=False, indent=2)
    return minimal

def main():
    parser = argparse.ArgumentParser(description="Vibe Flow 前端菜单渲染工具（hub/service/unified，缺失状态可自动初始化）")
    parser.add_argument("--mode", "-m", choices=["hub", "service", "unified"], required=True)
    parser.add_argument("--status", "-s", required=True, help="状态文件路径 (hub_status.json 或 phase_status.json)")
    parser.add_argument("--feature", "-f", help="需求名称")
    parser.add_argument("--service", help="服务名称 (service 模式必填)")
    parser.add_argument("--services-dir", "-d", help="服务状态目录 (.service_status/) 可选")
    parser.add_argument("--version", "-v", default="v2.0")
    parser.add_argument(
        "--require-paths",
        help="逗号分隔的必需文件/目录列表；任一缺失将阻断（适配 F03/F04/F05/F06 放行前校验）"
    )
    args = parser.parse_args()

    try:
        if args.mode == "hub":
            status_path = Path(args.status)
            if status_path.exists():
                hub_status = load_json_file(args.status)
            else:
                feature_name = args.feature or "unknown"
                hub_status = init_status_file(status_path, feature_name)
            feature_name = args.feature or hub_status.get("feature", "unknown")
            service_statuses = []
            if args.services_dir:
                services_path = Path(args.services_dir)
                if services_path.exists():
                    for jf in services_path.glob("*.json"):
                        service_statuses.append(load_json_file(str(jf)))
            menu = render_hub_menu(hub_status, service_statuses, feature_name, args.version)
            print(format_menu_output(menu))

        elif args.mode == "service":
            status_path = Path(args.status)
            if status_path.exists():
                phase_status = load_json_file(args.status)
            else:
                feature_name = args.feature or "unknown"
                phase_status = init_status_file(status_path, feature_name)
            feature_name = args.feature or phase_status.get("feature", "unknown")
            service_name = args.service or phase_status.get("service", "unknown")

            # 可选：放行前文件存在校验
            if args.require_paths:
                missing = []
                for p in [p.strip() for p in args.require_paths.split(',') if p.strip()]:
                    if not Path(p).exists():
                        missing.append(p)
                if missing:
                    print(f"❌ 缺少必需文件/目录，阻断放行: {', '.join(missing)}", file=sys.stderr)
                    sys.exit(1)
            menu = render_service_menu(phase_status, service_name, feature_name, args.version)
            print(format_menu_output(menu))

        elif args.mode == "unified":
            status_path = Path(args.status)
            if status_path.exists():
                status_data = load_json_file(args.status)
            else:
                feature_name = args.feature or "unknown"
                status_data = init_status_file(status_path, feature_name)
            feature_name = args.feature or status_data.get("feature", "unknown")
            menu = render_unified_menu(status_data, feature_name, args.version)
            print(format_menu_output(menu))

    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
