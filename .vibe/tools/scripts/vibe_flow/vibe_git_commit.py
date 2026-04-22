#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
阶段 Git 提交脚本

用于 P/S/T 阶段执行完成后的 Git 提交推送。
保留现有的交互确认逻辑，将 Git 操作封装到脚本中。

使用方式：
    # 检查变更（返回 JSON）
    python vibe_git_commit.py --mode check --feature <feature>
    
    # 执行提交（需要传入 commit message）
    python vibe_git_commit.py --mode commit --feature <feature> --phase <phase> --commit-msg "<msg>"

注意：S/T 阶段的 Hub 同步已在 vibe_sync.py 状态上报时自动完成，无需重复提交。

输出格式：JSON
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
from typing import Optional, Tuple


def run_git(repo_path: Path, git_args: list) -> Tuple[bool, str]:
    """执行 Git 命令"""
    cmd = ["git", "-C", str(repo_path)] + git_args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode == 0, output
    except Exception as e:
        return False, str(e)


def check_git_repo(repo_path: Path) -> dict:
    """检查是否是 Git 仓库"""
    success, _ = run_git(repo_path, ["rev-parse", "--git-dir"])
    if not success:
        return {
            "is_git_repo": False,
            "git_root": None,
            "current_branch": None
        }
    
    success, git_root = run_git(repo_path, ["rev-parse", "--show-toplevel"])
    success2, branch = run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    
    return {
        "is_git_repo": True,
        "git_root": git_root if success else str(repo_path),
        "current_branch": branch if success2 else "unknown"
    }


def get_changed_files(repo_path: Path) -> list[str]:
    """获取变更文件列表"""
    success, output = run_git(repo_path, ["status", "--porcelain"])
    if not success or not output.strip():
        return []
    
    files = []
    for line in output.strip().split("\n"):
        if line.strip():
            # 格式: XY filename 或 XY "filename with spaces"
            parts = line.strip().split(maxsplit=1)
            if len(parts) >= 2:
                files.append(parts[1].strip('"'))
    return files


def cmd_check(args) -> int:
    """检查模式：返回 Git 状态和变更文件"""
    repo_path = Path.cwd()
    
    # 检查 Git 仓库
    git_info = check_git_repo(repo_path)
    if not git_info["is_git_repo"]:
        print(json.dumps({
            "success": True,
            "is_git_repo": False,
            "message": "当前目录不是 Git 仓库"
        }, ensure_ascii=False, indent=2))
        return 0
    
    # 获取变更文件
    changed_files = get_changed_files(repo_path)
    
    # 检查是否有 origin 远程
    success, _ = run_git(repo_path, ["remote", "get-url", "origin"])
    has_remote = success
    
    print(json.dumps({
        "success": True,
        "is_git_repo": True,
        "git_root": git_info["git_root"],
        "current_branch": git_info["current_branch"],
        "has_remote": has_remote,
        "changed_files": changed_files,
        "changed_count": len(changed_files)
    }, ensure_ascii=False, indent=2))
    return 0


def execute_git_commit(repo_path: Path, commit_msg: str, files_to_add: Optional[list] = None) -> dict:
    """
    执行 Git 提交流程（统一使用 git add . 添加所有变更）
    
    Args:
        repo_path: 仓库路径
        commit_msg: 提交信息
        files_to_add: 保留参数（兼容性），实际执行时统一使用 git add .
    
    Returns:
        执行结果字典
    """
    steps = []
    
    # 获取当前分支
    success, branch = run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if not success:
        return {
            "success": False,
            "message": "无法获取当前分支",
            "steps": steps
        }
    
    # Step 1: git add .（统一添加所有未提交的变更，忽略 files_to_add 参数）
    steps.append({"action": "git add .", "status": "running"})
    run_git(repo_path, ["add", "."])
    steps[-1]["status"] = "success"
    
    # Step 2: git commit
    steps.append({"action": "git commit", "status": "running"})
    success, output = run_git(repo_path, ["commit", "-m", commit_msg])
    if not success:
        if "nothing to commit" in output.lower():
            steps[-1]["status"] = "skipped"
            steps[-1]["note"] = "无变更需要提交"
            return {
                "success": True,
                "message": "无变更需要提交",
                "steps": steps,
                "skipped": True
            }
        steps[-1]["status"] = "failed"
        steps[-1]["error"] = output
        return {
            "success": False,
            "message": f"Git commit 失败: {output}",
            "steps": steps
        }
    steps[-1]["status"] = "success"
    
    # Step 3: git pull --rebase
    steps.append({"action": "git pull --rebase", "status": "running"})
    success, output = run_git(repo_path, ["pull", "--rebase", "origin", branch])
    if not success:
        if "CONFLICT" in output or "conflict" in output.lower():
            steps[-1]["status"] = "failed"
            steps[-1]["error"] = "存在冲突，请手动解决"
            return {
                "success": False,
                "message": "Git rebase 存在冲突",
                "steps": steps,
                "manual_command": f"cd {repo_path} && git status && git rebase --abort"
            }
        # 可能是新分支，远程不存在，继续执行
        steps[-1]["status"] = "skipped"
        steps[-1]["note"] = "远程分支不存在（新分支）"
    else:
        steps[-1]["status"] = "success"
    
    # Step 4: git push
    steps.append({"action": "git push", "status": "running"})
    success, output = run_git(repo_path, ["push", "origin", branch])
    if not success:
        steps[-1]["status"] = "failed"
        steps[-1]["error"] = output
        return {
            "success": False,
            "message": f"Git push 失败: {output}",
            "steps": steps,
            "manual_command": f"git push origin {branch}"
        }
    steps[-1]["status"] = "success"
    
    return {
        "success": True,
        "message": f"已提交并推送到 {branch}",
        "steps": steps,
        "branch": branch
    }


def cmd_commit(args) -> int:
    """
    提交模式：执行 Git 提交
    
    注意：
    - P 阶段：提交 Hub 仓库
    - S/T 阶段：只提交 Service 仓库（Hub 已在 vibe_sync.py 状态上报时自动提交）
    """
    repo_path = Path.cwd()
    commit_msg = args.commit_msg
    
    # 检查 Git 仓库
    git_info = check_git_repo(repo_path)
    if not git_info["is_git_repo"]:
        print(json.dumps({
            "success": False,
            "message": "当前目录不是 Git 仓库"
        }, ensure_ascii=False, indent=2))
        return 1
    
    # 执行当前仓库提交
    result = execute_git_commit(repo_path, commit_msg)
    result["repo"] = "current"
    result["path"] = str(repo_path)
    
    # 输出结果
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1


def main():
    parser = argparse.ArgumentParser(
        description="阶段 Git 提交脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "--mode", "-m",
        choices=["check", "commit"],
        required=True,
        help="执行模式: check=检查变更, commit=执行提交"
    )
    
    parser.add_argument(
        "--feature", "-f",
        help="需求名称"
    )
    
    parser.add_argument(
        "--phase", "-p",
        help="阶段 ID (如 P01, S01)"
    )
    
    parser.add_argument(
        "--commit-msg",
        help="提交信息"
    )
    
    args = parser.parse_args()
    
    if args.mode == "check":
        return cmd_check(args)
    elif args.mode == "commit":
        if not args.commit_msg:
            print(json.dumps({
                "success": False,
                "message": "commit 模式需要 --commit-msg 参数"
            }, ensure_ascii=False, indent=2))
            return 1
        return cmd_commit(args)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
