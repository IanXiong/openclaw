#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
VAF 版本检测脚本

功能：
- 日常使用时：检测本地 VAF vs 远程 VAF、工作区 vs 目标版本
- Hub 初始化时：检测本地 VAF vs 远程 VAF
- 24 小时静默机制
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
import os
import subprocess
import shutil
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict

try:
    from vibe_helpers import (
        find_project_root,
        get_workspace_type,
        load_json_file,
        save_json_file,
    )
except ImportError:
    from .vibe_helpers import (
        find_project_root,
        get_workspace_type,
        load_json_file,
        save_json_file,
    )

# ============ 常量与配置 ============

SKIP_CONFIG_PATH = Path.home() / ".vaf" / "upgrade_config.json"

SKIP_KEYS = {
    "vaf_remote": "vaf_remote_skip_timestamp",
    "hub_vaf": "hub_vaf_skip_timestamp",
    "service_hub": "service_hub_skip_timestamp",
}

VAF_RELEASE_BITABLE_URL = "https://mi.feishu.cn/wiki/WfwgwSSrti2z3okmnTJcIIqCnlI?table=tblb57rJcMeG8WEe&view=vewu8hKfFF"


# ============ 通用工具 ============


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _load_skip_config() -> Dict[str, str]:
    if not SKIP_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(SKIP_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_skip_config(config: Dict[str, str]) -> None:
    save_json_file(str(SKIP_CONFIG_PATH), config, indent=2)


def _record_skip(skip_key: str) -> None:
    config = _load_skip_config()
    config[skip_key] = _now_iso()
    _save_skip_config(config)


def _is_skip_valid(skip_timestamp: str) -> bool:
    if not skip_timestamp:
        return False
    try:
        skip_time = datetime.fromisoformat(skip_timestamp)
        if skip_time.tzinfo is None:
            skip_time = skip_time.replace(tzinfo=timezone.utc)
        now = datetime.now(skip_time.tzinfo)
        return now - skip_time < timedelta(hours=24)
    except Exception:
        return False


def _is_skip_active(skip_key: str) -> bool:
    config = _load_skip_config()
    return _is_skip_valid(config.get(skip_key, ""))


def _compare_versions(left: str, right: str) -> int:
    try:
        from packaging.version import Version

        return (Version(left) > Version(right)) - (Version(left) < Version(right))
    except Exception:
        return (left > right) - (left < right)


def _is_version_behind(current: str, target: str) -> bool:
    return _compare_versions(current, target) < 0


def _parse_bitable_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    if not url:
        return None, None
    import re

    table_match = re.search(r"[?&]table=([^&]+)", url)
    table_id = table_match.group(1) if table_match else None
    wiki_match = re.search(r"/wiki/([^?/]+)", url)
    wiki_token = wiki_match.group(1) if wiki_match else None
    return wiki_token, table_id


# ============ 版本信息获取 ============


def _get_vaf_source_path() -> Optional[Path]:
    env_path = os.getenv("VAF_SOURCE_PATH", "").strip()
    if env_path:
        candidate = Path(env_path).expanduser().resolve()
        if (candidate / "VERSION").exists():
            return candidate

    # 当前脚本位于 VAF 源码仓时
    for parent in Path(__file__).resolve().parents:
        if (parent / "VERSION").exists() and (parent / "tools").exists():
            return parent

    # 从 vaf.py 或 vaf 命令定位
    vaf_cmd = shutil.which("vaf.py") or shutil.which("vaf")
    if vaf_cmd:
        resolved = Path(vaf_cmd).resolve()
        # vaf.py 位于 tools/vaf.py，VAF 根目录是其父目录的父目录
        if resolved.name == "vaf.py" and resolved.parent.name == "tools":
            candidate = resolved.parent.parent
            if (candidate / "VERSION").exists():
                return candidate
        # 否则向上查找
        for parent in resolved.parents:
            if (parent / "VERSION").exists() and (parent / "tools").exists():
                return parent

    # 尝试同级目录
    try:
        root = find_project_root()
        sibling = root.parent / "vibe-agentic-flow"
        if (sibling / "VERSION").exists():
            return sibling
    except FileNotFoundError:
        pass

    return None


def _get_local_vaf_version(vaf_path: Path) -> Optional[str]:
    version_file = vaf_path / "VERSION"
    if version_file.exists():
        version = version_file.read_text(encoding="utf-8").strip()
        return version or None
    return None


def _get_remote_vaf_version(vaf_path: Path, timeout: int = 5) -> Optional[str]:
    try:
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=vaf_path,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            ["git", "show", "origin/main:VERSION"],
            cwd=vaf_path,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:
        return None
    return None


def _get_release_info(version: str) -> Dict[str, Optional[str]]:
    """从飞书多维表格获取版本发布信息（使用 larkkit CLI）"""
    if not VAF_RELEASE_BITABLE_URL:
        return {}

    # 添加 infra 目录到 path 以导入 larkkit_deps
    infra_dir = Path(__file__).parent.parent / "infra"
    if str(infra_dir) not in sys.path:
        sys.path.insert(0, str(infra_dir))

    try:
        import larkkit_deps
    except ImportError:
        return {}

    wiki_token, table_id = _parse_bitable_url(VAF_RELEASE_BITABLE_URL)
    if not wiki_token or not table_id:
        return {}

    try:
        # 使用 CLI 方式获取 app_token
        wiki_url = f"https://mi.feishu.cn/wiki/{wiki_token}"
        success, app_token, _, error_msg = larkkit_deps.run_larkkit_bitable_resolve_token(wiki_url)
        if not success or not app_token:
            return {}

        # 使用 CLI 方式查询记录
        success, records, error_msg = larkkit_deps.run_larkkit_bitable_list(
            app_token,
            table_id,
            filter_expr=f'CurrentValue.[版本]="{version}"',
        )
        if not success or not records:
            return {}

        fields = records[0].get("fields", {}) if records else {}
        # 发版文档字段可能是链接对象 {'link': '...', 'text': '...'} 或字符串或富文本列表
        release_doc = fields.get("发版文档") or fields.get("release_notes_url")
        if isinstance(release_doc, dict):
            release_notes_url = release_doc.get("link") or release_doc.get("text")
        elif isinstance(release_doc, list):
            # 富文本列表: [{'text': '...', 'type': 'text'}]
            release_notes_url = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in release_doc
            )
        else:
            release_notes_url = release_doc
        min_compat = fields.get("最低兼容版本") or fields.get("min_compatible")
        if isinstance(min_compat, list):
            min_compat = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in min_compat
            )
        elif isinstance(min_compat, dict):
            min_compat = min_compat.get("text") or min_compat.get("value")
        return {
            "min_compatible": min_compat,
            "release_notes_url": release_notes_url,
        }
    except Exception:
        return {}


