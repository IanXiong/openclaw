#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VAF 埋点模块 - 使用数据采集与上报（重构后主入口）

重构说明：
- vaf_telemetry_env.py: 环境检测（IDE/OS/Git）
- vaf_telemetry_bitable.py: 飞书表格操作
- vaf_telemetry.py: 主入口+数据采集（本文件）
"""

import sys

# Windows 编码兼容：确保 stdout/stderr 使用 UTF-8，避免 emoji 输出时 GBK 编码错误
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

# 兼容相对导入和直接导入
try:
    from .vibe_helpers import (
        get_feature_dir_name, find_project_root as _find_project_root,
        get_workspace_type, load_json_file, get_vaf_version,
    )
    from .vaf_telemetry_env import (
        get_os_type, get_branch_name, get_ide_tool,
        get_git_remote_url_for_path, get_larkkit_version, get_codex_model,
    )
    from .vaf_telemetry_bitable import (
        _ensure_larkkit_installed, find_existing_record, report_to_bitable,
        get_ide_model_options, _DEFAULT_MODELS, normalize_model_name,
    )
except ImportError:
    from vibe_helpers import (
        get_feature_dir_name, find_project_root as _find_project_root,
        get_workspace_type, load_json_file, get_vaf_version,
    )
    from vaf_telemetry_env import (
        get_os_type, get_branch_name, get_ide_tool,
        get_git_remote_url_for_path, get_larkkit_version, get_codex_model,
    )
    from vaf_telemetry_bitable import (
        _ensure_larkkit_installed, find_existing_record, report_to_bitable,
        get_ide_model_options, _DEFAULT_MODELS, normalize_model_name,
    )

BJ_TZ = timezone(timedelta(hours=8))
STATUS_MAP = {"not_started": "未开始", "in_progress": "进行中", "pending_review": "待审核", "approved": "已完成"}
MODE_MAP = {"single": "单服务", "multi": "多服务", "undetermined": "未确定"}


def find_project_root() -> Path:
    try:
        return _find_project_root()
    except FileNotFoundError:
        return Path.cwd()


def find_hub_path(feature: str = None) -> str:
    root = find_project_root()
    ws_type = get_workspace_type()
    if ws_type == "service" and feature:
        feature_dir = get_feature_dir_name(feature)
        manifest_path = root / "docs" / "features" / feature_dir / "manifest_local.json"
        if manifest_path.exists():
            data = load_json_file(str(manifest_path))
            if data.get("hub_path"):
                return data["hub_path"]
        features_dir = root / "docs" / "features"
        if features_dir.exists():
            for fp in features_dir.iterdir():
                if fp.is_dir():
                    mp = fp / "manifest_local.json"
                    if mp.exists():
                        d = load_json_file(str(mp))
                        if d.get("hub_path"):
                            return d["hub_path"]
    return str(root)


def get_unique_key(req_pool: str, service: str, branch: str, phase: str) -> str:
    return f"{req_pool}|{service}|{branch}|{phase}"


def get_requirement_pool(hub_path: str = None, feature: str = None) -> str:
    if hub_path:
        return Path(hub_path).resolve().name
    return Path(find_hub_path(feature)).resolve().name


def get_service_name(service_path: str = None) -> str:
    if service_path:
        return Path(service_path).resolve().name
    return find_project_root().name


def get_execution_mode(hub_path: str, feature: str) -> str:
    """
    获取执行模式
    
    规则：
    - P04 未完成（execution_manifest.json 不存在）：返回 "未确定"
    - P04 已完成：根据 manifest 中的 mode 返回 "单服务" 或 "多服务"
    """
    feature_dir = get_feature_dir_name(feature)
    manifest_path = Path(hub_path) / "features" / feature_dir / "docs" / "P05_preliminary_design" / "execution_manifest.json"
    
    # P04 未完成时，execution_manifest.json 不存在，返回 "未确定"
    if not manifest_path.exists():
        return "未确定"
    
    try:
        data = load_json_file(str(manifest_path))
        mode = data.get("execution_mode") or data.get("mode", "single")
        return MODE_MAP.get(mode, "单服务")
    except Exception:
        return "未确定"


def get_service_count(hub_path: str, feature: str) -> int:
    feature_dir = get_feature_dir_name(feature)
    manifest_path = Path(hub_path) / "features" / feature_dir / "docs" / "P05_preliminary_design" / "execution_manifest.json"
    if manifest_path.exists():
        try:
            data = load_json_file(str(manifest_path))
            services = data.get("services", [])
            return len(services) if services else 1
        except Exception:
            pass
    return 1


def get_current_status(feature: str, phase: str) -> str:
    root = find_project_root()
    ws_type = get_workspace_type()
    feature_dir = get_feature_dir_name(feature)
    if ws_type == "hub":
        status_file = root / "features" / feature_dir / "docs" / "hub_status.json"
    else:
        status_file = root / "docs" / "features" / feature_dir / "phase_status.json"
    status = "not_started"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding='utf-8'))
            status = data.get("phases", {}).get(phase, {}).get("status", "not_started")
        except (json.JSONDecodeError, KeyError):
            pass
    return STATUS_MAP.get(status, "未开始")


def get_execution_times(feature: str, phase: str) -> int:
    root = find_project_root()
    ws_type = get_workspace_type()
    feature_dir = get_feature_dir_name(feature)
    if ws_type == "hub":
        status_file = root / "features" / feature_dir / "docs" / "hub_status.json"
    else:
        status_file = root / "docs" / "features" / feature_dir / "phase_status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding='utf-8'))
            return data.get("phases", {}).get(phase, {}).get("execution_times", 1)
        except (json.JSONDecodeError, KeyError):
            pass
    return 1


def get_phase_info(feature: str, phase: str) -> Optional[Dict[str, Any]]:
    root = find_project_root()
    ws_type = get_workspace_type()
    feature_dir = get_feature_dir_name(feature)
    if ws_type == "hub":
        status_file = root / "features" / feature_dir / "docs" / "hub_status.json"
    else:
        status_file = root / "docs" / "features" / feature_dir / "phase_status.json"
    if status_file.exists():
        try:
            data = json.loads(status_file.read_text(encoding='utf-8'))
            return data.get("phases", {}).get(phase, {})
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def parse_time_to_ms(time_str: str) -> Optional[int]:
    if not time_str:
        return None
    try:
        if '+' in time_str or time_str.endswith('Z'):
            dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            return int(dt.timestamp() * 1000)
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                dt = datetime.strptime(time_str, fmt).replace(tzinfo=BJ_TZ)
                return int(dt.timestamp() * 1000)
            except ValueError:
                continue
    except Exception:
        pass
    return None


def get_git_remote_url(feature: str = None) -> Dict[str, str]:
    result = {"hub": "", "service": ""}
    ws_type = get_workspace_type()
    current_url = get_git_remote_url_for_path()
    if ws_type == "hub":
        result["hub"] = current_url
    else:
        result["service"] = current_url
        hub_path = find_hub_path(feature)
        if hub_path != str(find_project_root()):
            result["hub"] = get_git_remote_url_for_path(hub_path)
    return result


def collect_telemetry_data(phase: str, feature: str, **kwargs) -> Dict[str, Any]:
    hub_path = kwargs.get("hub_path") or find_hub_path(feature)
    service_path = kwargs.get("service_path") or str(find_project_root())
    ws_type = get_workspace_type()
    now = datetime.now(BJ_TZ)
    repo_type = "需求池" if ws_type == "hub" else "服务"
    
    data = {
        "日期": int(now.timestamp() * 1000),
        "上报时间": int(now.timestamp() * 1000),
        "版本号": get_vaf_version(),
        "需求池": get_requirement_pool(hub_path, feature),
        "仓库类型": repo_type,
        "分支名": get_branch_name(),
        "执行阶段": phase,
        "操作系统": get_os_type(),
        "IDE工具": get_ide_tool(),
        "当前状态": kwargs.get("status") or get_current_status(feature, phase),
        "执行模式": get_execution_mode(hub_path, feature),
        "涉及服务数": get_service_count(hub_path, feature),
        "执行次数": get_execution_times(feature, phase),
        "larkkit版本": get_larkkit_version(),
    }
    
    # 模型：如果未传入则使用 Codex CLI 的第一个模型作为默认值
    model = kwargs.get("model")
    if not model:
        models = get_ide_model_options("Codex CLI")
        model = models[0] if models else _DEFAULT_MODELS[0]
    # 标准化模型名称（忽略大小写，统一分隔符）
    data["模型"] = normalize_model_name(model)
    
    # 模型节点：仅当模型包含 codex 关键字时，才读取 ~/.codex/config.toml
    if "codex" in data["模型"].lower():
        codex_model = get_codex_model()
        if codex_model:
            data["模型节点"] = codex_model
    if ws_type == "service":
        data["服务"] = get_service_name(service_path)
    
    # 用户字段不传值，飞书多维表格会自动使用创建记录的用户
    
    git_urls = get_git_remote_url(feature)
    if git_urls["hub"]:
        data["需求仓git地址"] = {"link": git_urls["hub"], "text": git_urls["hub"]}
    if git_urls["service"]:
        data["服务仓git地址"] = {"link": git_urls["service"], "text": git_urls["service"]}
    
    phase_info = get_phase_info(feature, phase)
    if phase_info:
        if phase_info.get("started_at"):
            data["开始时间"] = parse_time_to_ms(phase_info["started_at"])
        if phase_info.get("updated_at"):
            data["完成时间"] = parse_time_to_ms(phase_info["updated_at"])
        data["对话轮次"] = phase_info.get("conversation_turns", 0)
    else:
        data["对话轮次"] = 0
    
    if kwargs.get("turns") is not None:
        data["对话轮次"] = kwargs["turns"]
    if kwargs.get("start_time"):
        data["开始时间"] = kwargs["start_time"]
    if kwargs.get("end_time"):
        data["完成时间"] = kwargs["end_time"]
    
    if data.get("开始时间") and data.get("完成时间"):
        duration_ms = data["完成时间"] - data["开始时间"]
        duration_minutes = max(1, int(duration_ms / 60000))
        if duration_minutes >= 60:
            hours, mins = duration_minutes // 60, duration_minutes % 60
            data["耗时"] = f"{hours}小时{mins}分钟" if mins > 0 else f"{hours}小时"
        else:
            data["耗时"] = f"{duration_minutes}分钟"
    
    return data


def safe_report_telemetry(phase: str, feature: str, **kwargs) -> bool:
    try:
        return report_telemetry(phase, feature, **kwargs)
    except Exception as e:
        print(f"⚠️ 埋点上报失败（不影响主流程）: {e}")
        return False


def report_telemetry(phase: str, feature: str, **kwargs) -> bool:
    dry_run = kwargs.pop("dry_run", False)
    if not dry_run and not _ensure_larkkit_installed():
        return False
    
    print(f"\n🔍 正在采集埋点数据...")
    data = collect_telemetry_data(phase, feature, **kwargs)
    
    def fmt_time(ts):
        return datetime.fromtimestamp(ts/1000, BJ_TZ).strftime('%Y-%m-%d %H:%M:%S') if ts else "-"
    def fmt_date(ts):
        return datetime.fromtimestamp(ts/1000, BJ_TZ).strftime('%Y-%m-%d') if ts else "-"
    
    service = data.get('服务') or ""
    print(f"\n📋 上报数据预览:")
    print(f"   日期: {fmt_date(data.get('日期'))}")
    print(f"   版本号: {data.get('版本号', '-')}")
    print(f"   仓库类型: {data.get('仓库类型', '-')}")
    print(f"   需求池: {data.get('需求池', '-')}")
    print(f"   服务: {service or '-'}")
    print(f"   分支名: {data.get('分支名', '-')}")
    print(f"   执行阶段: {phase}")
    print(f"   开始时间: {fmt_time(data.get('开始时间'))}")
    print(f"   完成时间: {fmt_time(data.get('完成时间'))}")
    print(f"   耗时: {data.get('耗时', '-')}")
    print(f"   对话轮次: {data.get('对话轮次', 0)}")
    print(f"   当前状态: {data.get('当前状态', '-')}")
    print(f"   执行模式: {data.get('执行模式', '-')}")
    print(f"   操作系统: {data.get('操作系统', '-')}")
    print(f"   IDE工具: {data.get('IDE工具', '-')}")
    print(f"   模型: {data.get('模型', '-')}")
    print(f"   模型节点: {data.get('模型节点', '-')}")
    print(f"   执行次数: {data.get('执行次数', '-')}")
    print(f"   涉及服务数: {data.get('涉及服务数', '-')}")
    print(f"   larkkit版本: {data.get('larkkit版本', '-')}")
    print(f"   上报时间: {fmt_time(data.get('上报时间'))}")
    
    if dry_run:
        print(f"\n📋 [dry-run] 完整数据:")
        for key, value in data.items():
            print(f"   {key}: {value}")
        return True
    
    print(f"\n🔍 查询现有记录...")
    unique_key = get_unique_key(data["需求池"], service, data["分支名"], phase)
    print(f"   唯一键: {unique_key}")
    
    record_id = find_existing_record(data["需求池"], service, data["分支名"], phase)
    if record_id:
        print(f"   ✅ 找到现有记录: {record_id[:8]}...")
    else:
        print(f"   📝 未找到记录，将新增")
    
    print(f"\n📤 正在上报...")
    return report_to_bitable(data, record_id)


def report_all_phases(feature: str, model: str = None, dry_run: bool = False) -> bool:
    root = find_project_root()
    ws_type = get_workspace_type()
    feature_dir = get_feature_dir_name(feature)
    
    if ws_type == "hub":
        status_file = root / "features" / feature_dir / "docs" / "hub_status.json"
    else:
        status_file = root / "docs" / "features" / feature_dir / "phase_status.json"
    
    if not status_file.exists():
        print(f"❌ 状态文件不存在: {status_file}")
        return False
    
    try:
        data = json.loads(status_file.read_text(encoding='utf-8'))
        phases = data.get("phases", {})
    except (json.JSONDecodeError, KeyError) as e:
        print(f"❌ 解析状态文件失败: {e}")
        return False
    
    if not phases:
        print(f"⚠️ 状态文件中没有阶段数据")
        return False
    
    total, success = 0, 0
    print(f"\n📊 批量上报分支 [{feature}] 的所有阶段...")
    print(f"   状态文件: {status_file}")
    print(f"   工作区类型: {ws_type}\n")
    
    phase_order = ["P01", "P02", "P03", "P04", "P05", "P06-A", "P06-B", "P06-C",
                   "S01", "S02", "S03", "S04", "S05", "S06", "S07", "T01"]
    
    for phase in phase_order:
        if phase not in phases:
            continue
        info = phases[phase]
        status = info.get("status", "not_started")
        total += 1
        status_icon = {"approved": "✅", "pending_review": "🔄", "in_progress": "🔵", "not_started": "⚪"}.get(status, "❓")
        print(f"\n{'='*50}")
        print(f"📤 [{total}] {phase} {status_icon} {status}")
        if safe_report_telemetry(phase, feature, model=model, dry_run=dry_run):
            success += 1
    
    print(f"\n{'='*50}")
    print(f"\n📊 上报完成: 总计 {total} 个阶段, 成功 {success} 个, 失败 {total - success} 个")
    return success == total


def show_report_status(feature: str) -> bool:
    root = find_project_root()
    ws_type = get_workspace_type()
    if ws_type == "hub":
        status_file = root / "features" / feature / ".vibe" / "phase_status.json"
    else:
        status_file = root / "docs" / "features" / feature / ".vibe" / "phase_status.json"
    
    if not status_file.exists():
        print(f"❌ 状态文件不存在: {status_file}")
        return False
    
    try:
        data = json.loads(status_file.read_text(encoding='utf-8'))
        phases = data.get("phases", {})
    except (json.JSONDecodeError, KeyError) as e:
        print(f"❌ 解析状态文件失败: {e}")
        return False
    
    print(f"\n📊 需求 [{feature}] 阶段状态:\n")
    print(f"{'阶段':<8} {'状态':<15} {'执行次数':<10}")
    print("-" * 40)
    
    for phase in ["P01", "P02", "P03", "P04", "P05", "P06-A", "P06-B", "P06-C",
                  "S01", "S02", "S03", "S04", "S05", "S06", "S07", "T01"]:
        info = phases.get(phase, {})
        status = info.get("status", "not_started")
        exec_times = info.get("execution_times", 0)
        icon = {"approved": "✅", "pending_review": "🔄", "in_progress": "🔵"}.get(status, "⚪")
        print(f"{phase:<8} {icon} {status:<12} {exec_times}")
    
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="VAF 埋点工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")
    
    report_parser = subparsers.add_parser("report", help="上报埋点数据")
    report_parser.add_argument("phase", help="阶段标识（P01-T01）或 all")
    report_parser.add_argument("--feature", "-f", required=True, help="需求名称")
    report_parser.add_argument("--turns", "-t", type=int, help="对话轮次")
    report_parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际上报")
    
    status_parser = subparsers.add_parser("status", help="查看上报状态")
    status_parser.add_argument("--feature", "-f", required=True, help="需求名称")
    
    args = parser.parse_args()
    
    if args.command == "report":
        if args.phase.lower() == "all":
            success = report_all_phases(args.feature, dry_run=args.dry_run)
        else:
            success = safe_report_telemetry(args.phase, args.feature, turns=args.turns, dry_run=args.dry_run)
        sys.exit(0 if success else 1)
    elif args.command == "status":
        success = show_report_status(args.feature)
        sys.exit(0 if success else 1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
