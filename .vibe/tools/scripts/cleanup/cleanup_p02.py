#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P02 知识库产物清理脚本

跨平台支持（Windows/macOS/Linux），用于清理 P02 阶段的现有产物。

使用方法:
    python .vibe/tools/scripts/cleanup_p02.py "docs/features/<功能名称>/P02_context_analysis"
"""

import sys
import io

# 跨平台 Unicode 输出支持 (Windows GBK 兼容)
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import argparse
import shutil
from pathlib import Path


def cleanup_p02(output_dir: Path, dry_run: bool = False) -> dict:
    """
    清理 P02 阶段的现有产物
    
    Args:
        output_dir: P02 输出目录路径
        dry_run: 仅预览，不实际删除
        
    Returns:
        dict: 清理结果统计
    """
    results = {
        "deleted_dirs": [],
        "deleted_files": [],
        "not_found": [],
        "errors": []
    }
    
    # 需要清理的目录
    dirs_to_clean = ["source_export", "knowledge_repos"]
    
    # 需要清理的文件
    files_to_clean = ["knowledge_base.toon", "context_analysis_report.md"]
    
    # 清理目录
    for dir_name in dirs_to_clean:
        dir_path = output_dir / dir_name
        if dir_path.exists():
            if dry_run:
                print(f"[预览] 将删除目录: {dir_path}")
                results["deleted_dirs"].append(str(dir_path))
            else:
                try:
                    shutil.rmtree(dir_path)
                    print(f"✅ 已删除目录: {dir_path}")
                    results["deleted_dirs"].append(str(dir_path))
                except Exception as e:
                    print(f"❌ 删除目录失败: {dir_path} - {e}")
                    results["errors"].append(f"{dir_path}: {e}")
        else:
            results["not_found"].append(str(dir_path))
    
    # 清理文件
    for file_name in files_to_clean:
        file_path = output_dir / file_name
        if file_path.exists():
            if dry_run:
                print(f"[预览] 将删除文件: {file_path}")
                results["deleted_files"].append(str(file_path))
            else:
                try:
                    file_path.unlink()
                    print(f"✅ 已删除文件: {file_path}")
                    results["deleted_files"].append(str(file_path))
                except Exception as e:
                    print(f"❌ 删除文件失败: {file_path} - {e}")
                    results["errors"].append(f"{file_path}: {e}")
        else:
            results["not_found"].append(str(file_path))
    
    return results


def main():
    parser = argparse.ArgumentParser(
        description="P02 知识库产物清理脚本（跨平台）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 清理指定目录
    python .vibe/tools/scripts/cleanup_p02.py "docs/features/feature/inventory_optimize/P02_context_analysis"
    
    # 预览模式（不实际删除）
    python .vibe/tools/scripts/cleanup_p02.py "docs/features/feature/inventory_optimize/P02_context_analysis" --dry-run
        """
    )
    
    parser.add_argument(
        "output_dir",
        type=str,
        help="P02 输出目录路径"
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="预览模式，仅显示将要删除的内容，不实际执行"
    )
    
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir)
    
    # 验证目录
    if not output_dir.exists():
        print(f"⚠️ 目录不存在: {output_dir}")
        print("无需清理，目录将在后续步骤中创建。")
        sys.exit(0)
    
    if not output_dir.is_dir():
        print(f"❌ 错误: {output_dir} 不是目录")
        sys.exit(1)
    
    print("=" * 60)
    print("P02 知识库产物清理")
    print("=" * 60)
    print(f"目标目录: {output_dir}")
    print(f"模式: {'预览' if args.dry_run else '执行'}")
    print("-" * 60)
    
    results = cleanup_p02(output_dir, dry_run=args.dry_run)
    
    print("-" * 60)
    print("清理汇总:")
    print(f"  删除目录: {len(results['deleted_dirs'])} 个")
    print(f"  删除文件: {len(results['deleted_files'])} 个")
    print(f"  未找到: {len(results['not_found'])} 个")
    print(f"  错误: {len(results['errors'])} 个")
    
    if results["errors"]:
        print("\n❌ 清理过程中发生错误:")
        for error in results["errors"]:
            print(f"  - {error}")
        sys.exit(1)
    
    if args.dry_run:
        print("\n💡 这是预览模式，未实际删除任何内容。")
        print("   移除 --dry-run 参数以执行实际删除。")
    else:
        print("\n✅ 清理完成！")


if __name__ == "__main__":
    main()

