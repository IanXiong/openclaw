#!/usr/bin/env python3
from __future__ import annotations
"""
清理项目缓存文件

清理目标：
- __pycache__ 目录
- .cache 目录
- *.pyc 文件
- .pytest_cache 目录

使用方法：
    python tools/clean_cache.py
    
    # 预览模式（不实际删除）
    python tools/clean_cache.py --dry-run
"""

import sys

# Windows 编码兼容：确保 stdout/stderr 使用 UTF-8，避免 emoji 输出时 GBK 编码错误
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import shutil
import argparse
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 需要清理的目录名
CACHE_DIRS = [
    "__pycache__",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
]

# 需要清理的文件扩展名
CACHE_FILES = [
    "*.pyc",
    "*.pyo",
]


def find_cache_items(root: Path) -> tuple[list[Path], list[Path]]:
    """
    查找所有缓存目录和文件
    
    Returns:
        (缓存目录列表, 缓存文件列表)
    """
    cache_dirs = []
    cache_files = []
    
    # 查找缓存目录
    for dir_name in CACHE_DIRS:
        for item in root.rglob(dir_name):
            if item.is_dir():
                cache_dirs.append(item)
    
    # 查找缓存文件
    for pattern in CACHE_FILES:
        for item in root.rglob(pattern):
            if item.is_file():
                cache_files.append(item)
    
    return cache_dirs, cache_files


def clean_cache(dry_run: bool = False):
    """
    清理缓存
    
    Args:
        dry_run: 预览模式，不实际删除
    """
    print("=" * 50)
    print("🧹 清理项目缓存")
    print("=" * 50)
    print(f"📁 项目根目录: {PROJECT_ROOT}")
    if dry_run:
        print("⚠️  预览模式：不会实际删除文件")
    print("")
    
    cache_dirs, cache_files = find_cache_items(PROJECT_ROOT)
    
    total_size = 0
    deleted_count = 0
    
    # 清理目录
    if cache_dirs:
        print(f"📂 发现 {len(cache_dirs)} 个缓存目录:")
        for dir_path in sorted(cache_dirs):
            try:
                # 计算目录大小
                dir_size = sum(f.stat().st_size for f in dir_path.rglob("*") if f.is_file())
                total_size += dir_size
                rel_path = dir_path.relative_to(PROJECT_ROOT)
                size_str = _format_size(dir_size)
                
                if dry_run:
                    print(f"   📁 {rel_path} ({size_str})")
                else:
                    shutil.rmtree(dir_path)
                    print(f"   ✅ {rel_path} ({size_str})")
                    deleted_count += 1
            except Exception as e:
                print(f"   ❌ {dir_path}: {e}")
    else:
        print("📂 未发现缓存目录")
    
    print("")
    
    # 清理文件
    if cache_files:
        print(f"📄 发现 {len(cache_files)} 个缓存文件:")
        for file_path in sorted(cache_files):
            try:
                file_size = file_path.stat().st_size
                total_size += file_size
                rel_path = file_path.relative_to(PROJECT_ROOT)
                size_str = _format_size(file_size)
                
                if dry_run:
                    print(f"   📄 {rel_path} ({size_str})")
                else:
                    file_path.unlink()
                    print(f"   ✅ {rel_path} ({size_str})")
                    deleted_count += 1
            except Exception as e:
                print(f"   ❌ {file_path}: {e}")
    else:
        print("📄 未发现缓存文件")
    
    print("")
    print("=" * 50)
    if dry_run:
        print(f"📊 预览: {len(cache_dirs)} 个目录, {len(cache_files)} 个文件")
        print(f"💾 可释放空间: {_format_size(total_size)}")
        print("")
        print("💡 运行 `python scripts/clean_cache.py` 执行清理")
    else:
        print(f"✅ 清理完成: {deleted_count} 项已删除")
        print(f"💾 释放空间: {_format_size(total_size)}")
    print("=" * 50)


def _format_size(size: int) -> str:
    """格式化文件大小"""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


def main():
    parser = argparse.ArgumentParser(
        description="清理项目缓存文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
清理目标:
    - __pycache__ 目录
    - .cache 目录
    - .pytest_cache 目录
    - *.pyc, *.pyo 文件

示例:
    # 执行清理
    python scripts/clean_cache.py
    
    # 预览模式（不实际删除）
    python scripts/clean_cache.py --dry-run
        """
    )
    
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="预览模式，不实际删除文件"
    )
    
    args = parser.parse_args()
    clean_cache(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