def _check_compatibility(local_ver: str, remote_ver: str) -> Tuple[bool, Optional[str], Dict[str, Optional[str]]]:
    release_info = _get_release_info(remote_ver)
    min_compatible = release_info.get("min_compatible") if release_info else None
    if not min_compatible:
        return True, None, release_info
    return _compare_versions(local_ver, min_compatible) >= 0, min_compatible, release_info


# ============ 提示输出 ============


def _print_box_line(text: str = "") -> None:
    print(f"│  {text:<58}│")


def _print_remote_upgrade_prompt(
    local_version: str,
    remote_version: str,
    compatible: bool,
    release_notes_url: Optional[str],
    option_line: str,
) -> None:
    title = "⚠️ VAF 源码仓有更新（建议升级）" if compatible else "⚠️ VAF 源码仓有更新（警告升级）"
    print(f"┌{'─' * 62}┐")
    _print_box_line(title)
    print(f"├{'─' * 62}┤")
    _print_box_line(f"本地版本: {local_version}")
    _print_box_line(f"远程版本: {remote_version}")
    _print_box_line()
    if compatible:
        _print_box_line("📋 兼容性：✅ 完全兼容当前版本")
    else:
        _print_box_line("📋 兼容性：❌ 不兼容当前版本")
        _print_box_line("   如有进行中的需求，升级后可能导致兼容问题")
    print(f"├{'─' * 62}┤")
    _print_box_line(option_line)
    print(f"└{'─' * 62}┘")
    # 链接放到框外显示，避免超出边框
    print("\n📖 新版本了解（升级必看）:")
    if release_notes_url:
        print(f"   • 发版文档: {release_notes_url}")
    print(f"   • 版本清单: {VAF_RELEASE_BITABLE_URL}")
    print()  # 在升级必看和请选择之间增加空行


