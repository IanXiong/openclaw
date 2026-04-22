#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
前端状态更新脚本（独立，不影响后端）
- 支持前端 P/F 阶段状态写入
- action: start/pending/approve/reset
- 记录 started_at/updated_at，计算耗时（approve 时）
- 路径可用于 hub_status.json 或 phase_status.json
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
from datetime import datetime, timezone, timedelta
from pathlib import Path

VALID_TRANSITIONS = {
    "not_started": ["in_progress"],
    "in_progress": ["pending_review", "not_started"],
    "pending_review": ["approved", "in_progress"],
    "approved": [],
    "blocked": ["in_progress"],
    "rejected": ["in_progress"],
}

ACTION_TO_STATUS = {
    "start": "in_progress",
    "pending": "pending_review",
    "approve": "approved",
    "reset": "not_started",
    "block": "blocked",
}


def now_cn():
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_json(path: str, data: dict) -> bool:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def calc_duration(started_at: str, updated_at: str) -> str:
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


def update_phase(status_file: str, phase: str, action: str, comment: str = "") -> dict:
    result = {"success": False, "phase": phase, "previous_status": None, "new_status": None, "message": ""}
    if action not in ACTION_TO_STATUS:
        result["message"] = f"无效 action: {action}"
        return result
    target = ACTION_TO_STATUS[action]
    data = load_json(status_file)
    data.setdefault("phases", {})
    pdata = data["phases"].get(phase, {"status": "not_started", "started_at": None, "updated_at": None, "comment": ""})
    curr = pdata.get("status", "not_started")
    result["previous_status"] = curr
    if target not in VALID_TRANSITIONS.get(curr, []):
        result["message"] = f"非法状态转换: {curr} -> {target}"
        return result
    now = now_cn()
    pdata["status"] = target
    pdata["updated_at"] = now
    if action == "start":
        pdata["started_at"] = now
    if action == "reset":
        pdata["started_at"] = None
    if comment:
        pdata["comment"] = comment
    data["phases"][phase] = pdata
    data["updated_at"] = now
    if not save_json(status_file, data):
        result["message"] = f"保存失败: {status_file}"
        return result
    result.update({"success": True, "new_status": target, "started_at": pdata.get("started_at"), "updated_at": now})
    if action == "approve":
        result["duration"] = calc_duration(pdata.get("started_at"), now)
        result["message"] = f"{phase} 已放行，耗时 {result['duration']}"
    else:
        result["message"] = f"{phase} 状态更新为 {target}"
    return result


def main():
    parser = argparse.ArgumentParser(description="前端状态更新工具（独立，不影响后端）")
    parser.add_argument("--status", required=True, help="状态文件路径 (hub_status.json / phase_status.json)")
    parser.add_argument("--phase", required=True, help="阶段 ID，例如 P01_F/F01")
    parser.add_argument("--action", required=True, choices=list(ACTION_TO_STATUS.keys()))
    parser.add_argument("--comment", default="", help="备注")
    args = parser.parse_args()
    result = update_phase(args.status, args.phase, args.action, args.comment)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("success") else 1)

if __name__ == "__main__":
    main()
