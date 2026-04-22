#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
代码变更验证脚本 - S03 硬门禁

功能：
- 读取 Task_<ID>_Log.md 中的变更记录表格
- 执行 git diff --name-only 和 git status 获取实际变更
- 对比声明 vs 实际，输出验证结果
- 返回 exit code（0=通过，1=失败）

使用方式：
    python verify_code_changes.py --task-log <path_to_log.md>
    python verify_code_changes.py --task-log docs/feature/S03_implementation/Task_T002_Log.md
"""

import sys
import io

# 跨平台 Unicode 输出支持 (Windows GBK 兼容)
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import re
import argparse
import subprocess
from pathlib import Path
from typing import List, Dict, Tuple, Optional


def find_project_root() -> Path:
    """向上查找 Git 仓库根目录"""
    current = Path.cwd()
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    return Path.cwd()


def parse_change_log_table(content: str) -> List[Dict[str, str]]:
    """解析变更记录表格
    
    支持格式：
    | 文件路径 | 变更类型 (Add/Mod/Del) | 简要说明 |
    | :--- | :--- | :--- |
    | `src/main/java/com/xxx/OrderService.java` | Mod | 增加 createOrder 方法 |
    
    Returns:
        List[Dict]: [{"path": "xxx.java", "type": "Mod", "desc": "xxx"}, ...]
    """
    changes = []
    
    # 查找「变更记录」或「Change Log」部分
    section_pattern = r'##\s*\d*\.?\s*(变更记录|Change\s*Log)'
    section_match = re.search(section_pattern, content, re.IGNORECASE)
    
    if not section_match:
        return changes
    
    # 从该部分开始解析表格
    section_start = section_match.end()
    section_content = content[section_start:]
    
    # 查找下一个 ## 标题作为结束
    next_section = re.search(r'\n##\s', section_content)
    if next_section:
        section_content = section_content[:next_section.start()]
    
    # 解析表格行
    # 匹配格式: | `path` | Type | desc |
    table_row_pattern = r'\|\s*`?([^`|]+)`?\s*\|\s*(Add|Mod|Del)\s*\|\s*([^|]*)\|'
    
    for match in re.finditer(table_row_pattern, section_content, re.IGNORECASE):
        path = match.group(1).strip()
        change_type = match.group(2).strip().capitalize()
        desc = match.group(3).strip()
        
        # 跳过表头示例行
        if 'xxx' in path.lower() or 'example' in path.lower():
            continue
        
        changes.append({
            "path": path,
            "type": change_type,
            "desc": desc
        })
    
    return changes


def get_git_diff_files() -> List[str]:
    """获取 git diff 中的变更文件列表（已暂存 + 未暂存）"""
    try:
        # 已暂存的变更
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        # 未暂存的变更
        unstaged = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        
        files = set()
        if staged.returncode == 0:
            files.update(staged.stdout.strip().split('\n'))
        if unstaged.returncode == 0:
            files.update(unstaged.stdout.strip().split('\n'))
        
        # 过滤空字符串
        return [f for f in files if f]
    except Exception as e:
        print(f"⚠️ 获取 git diff 失败: {e}")
        return []


def get_git_status() -> Dict[str, str]:
    """获取 git status 中的文件状态
    
    Returns:
        Dict[str, str]: {"path": "status"} 
        status: ?? (untracked), A (added), M (modified), D (deleted)
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, encoding='utf-8', errors='replace'
        )
        
        status_map = {}
        if result.returncode == 0:
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                # 格式: XY path 或 XY "path with spaces"
                status_code = line[:2].strip()
                file_path = line[3:].strip().strip('"')
                status_map[file_path] = status_code
        
        return status_map
    except Exception as e:
        print(f"⚠️ 获取 git status 失败: {e}")
        return {}


def normalize_path(path: str) -> str:
    """标准化路径，提取文件名用于模糊匹配"""
    return Path(path).name