def _print_workspace_upgrade_prompt(
    title: str,
    current_label: str,
    current_version: str,
    target_label: str,
    target_version: str,
    hint: Optional[str],
    option_line: str,
) -> None:
    print(f"┌{'─' * 62}┐")
    _print_box_line(title)
    print(f"├{'─' * 62}┤")
    _print_box_line(f"{current_label}: {current_version}")
    _print_box_line(f"{target_label}: {target_version}")
    if hint:
        _print_box_line()
        _print_box_line(hint)
    print(f"├{'─' * 62}┤")
    _print_box_line(option_line)
    print(f"└{'─' * 62}┘")


# ============ 业务逻辑 ============


def _run_workspace_upgrade(ws_type: str, hub_path: Optional[str] = None) -> bool:
    # 升级使用 VAF 源码仓的脚本（与 vaf init hub-workspace / service-workspace 保持一致）
    vaf_path = _get_vaf_source_path()
    if not vaf_path:
        print("❌ 未找到 VAF 源码仓路径")
        print("   请确保 vaf.py 在 PATH 中，或设置 VAF_SOURCE_PATH 环境变量")
        return False
    
    script_dir = vaf_path / "tools" / "scripts" / "init"
    if ws_type == "hub":
        script_path = script_dir / "vibe_init_hub_workspace.py"
        cmd = [sys.executable, str(script_path)]
    else:
        if not hub_path:
            print("❌ 未指定 Hub 路径")
            return False
        script_path = script_dir / "vibe_init_service_workspace.py"
        cmd = [sys.executable, str(script_path), "-p", hub_path]

    if not script_path.exists():
        print(f"❌ 初始化脚本不存在: {script_path}")
        return False

    print("\n🔄 正在升级 .vibe 资产...")
    try:
        result = subprocess.run(cmd, cwd=str(find_project_root()))
        if result.returncode == 0:
            print("✅ 升级完成！\n")
            return True
        print(f"❌ 升级失败，退出码: {result.returncode}")
        return False
    except Exception as exc:
        print(f"❌ 升级异常: {exc}")
        return False


def _get_latest_hub_info_for_service() -> Tuple[Optional[str], Optional[str]]:
    try:
        root = find_project_root()
    except FileNotFoundError:
        return None, None

    features_dir = root / "docs" / "features"
    if not features_dir.exists():
        return None, None

    latest_hub_path = None
    latest_version = None
    latest_mtime = 0.0

    for feature_dir in features_dir.iterdir():
        if not feature_dir.is_dir():
            continue
        manifest_path = feature_dir / "manifest_local.json"
        if not manifest_path.exists():
            continue

        try:
            mtime = manifest_path.stat().st_mtime
            if mtime <= latest_mtime:
                continue

            manifest = load_json_file(str(manifest_path))
            hub_path = manifest.get("hub_path")
            if not hub_path:
                continue

            hub_workspace_json = Path(hub_path).expanduser().resolve() / ".vibe" / "workspace.json"
            if not hub_workspace_json.exists():
                continue

            hub_data = load_json_file(str(hub_workspace_json))
            hub_version = hub_data.get("vaf_version")
            if not hub_version:
                continue

            latest_mtime = mtime
            latest_hub_path = str(Path(hub_path).expanduser().resolve())
            latest_version = hub_version
        except Exception:
            continue

    return latest_version, latest_hub_path


def _get_workspace_version() -> Optional[str]:
    try:
        root = find_project_root()
    except FileNotFoundError:
        return None
    ws_file = root / ".vibe" / "workspace.json"
    if not ws_file.exists():
        return None
    ws_data = load_json_file(str(ws_file))
    return ws_data.get("vaf_version")


def _check_vaf_remote_update(vaf_path: Optional[Path], prompt_mode: str) -> bool:
    if not vaf_path:
        return False

    local_version = _get_local_vaf_version(vaf_path)
    remote_version = _get_remote_vaf_version(vaf_path)

    if not local_version or not remote_version:
        print("ℹ️  未检测到版本更新")
        return False

    if not _is_version_behind(local_version, remote_version):
        return False

    skip_key = SKIP_KEYS["vaf_remote"]
    if _is_skip_active(skip_key):
        return False

    compatible, min_compatible, release_info = _check_compatibility(local_version, remote_version)
    release_notes_url = release_info.get("release_notes_url") if release_info else None
    _print_remote_upgrade_prompt(
        local_version,
        remote_version,
        compatible,
        release_notes_url,
        "[y] 继续使用当前版本初始化    [n] 退出，升级后重试"
        if prompt_mode == "init"
        else "[y] 跳过 (24小时内不再提醒)    [n] 退出升级",
    )


    if prompt_mode == "init":
        choice = input("请选择 (y/n): ").strip().lower()
        if choice == "y":
            _record_skip(skip_key)
            print("\n⏩ 继续使用当前版本初始化...\n")
            return False
        print("\n⏩ 已取消初始化，请升级 VAF 后重试\n")
        return True

    choice = input("请选择 (y/n): ").strip().lower()
    if choice == "y":
        _record_skip(skip_key)
        print("\n⏩ 继续使用当前版本...\n")
        return False

    return True


