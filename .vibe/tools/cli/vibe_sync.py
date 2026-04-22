#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Vibe Sync CLI - 状态同步命令行工具

封装 vibe_update_status.py 和 vibe_unlock_checker.py，提供简化的 CLI 接口。

使用方式：
    # S 阶段完成后上报状态
    python vibe_sync.py report S01 --feature <feature_name>
    
    # 检查门禁
    python vibe_sync.py gate S02
    
    # 拉取最新状态
    python vibe_sync.py pull
    
    # 查看状态汇总
    python vibe_sync.py status
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
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

# 导入 vibe_git_commit 的 Git 操作函数
try:
    # 尝试从相对路径导入
    scripts_dir = Path(__file__).parent.parent / "scripts" / "vibe_flow"
    if scripts_dir.exists():
        sys.path.insert(0, str(scripts_dir))
        from vibe_git_commit import execute_git_commit, run_git
        HAS_GIT_COMMIT_MODULE = True
    else:
        HAS_GIT_COMMIT_MODULE = False
except ImportError:
    HAS_GIT_COMMIT_MODULE = False

# 导入埋点上报模块
try:
    from vaf_telemetry import safe_report_telemetry
    HAS_TELEMETRY_MODULE = True
except ImportError:
    HAS_TELEMETRY_MODULE = False


def get_feature_dir_name(branch_name: str) -> str:
    """获取 feature 目录名（仅将 / 转换为 _）
    
    分支名 feature/xxx -> 目录名 feature_xxx
    """
    return branch_name.replace("/", "_")


# ============ 路径解析 ============

def find_project_root() -> Path:
    """向上查找包含 .vibe 目录的项目根目录"""
    current = Path.cwd()
    while current != current.parent:
        if (current / ".vibe").exists():
            return current
        current = current.parent
    return Path.cwd()