def verify_changes(declared: List[Dict[str, str]], diff_files: List[str], status_map: Dict[str, str]) -> Tuple[bool, List[str], List[str]]:
    """验证声明的变更与实际变更是否一致
    
    Args:
        declared: 声明的变更列表
        diff_files: git diff 中的文件列表
        status_map: git status 中的文件状态
    
    Returns:
        Tuple[bool, List[str], List[str]]: (是否通过, 成功列表, 失败列表)
    """
    passed = []
    failed = []
    
    # 构建文件名到完整路径的映射（用于模糊匹配）
    diff_names = {normalize_path(f): f for f in diff_files}
    status_names = {normalize_path(f): f for f in status_map.keys()}
    
    for change in declared:
        path = change["path"]
        change_type = change["type"]
        file_name = normalize_path(path)
        
        if change_type == "Mod":
            # 修改：必须在 git diff 中
            if path in diff_files or file_name in diff_names:
                passed.append(f"[Mod] {path} ✅")
            else:
                failed.append(f"[Mod] {path} ❌ 未出现在 git diff 中")
        
        elif change_type == "Add":
            # 新增：必须在 git status 中显示为 ?? 或 A
            matched_path = path if path in status_map else status_names.get(file_name)
            if matched_path:
                status = status_map.get(matched_path, status_map.get(path, ""))
                if status in ["??", "A", "AM", "A "]:
                    passed.append(f"[Add] {path} ✅")
                else:
                    failed.append(f"[Add] {path} ❌ 状态为 '{status}'，期望 '??' 或 'A'")
            else:
                failed.append(f"[Add] {path} ❌ 未出现在 git status 中")
        
        elif change_type == "Del":
            # 删除：必须在 git status 中显示为 D
            matched_path = path if path in status_map else status_names.get(file_name)
            if matched_path:
                status = status_map.get(matched_path, status_map.get(path, ""))
                if "D" in status:
                    passed.append(f"[Del] {path} ✅")
                else:
                    failed.append(f"[Del] {path} ❌ 状态为 '{status}'，期望 'D'")
            else:
                failed.append(f"[Del] {path} ❌ 未出现在 git status 中")
    
    return len(failed) == 0, passed, failed


def main():
    parser = argparse.ArgumentParser(
        description="代码变更验证脚本 - S03 硬门禁"
    )
    parser.add_argument(
        "--task-log",
        required=True,
        help="Task_<ID>_Log.md 文件路径"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细信息"
    )
    
    args = parser.parse_args()
    
    # 读取 Task Log 文件
    log_path = Path(args.task_log)
    if not log_path.exists():
        print(f"❌ 文件不存在: {log_path}")
        sys.exit(1)
    
    with open(log_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 解析声明的变更
    declared_changes = parse_change_log_table(content)
    
    if not declared_changes:
        print("⚠️ 未在 Task Log 中找到变更记录表格")
        print("   请确保文件包含「## 变更记录」或「## Change Log」部分")
        sys.exit(1)
    
    # 获取实际变更
    diff_files = get_git_diff_files()
    status_map = get_git_status()
    
    # 验证
    success, passed, failed = verify_changes(declared_changes, diff_files, status_map)
    
    # 输出结果
    print()
    print("=" * 60)
    print("📋 代码变更验证")
    print("=" * 60)
    print()
    
    print(f"声明变更文件（来自 {log_path.name}）：")
    for change in declared_changes:
        print(f"  - [{change['type']}] {change['path']}")
    print()
    
    if args.verbose:
        print("实际变更文件（来自 git）：")
        all_files = set(diff_files) | set(status_map.keys())
        for f in sorted(all_files):
            status = status_map.get(f, "M")
            print(f"  - [{status}] {f}")
        print()
    
    print("验证结果：")
    for item in passed:
        print(f"  {item}")
    for item in failed:
        print(f"  {item}")
    print()
    
    if success:
        print("✅ 验证通过：所有声明的变更文件均已实际修改")
        print("=" * 60)
        sys.exit(0)
    else:
        print("❌ 验证失败：以下文件声明了变更但未实际修改")
        for item in failed:
            print(f"   {item}")
        print()
        print("👉 请实际修改这些文件后再继续")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