def _check_workspace_update(ws_type: str, vaf_path: Optional[Path]) -> bool:
    current_version = _get_workspace_version()
    if not current_version:
        return False

    if ws_type == "hub":
        if not vaf_path:
            return False
        target_version = _get_local_vaf_version(vaf_path)
        if not target_version:
            return False
        if not _is_version_behind(current_version, target_version):
            print(f"✅ 需求池 .vibe 版本 ({current_version}) 已是最新")
            return False

        skip_key = SKIP_KEYS["hub_vaf"]
        if _is_skip_active(skip_key):
            return False

        _print_workspace_upgrade_prompt(
            title="⚠️ 检测到需求池 .vibe 版本落后于 VAF",
            current_label="当前版本",
            current_version=current_version,
            target_label="VAF 版本",
            target_version=target_version,
            hint="📝 建议升级 .vibe 资产以获得最新功能",
            option_line="[y] 立即升级    [n] 跳过 (24小时内不再提醒)",
        )
        choice = input("请选择 (y/n, 默认y): ").strip().lower()
        if choice != "n":
            if _run_workspace_upgrade("hub"):
                print("✅ 升级完成，请重新启动 @vaf_starter。")
                return True
            print("⏩ 升级失败，继续使用当前版本...\n")
            return False

        _record_skip(skip_key)
        print("\n⏩ 继续使用当前版本...\n")
        return False

    if ws_type == "service":
        target_version, hub_path = _get_latest_hub_info_for_service()
        if not target_version or not hub_path:
            print("ℹ️  未找到关联的需求池，跳过版本检测")
            return False
        if not _is_version_behind(current_version, target_version):
            print(f"✅ 服务 .vibe 版本 ({current_version}) 已与需求池同步")
            return False

        skip_key = SKIP_KEYS["service_hub"]
        if _is_skip_active(skip_key):
            return False

        _print_workspace_upgrade_prompt(
            title="⚠️ 检测到服务 .vibe 版本落后于需求池",
            current_label="当前版本",
            current_version=current_version,
            target_label="Hub 版本",
            target_version=target_version,
            hint="📝 建议升级 .vibe 资产以与需求池保持一致",
            option_line="[y] 立即升级    [n] 跳过 (24小时内不再提醒)",
        )
        choice = input("请选择 (y/n, 默认y): ").strip().lower()
        if choice != "n":
            if _run_workspace_upgrade("service", hub_path):
                print("✅ 升级完成，请重新启动 @vaf_starter。")
                return True
            print("⏩ 升级失败，继续使用当前版本...\n")
            return False

        _record_skip(skip_key)
        print("\n⏩ 继续使用当前版本...\n")
        return False

    return False


def check_vaf_remote_update_for_init(vaf_path: Optional[Path] = None) -> bool:
    """
    Hub 初始化时的远程版本检测

    Returns:
        True: 用户选择退出升级
        False: 继续初始化
    """
    resolved_vaf_path = vaf_path or _get_vaf_source_path()
    return _check_vaf_remote_update(resolved_vaf_path, prompt_mode="init")


def check_for_upgrade() -> int:
    """日常使用场景入口（vaf_starter Step 2 调用）
    
    仅检测工作区版本，不检测本地 VAF vs 远程 VAF
    """
    try:
        ws_type = get_workspace_type()
    except Exception:
        print("⚠️ 未检测到工作区配置，跳过版本检测")
        return 0

    vaf_path = _get_vaf_source_path()

    # 仅检测工作区版本（.vibe vs VAF/Hub）
    if _check_workspace_update(ws_type, vaf_path):
        return 2

    return 0


def main():
    sys.exit(check_for_upgrade())


if __name__ == "__main__":
    main()