def load_json_file(path: str) -> dict:
    """加载 JSON 文件"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_workspace_type() -> str:
    """获取工作区类型: hub | service"""
    root = find_project_root()
    workspace_file = root / ".vibe" / "workspace.json"
    if workspace_file.exists():
        data = load_json_file(str(workspace_file))
        return data.get("type", "unknown")
    return "unknown"


def find_feature_dir(feature_name: Optional[str] = None) -> Optional[Path]:
    """查找需求目录
    
    Args:
        feature_name: 指定需求名称，若为 None 则返回第一个找到的需求目录
    """
    root = find_project_root()
    ws_type = get_workspace_type()
    
    if ws_type == "hub":
        # Hub: features/<name>/docs/
        features_dir = root / "features"
        if features_dir.exists():
            if feature_name:
                # 指定了需求名称，直接查找
                target = features_dir / feature_name / "docs"
                if target.exists():
                    return target
                return None
            else:
                # 未指定，返回第一个找到的（向后兼容）
                for feature in features_dir.iterdir():
                    if feature.is_dir():
                        docs = feature / "docs"
                        if docs.exists():
                            return docs
    elif ws_type == "service":
        # Service: docs/features/<name>/
        docs_features = root / "docs" / "features"
        if docs_features.exists():
            if feature_name:
                target = docs_features / feature_name
                if target.exists():
                    return target
                return None
            else:
                for feature in docs_features.iterdir():
                    if feature.is_dir():
                        return feature
    
    return None


def get_manifest_local(feature_name: Optional[str] = None) -> dict:
    """获取 manifest_local.json 配置"""
    feature_dir = find_feature_dir(feature_name)
    if feature_dir:
        manifest = feature_dir / "manifest_local.json"
        if manifest.exists():
            return load_json_file(str(manifest))
    return {}


def get_status_file_path(feature_name: Optional[str] = None) -> Optional[Path]:
    """获取状态文件路径
    
    Args:
        feature_name: 指定需求名称
    """
    feature_dir = find_feature_dir(feature_name)
    ws_type = get_workspace_type()
    
    if not feature_dir:
        return None
    
    if ws_type == "hub":
        return feature_dir / "hub_status.json"
    elif ws_type == "service":
        return feature_dir / "phase_status.json"
    
    return None


def get_scripts_dir() -> Path:
    """获取脚本目录"""
    # 尝试多种路径
    candidates = [
        Path(__file__).parent.parent / "scripts" / "vibe_flow",
        find_project_root() / "tools" / "scripts" / "vibe_flow",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


# ============ 命令实现 ============

def validate_phase_workspace(phase: str, ws_type: str) -> Tuple[bool, str]:
    """校验阶段与工作区类型是否匹配"""
    # P 阶段只能在 Hub 执行
    p_phases = ["P01", "P02", "P03", "P04", "P05", "P06-A", "P06-B", "P06-C"]
    # S 阶段只能在 Service 执行
    s_phases = ["S01", "S02", "S03", "S04", "S05", "S06", "S07", "T01"]
    
    phase_upper = phase.upper()
    
    if phase_upper in p_phases:
        if ws_type != "hub":
            return False, f"❌ {phase} 是 P 阶段，只能在 Hub 工作区执行，当前工作区类型: {ws_type}"
    elif phase_upper in s_phases:
        if ws_type != "service":
            return False, f"❌ {phase} 是 S 阶段，只能在 Service 工作区执行，当前工作区类型: {ws_type}"
    
    return True, ""


def cmd_report(args):
    """上报状态"""
    stage = args.stage
    action = args.action or "approve"
    feature_name = getattr(args, 'feature', None)
    token_usage = getattr(args, 'token_usage', None)
    turns = getattr(args, 'turns', None)
    model = getattr(args, 'model', None)
    skip_validate = getattr(args, 'skip_validate', False)
    
    ws_type = get_workspace_type()
    
    # 校验阶段与工作区类型是否匹配
    valid, error_msg = validate_phase_workspace(stage, ws_type)
    if not valid:
        print(json.dumps({
            "success": False,
            "message": error_msg,
            "phase": stage,
            "workspace_type": ws_type
        }, ensure_ascii=False, indent=2))
        return 1
    
    # pending 动作时，先执行产出校验
    if action == "pending" and not skip_validate:
        validate_script = get_scripts_dir() / "vibe_validate_output.py"
        if validate_script.exists():
            validate_cmd = [
                sys.executable, str(validate_script),
                "--phase", stage,
                "--feature", feature_name or "",
                "--json"
            ]
            validate_result = subprocess.run(validate_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
            
            if validate_result.returncode != 0:
                # 校验失败，输出校验结果
                try:
                    validate_data = json.loads(validate_result.stdout)
                    print(json.dumps({
                        "success": False,
                        "message": "产出校验失败",
                        "phase": stage,
                        "validation": validate_data
                    }, ensure_ascii=False, indent=2))
                except json.JSONDecodeError:
                    print(json.dumps({
                        "success": False,
                        "message": "产出校验失败",
                        "phase": stage,
                        "validation_output": validate_result.stdout
                    }, ensure_ascii=False, indent=2))
                return 1
            else:
                # 校验通过，输出校验结果供 AI 展示
                try:
                    validate_data = json.loads(validate_result.stdout)
                    print(f"\n{validate_data.get('render_template', '✅ 产出校验通过')}\n")
                except json.JSONDecodeError:
                    print(f"\n✅ 产出校验通过\n")
    
    status_file = get_status_file_path(feature_name)
    
    if not status_file:
        print(json.dumps({
            "success": False,
            "message": "无法找到状态文件，请确认当前目录是否正确"
        }, ensure_ascii=False, indent=2))
        return 1
    
    scripts_dir = get_scripts_dir()
    update_script = scripts_dir / "vibe_update_status.py"
    
    # 构建命令（添加 --_internal 标记，表示由 vibe_sync.py 调用）
    cmd = [
        sys.executable, str(update_script),
        "--_internal",  # 内部调用标记，绕过直接调用检测
        "--action", action,
        "--phase", stage,
        "--status", str(status_file)
    ]
    
    # 放行时记录 token 消耗（已废弃）
    if action == "approve" and token_usage is not None:
        cmd.extend(["--token-usage", str(token_usage)])
    
    # 放行时记录对话轮次
    if action == "approve" and turns is not None:
        cmd.extend(["--turns", str(turns)])
    
    # 放行时记录使用的 AI 模型
    if action == "approve" and model is not None:
        cmd.extend(["--model", model])
    
    # 执行状态更新
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    
    # approve 动作成功后，执行埋点上报和提交当前仓库
    if action == "approve" and result.returncode == 0:
        # 埋点上报
        if HAS_TELEMETRY_MODULE:
            try:
                safe_report_telemetry(stage, feature_name, turns=turns, model=model)
                print(f"📊 埋点已上报" + (f"（模型: {model}）" if model else ""))
            except Exception as e:
                print(f"⚠️ 埋点上报失败（不影响主流程）: {e}")
        
        print(f"\n🔄 自动提交当前仓库...")
        
        # 构建提交信息
        commit_msg = f"feat({stage}): complete {stage}"
        if feature_name:
            commit_msg += f" for {feature_name}"
        
        if HAS_GIT_COMMIT_MODULE:
            # 使用统一的 Git 提交流程
            current_git_result = execute_git_commit(Path.cwd(), commit_msg)
            
            if current_git_result.get("success"):
                if current_git_result.get("skipped"):
                    print("ℹ️  当前仓库无变更需要提交")
                else:
                    print(f"✅ 当前仓库已提交并推送 ({current_git_result.get('branch', 'main')})")
                    # 输出执行步骤
                    for step in current_git_result.get("steps", []):
                        status_icon = "✅" if step["status"] == "success" else "⏭️" if step["status"] == "skipped" else "❌"
                        print(f"   {status_icon} {step['action']}")
            else:
                print(f"⚠️  当前仓库 Git 提交失败: {current_git_result.get('message')}")
                if current_git_result.get("manual_command"):
                    print(f"   请手动执行: {current_git_result['manual_command']}")
        else:
            # 回退方案：提示手动执行
            print("⚠️  未找到 vibe_git_commit 模块，请手动提交：")
            print(f"   python .vibe/tools/scripts/vibe_flow/vibe_git_commit.py --mode commit --phase {stage} --feature {feature_name or ''} --commit-msg \"{commit_msg}\"")
    
    # 如果是 Service 端，所有状态变更都同步到 Hub
    # 统一收口：vibe_sync.py 是唯一入口，所有状态都同步
    if ws_type == "service" and result.returncode == 0:
        manifest = get_manifest_local(feature_name)
        hub_path = manifest.get("hub_path")
        feature = manifest.get("feature")
        service = manifest.get("service")
        
        if hub_path and feature and service:
            # 获取 Service 仓库（当前目录）的分支，Hub 应使用同名分支
            branch_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "main"
            
            print(f"\n📥 同步到 Hub ({current_branch})...")
            
            # 同步状态
            # 注意：feature 可能是分支名 (feature/xxx)，需要转换为目录名 (feature_xxx)
            feature_dir_name = get_feature_dir_name(feature)
            hub_service_dir = Path(hub_path) / "features" / feature_dir_name / "docs" / ".service_status"
            result2 = subprocess.run(
                [sys.executable, str(update_script),
                 "--sync",
                 "--service-status", str(status_file),
                 "--hub-service-dir", str(hub_service_dir),
                 "--service-name", service],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            print(result2.stdout)
            
            # Git 提交推送（复用 vibe_git_commit.py 的 add → commit → rebase → push 流程）
            commit_msg = f"sync: {service} {stage} {action}"
            
            if HAS_GIT_COMMIT_MODULE:
                # 使用统一的 Git 提交流程
                print(f"\n� 提交 Hub 变更...")
                git_result = execute_git_commit(Path(hub_path), commit_msg)
                
                if git_result.get("success"):
                    if git_result.get("skipped"):
                        print("ℹ️ Hub 无变更可提交")
                    else:
                        print(f"✅ 已推送到 Hub 远端 ({git_result.get('branch', current_branch)})")
                        # 输出执行步骤
                        for step in git_result.get("steps", []):
                            status_icon = "✅" if step["status"] == "success" else "⏭️" if step["status"] == "skipped" else "❌"
                            print(f"   {status_icon} {step['action']}")
                else:
                    print(f"⚠️ Hub Git 提交失败: {git_result.get('message')}")
                    if git_result.get("manual_command"):
                        print(f"   请手动执行: {git_result['manual_command']}")
                    # 输出失败步骤
                    for step in git_result.get("steps", []):
                        if step["status"] == "failed":
                            print(f"   ❌ {step['action']}: {step.get('error', '')}")
            else:
                # 回退方案：直接执行 Git 命令（add → commit → rebase → push）
                hub_docs_dir = Path(hub_path) / "features" / feature_dir_name / "docs"
                
                # Step 1: git add
                add_result = subprocess.run(
                    ["git", "-C", hub_path, "add", str(hub_docs_dir)],
                    capture_output=True, text=True, encoding='utf-8', errors='replace'
                )
                if add_result.returncode != 0:
                    print(f"⚠️ git add 失败: {add_result.stderr}")
                    return 1
                print("   ✅ git add")
                
                # Step 2: git commit
                commit_result = subprocess.run(
                    ["git", "-C", hub_path, "commit", "-m", commit_msg],
                    capture_output=True, text=True, encoding='utf-8', errors='replace'
                )
                commit_out = (commit_result.stdout or "") + (commit_result.stderr or "")
                if commit_result.returncode != 0:
                    if "nothing to commit" in commit_out.lower():
                        print("ℹ️ Hub 无变更可提交")
                        return 0
                    print(f"⚠️ git commit 失败: {commit_result.stderr}")
                    return 1
                print("   ✅ git commit")
                
                # Step 3: git pull --rebase
                rebase_result = subprocess.run(
                    ["git", "-C", hub_path, "pull", "--rebase", "origin", current_branch],
                    capture_output=True, text=True, encoding='utf-8', errors='replace'
                )
                if rebase_result.returncode != 0:
                    rebase_out = (rebase_result.stdout or "") + (rebase_result.stderr or "")
                    if "CONFLICT" in rebase_out:
                        print(f"❌ Hub rebase 冲突，请手动解决: cd {hub_path} && git status")
                    else:
                        # 可能是新分支，远程不存在，继续执行
                        print("   ⏭️ git pull --rebase (远程分支不存在)")
                else:
                    print("   ✅ git pull --rebase")
                
                # Step 4: git push
                push_result = subprocess.run(
                    ["git", "-C", hub_path, "push", "origin", current_branch],
                    capture_output=True, text=True, encoding='utf-8', errors='replace'
                )
                if push_result.returncode == 0:
                    print(f"✅ 已推送到 Hub 远端 ({current_branch})")
                else:
                    print(f"⚠️ git push 失败: {push_result.stderr}")
                    print(f"   请手动执行: git -C {hub_path} push origin {current_branch}")
        else:
            print("\n⚠️ 未配置 manifest_local.json，跳过 Hub 同步")
    
    return result.returncode


def cmd_gate(args):
    """检查门禁"""
    stage = args.stage
    feature_name = getattr(args, 'feature', None)
    
    ws_type = get_workspace_type()
    status_file = get_status_file_path(feature_name)
    
    if not status_file:
        print(json.dumps({
            "success": False,
            "message": "无法找到状态文件"
        }, ensure_ascii=False, indent=2))
        return 1
    
    scripts_dir = get_scripts_dir()
    checker_script = scripts_dir / "vibe_unlock_checker.py"
    
    cmd = [
        sys.executable, str(checker_script),
        "--feature", feature_name or "",
        "--phase", stage,
        "--format", "text"
    ]
    # 工作区类型和 P06 门禁状态由 vibe_unlock_checker.py 内部自动读取
    
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(result.stdout)
    
    return result.returncode


def cmd_pull(args):
    """拉取最新状态"""
    feature_name = getattr(args, 'feature', None)
    ws_type = get_workspace_type()
    
    # 获取当前分支
    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace'
    )
    current_branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "main"
    
    if ws_type == "hub":
        # Hub 端直接 git pull
        result = subprocess.run(
            ["git", "pull", "origin", current_branch],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        print(result.stdout)
        print(f"✅ Hub 状态已更新 ({current_branch})")
    elif ws_type == "service":
        # Service 端需要拉取 Hub
        manifest = get_manifest_local(feature_name)
        hub_path = manifest.get("hub_path")
        
        if hub_path:
            result = subprocess.run(
                ["git", "-C", hub_path, "pull", "origin", current_branch],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            print(result.stdout)
            print(f"✅ 已从 Hub ({hub_path}) 拉取最新状态 ({current_branch})")
        else:
            print("❌ 未配置 hub_path，无法拉取")
            return 1
    else:
        print("❌ 无法识别工作区类型")
        return 1
    
    return 0


def cmd_status(args):
    """查看状态汇总"""
    feature_name = getattr(args, 'feature', None)
    ws_type = get_workspace_type()
    status_file = get_status_file_path(feature_name)
    
    if not status_file or not status_file.exists():
        print("❌ 状态文件不存在")
        return 1
    
    status = load_json_file(str(status_file))
    phases = status.get("phases", {})
    
    print(f"📊 状态汇总 ({ws_type} 模式)")
    print("=" * 50)
    
    for phase, info in phases.items():
        phase_status = info.get("status", "not_started")
        icon = {
            "not_started": "⬜",
            "in_progress": "🔵",
            "pending_review": "🟡",
            "approved": "✅"
        }.get(phase_status, "❓")
        
        print(f"  {icon} {phase}: {phase_status}")
    
    print("=" * 50)
    return 0


# ============ 主函数 ============

def main():
    parser = argparse.ArgumentParser(
        description="Vibe Sync CLI - 状态同步工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # S01 完成后上报
  python vibe_sync.py report S01 --feature <feature_name>
  
  # 检查 S02 门禁
  python vibe_sync.py gate S02
  
  # 拉取最新状态
  python vibe_sync.py pull
  
  # 查看状态
  python vibe_sync.py status
"""
    )
    
    subparsers = parser.add_subparsers(dest="command", help="子命令")
    
    # report 子命令
    report_parser = subparsers.add_parser("report", help="上报阶段状态")
    report_parser.add_argument("stage", help="阶段 ID（如 S01, P02）")
    report_parser.add_argument("--action", "-a", default="approve",
                               choices=["start", "pending", "approve"],
                               help="操作类型（默认: approve）")
    report_parser.add_argument("--feature", "-f",
                               help="指定需求名称")
    report_parser.add_argument("--token-usage", "-t", type=int,
                               help="本阶段预估 token 消耗（仅放行时记录，已废弃）")
    report_parser.add_argument("--turns", type=int,
                               help="本阶段对话轮次（仅放行时记录）")
    report_parser.add_argument("--model", type=str,
                               help="AI 模型名称（仅放行时记录）")
    report_parser.add_argument("--skip-validate", action="store_true",
                               help="跳过产出校验（pending 动作时）")
    
    # gate 子命令
    gate_parser = subparsers.add_parser("gate", help="检查门禁")
    gate_parser.add_argument("stage", help="阶段 ID（如 S02）")
    gate_parser.add_argument("--feature", "-f",
                             help="指定需求名称")
    
    # pull 子命令
    subparsers.add_parser("pull", help="拉取最新状态")
    
    # status 子命令
    status_parser = subparsers.add_parser("status", help="查看状态汇总")
    status_parser.add_argument("--feature", "-f",
                               help="指定需求名称")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    if args.command == "report":
        return cmd_report(args)
    elif args.command == "gate":
        return cmd_gate(args)
    elif args.command == "pull":
        return cmd_pull(args)
    elif args.command == "status":
        return cmd_status(args)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
