#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
飞书文档下载器 - Python 版

使用 larkkit 包下载飞书文档。
提供更高的图片下载成功率（带重试机制）和更好的错误处理。

Environment variables:
    FEISHU_APP_ID     飞书应用 App ID
    FEISHU_APP_SECRET 飞书应用 App Secret

Examples:
    python fetch_feishu_doc.py "https://example.feishu.cn/docx/xxx" "docs/features/feature-x/01_requirements_processing/source_export"
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
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from vaf_config import (
    get_feishu_app_id,
    get_feishu_app_secret,
    get_ignore_sections,
    get_ignore_callouts,
    get_ignore_section_callouts,
    get_filter_keywords,
)


def error_message(msg: str) -> None:
    """Print error message to stderr."""
    print(f"[ERROR] {msg}", file=sys.stderr)


def info_message(msg: str) -> None:
    """Print info message to stderr."""
    print(f"[fetch_feishu_doc] {msg}", file=sys.stderr)


def warning_message(msg: str) -> None:
    """Print warning message to stderr."""
    print(f"[WARN] {msg}", file=sys.stderr)


def _resolve_base_dir(output_path: Path) -> Path:
    """解析基础目录（处理 source_export 子目录情况）"""
    return output_path.parent if output_path.name == "source_export" else output_path


def save_metadata(
    doc_url: str,
    output_dir: str,
    version: str,
    *,
    failed_images: list[str] | None = None,
    download_log: str | None = None
) -> None:
    """保存下载元数据
    
    与 Go 版格式兼容，供下游脚本使用。
    """
    metadata = {
        "doc_url": doc_url,
        "download_time": datetime.now().isoformat(),
        "generator": "larkkit",
        "version": version,
        "failed_images": failed_images or [],
        "download_status": "partial" if failed_images else "success",
    }
    if download_log:
        metadata["download_log"] = download_log
    
    output_path = Path(output_dir)
    # 如果 output_dir 是 "source_export"，metadata 保存到父目录
    if output_path.name == "source_export":
        metadata_file = output_path.parent / "metadata.json"
    else:
        metadata_file = output_path / "metadata.json"
        
    try:
        metadata_file.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        info_message(f"Metadata saved to {metadata_file}")
    except Exception as e:
        error_message(f"Failed to save metadata: {e}")


def download_doc(
    doc_url: str,
    output_dir: str,
    *,
    ignore_sections: list[str] | None = None,
    ignore_callouts: list[str] | None = None,
    ignore_section_callouts: list[str] | None = None,
    filter_keywords: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """下载飞书文档到指定目录（使用 CLI 方式）
    
    Args:
        doc_url: 飞书文档 URL
        output_dir: 输出目录路径
        ignore_sections: 忽略包含关键字的章节
        ignore_callouts: 忽略包含关键字的引用块
        ignore_section_callouts: 忽略指定章节下的所有引用块
        filter_keywords: 过滤包含关键字的句子
        
    Returns:
        tuple: (是否成功, 失败的图片列表)
    """
    from larkkit_deps import run_larkkit_download, get_larkkit_version
    
    # 创建输出目录
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    info_message(f"Downloading {doc_url} to {output_dir}...")
    
    # 调用 CLI 下载
    success, result_path, error_msg = run_larkkit_download(
        url=doc_url,
        output_path=str(output_path),
        use_user_token=True,
        ignore_sections=ignore_sections,
        ignore_callouts=ignore_callouts,
        ignore_section_callouts=ignore_section_callouts,
        filter_keywords=filter_keywords,
    )
    
    if success:
        info_message(f"✅ Downloaded to {result_path}")
        
        # 保存元数据
        version = get_larkkit_version()
        save_metadata(
            doc_url=doc_url,
            output_dir=output_dir,
            version=version,
            failed_images=[],
        )
        return True, []
    else:
        error_message(f"Download failed: {error_msg}")
        return False, []


def main():
    parser = argparse.ArgumentParser(
        description="飞书文档下载器 - Python 版",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s "https://example.feishu.cn/docx/xxx" "output/source_export"
  
  # 指定输出目录
  %(prog)s "https://mi.feishu.cn/wiki/xxx" "docs/P01_req_intake/source_export"
        """
    )
    
    parser.add_argument(
        "doc_url",
        help="飞书文档或知识库 URL"
    )
    
    parser.add_argument(
        "output_dir",
        help="输出目录路径"
    )
    
    # ignore 参数
    parser.add_argument(
        "--ignore-sections",
        nargs="*",
        metavar="KEYWORD",
        help="忽略包含关键字的章节（如：--ignore-sections 附录 参考）",
    )
    parser.add_argument(
        "--ignore-callouts",
        nargs="*",
        metavar="KEYWORD",
        help="忽略包含关键字的引用块（如：--ignore-callouts 注意 警告）",
    )
    parser.add_argument(
        "--ignore-section-callouts",
        nargs="*",
        metavar="KEYWORD",
        help="忽略指定章节下的所有引用块（如：--ignore-section-callouts 附录）",
    )
    parser.add_argument(
        "--filter-keywords",
        nargs="*",
        metavar="KEYWORD",
        help="过滤包含关键字的句子（如：--filter-keywords 待删除 TODO）",
    )
    
    args = parser.parse_args()
    
    # 合并 CLI 参数与配置文件（CLI 优先）
    ignore_sections = get_ignore_sections(args.ignore_sections)
    ignore_callouts = get_ignore_callouts(args.ignore_callouts)
    ignore_section_callouts = get_ignore_section_callouts(args.ignore_section_callouts)
    filter_keywords = get_filter_keywords(args.filter_keywords)
    
    # 下载文档
    success, failed_images = download_doc(
        args.doc_url,
        args.output_dir,
        ignore_sections=ignore_sections or None,
        ignore_callouts=ignore_callouts or None,
        ignore_section_callouts=ignore_section_callouts or None,
        filter_keywords=filter_keywords or None,
    )
    
    if not success:
        sys.exit(1)

    if failed_images:
        warning_message(
            "Detected failed image downloads: " + ", ".join(failed_images[:5]) +
            ("..." if len(failed_images) > 5 else "")
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
