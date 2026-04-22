# -*- coding: utf-8 -*-
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
from pathlib import Path
from datetime import datetime

SUPPORTED_IMAGE_EXTS = ("png", "jpg", "jpeg", "gif", "webp", "bmp")

# 图片类型关键词映射
IMAGE_TYPE_KEYWORDS = {
    "流程图": ["流程图", "flowchart", "flow", "流程"],
    "时序图": ["时序图", "sequence", "时序"],
    "架构图": ["架构图", "architecture", "架构"],
    "UI截图": ["截图", "screenshot", "界面", "页面", "UI", "表格", "列表"],
    "对比图": ["对比", "compare", "vs", "改造前", "改造后"],
    "表格": ["表格", "table", "数据表"],
}


def detect_image_type(content: str) -> str:
    """从解析内容检测图片类型"""
    content_lower = content.lower()
    for img_type, keywords in IMAGE_TYPE_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in content_lower:
                return img_type
    return "图片"


def generate_summary(analysis_content: str, max_length: int = 50) -> str:
    """
    从详细解析提取核心摘要（≤50字）
    
    Args:
        analysis_content: 完整的图片解析内容
        max_length: 摘要最大长度
    
    Returns:
        格式: [图片类型] 一句话摘要
    """
    # 1. 检测图片类型
    img_type = detect_image_type(analysis_content)
    
    # 2. 提取关键信息
    key_info = ""
    
    # 尝试提取业务规则
    rule_match = re.search(r'规则1[：:](\s*)(.+?)(?:\n|$)', analysis_content)
    if rule_match:
        key_info = rule_match.group(2).strip()
    
    # 备选：提取业务场景
    if not key_info:
        scene_match = re.search(r'业务场景[：:](\s*)(.+?)(?:\n|$)', analysis_content)
        if scene_match:
            key_info = scene_match.group(2).strip()
    
    # 备选：提取整体概览中的描述
    if not key_info:
        overview_match = re.search(r'整体概览.*?图片类型[：:]\s*(.+?)(?:\n|$)', analysis_content, re.DOTALL)
        if overview_match:
            key_info = overview_match.group(1).strip()
    
    # 备选：提取第一个有意义的描述行
    if not key_info:
        for line in analysis_content.split('\n'):
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('-') and len(line) > 10:
                key_info = line
                break
    
    # 3. 截断并返回
    if key_info:
        # 移除 markdown 格式
        key_info = re.sub(r'\*\*|\*|`', '', key_info)
        if len(key_info) > max_length:
            key_info = key_info[:max_length-3] + "..."
    else:
        key_info = "详见解析文件"
    
    return f"[{img_type}] {key_info}"


def convert_to_blockquote(text: str) -> str:
    """
    将文本转换为 Markdown 引用块格式。
    所有行都添加 > 前缀，包括代码块内部，以保持引用块的连续性。
    """
    lines = text.split('\n')
    result = []
    
    for line in lines:
        result.append(f"> {line}" if line else ">")
    
    return '\n'.join(result)


def merge_content(source_dir, analysis_dir, output_file, mode="compact"):
    """
    合并 PRD 原文与图片解析
    
    Args:
        source_dir: source_export 目录路径
        analysis_dir: assets_analysis 目录路径
        output_file: 输出 PRD.md 路径
        mode: 合并模式
            - "compact": 精简模式（默认），仅添加行内摘要注释
            - "full": 完整模式，内嵌完整解析（兼容旧逻辑）
    """
    source_path = Path(source_dir)
    analysis_path = Path(analysis_dir)
    output_path = Path(output_file)

    # 1. Find source Markdown file
    source_files = list(source_path.glob("*.md"))
    if not source_files:
        print(f"⚠️ Warning: No markdown file found in {source_dir}")
        return
    
    # 过滤掉非PRD文件（如*_comments.md, *_download_report.md等）
    prd_candidates = [f for f in source_files if not any(
        pattern in f.name.lower() 
        for pattern in ['_comments', '_download', '_report', '_log']
    )]
    
    # 优先选择以[PRD]开头或名称包含PRD的文件
    prd_files = [f for f in prd_candidates if f.name.startswith('[PRD]') or 'PRD' in f.name]
    if prd_files:
        source_md_path = prd_files[0]
    elif prd_candidates:
        source_md_path = prd_candidates[0]
    else:
        source_md_path = source_files[0]
    
    print(f"Processing source file: {source_md_path}")
    print(f"Merge mode: {mode}")
    
    content = source_md_path.read_text(encoding='utf-8')
    
    # 统计信息
    stats = {"total_images": 0, "with_analysis": 0}
    
    # 2. Define Regex to match markdown images
    ext_pattern = "|".join(SUPPORTED_IMAGE_EXTS)
    img_pattern = re.compile(rf'!\[(.*?)\]\((.*?/([^/]+?)\.({ext_pattern}))\)', re.IGNORECASE)
    
    def replace_func_compact(match):
        """精简模式：仅添加行内摘要注释"""
        alt_text = match.group(1)
        original_rel_path = match.group(2)
        file_id = match.group(3)
        
        stats["total_images"] += 1
        
        analysis_file = analysis_path / f"{file_id}.md"
        # 如果路径已经包含 source_export/，则不重复添加
        if original_rel_path.startswith("source_export/"):
            new_img_path = original_rel_path
        else:
            new_img_path = f"source_export/{original_rel_path}"
        
        replacement = f"![{alt_text}]({new_img_path})"
        
        if analysis_file.exists():
            try:
                analysis_text = analysis_file.read_text(encoding='utf-8').strip()
                if analysis_text:
                    stats["with_analysis"] += 1
                    summary = generate_summary(analysis_text)
                    # 使用可渲染的引用块格式
                    replacement += f"\n> 📎 {summary} | [详情](assets_analysis/{file_id}.md)"
            except Exception as e:
                print(f"Error reading analysis for {file_id}: {e}")
        
        return replacement
    
    def replace_func_full(match):
        """完整模式：内嵌完整解析（兼容旧逻辑）"""
        alt_text = match.group(1)
        original_rel_path = match.group(2)
        file_id = match.group(3)
        
        stats["total_images"] += 1
        
        analysis_file = analysis_path / f"{file_id}.md"
        # 如果路径已经包含 source_export/，则不重复添加
        if original_rel_path.startswith("source_export/"):
            new_img_path = original_rel_path
        else:
            new_img_path = f"source_export/{original_rel_path}"
        
        replacement = f"![{alt_text}]({new_img_path})"
        
        if analysis_file.exists():
            try:
                analysis_text = analysis_file.read_text(encoding='utf-8').strip()
                if analysis_text:
                    stats["with_analysis"] += 1
                    quoted_analysis = convert_to_blockquote(analysis_text)
                    replacement += f"\n\n> 🤖 **AI 视觉解析**:\n>\n{quoted_analysis}\n"
            except Exception as e:
                print(f"Error reading analysis for {file_id}: {e}")
        
        return replacement

    # 3. 选择替换函数
    replace_func = replace_func_compact if mode == "compact" else replace_func_full
    
    # 4. Execute replacement
    new_content = img_pattern.sub(replace_func, content)
    
    # 4.5. 处理 HTML img 标签中的图片路径（表格中常见）
    # 匹配 <img src="static/xxx.png"> 并添加 source_export/ 前缀
    html_img_pattern = re.compile(r'<img\s+src="(?!source_export/)(static/[^"]+)"', re.IGNORECASE)
    new_content = html_img_pattern.sub(r'<img src="source_export/\1"', new_content)
    
    # 4.6. 替换评论文件链接路径（添加 source_export/ 前缀）
    # 匹配格式: ]([PRD]xxx_comments.md) 或 ](xxx.md)，但排除已有 source_export/ 的
    comment_pattern = re.compile(r'\]\((?!source_export/)([^\)]+_comments\.md)\)')
    new_content = comment_pattern.sub(r'](source_export/\1)', new_content)
    
    # 5. 添加 YAML 头部元数据（仅 compact 模式）
    if mode == "compact":
        yaml_header = f"""---
version: v2
mode: compact
assets_count: {stats['total_images']}
assets_with_analysis: {stats['with_analysis']}
assets_index: assets_summary.md
source: {source_md_path.name}
generated_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
---

"""
        new_content = yaml_header + new_content
    
    # 6. Write result
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(new_content, encoding='utf-8')
    
    # 7. 输出统计
    line_count = len(new_content.split('\n'))
    print(f"✅ Generated PRD ({mode} mode): {output_file}")
    print(f"   📊 Images: {stats['total_images']}, With analysis: {stats['with_analysis']}")
    print(f"   📄 Total lines: {line_count}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Merge Markdown content with AI image analysis.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 精简模式（默认，推荐）
  python merge_prd_assets.py --source ./source_export --analysis ./assets_analysis --output ./PRD.md
  
  # 完整模式（兼容旧逻辑）
  python merge_prd_assets.py --source ./source_export --analysis ./assets_analysis --output ./PRD.md --mode full
"""
    )
    parser.add_argument("--source", required=True, help="Path to source_export directory")
    parser.add_argument("--analysis", required=True, help="Path to assets_analysis directory")
    parser.add_argument("--output", required=True, help="Path to output PRD.md file")
    parser.add_argument(
        "--mode", 
        choices=["compact", "full"], 
        default="compact",
        help="Merge mode: 'compact' (default, inline summary) or 'full' (embedded analysis)"
    )
    
    args = parser.parse_args()
    
    merge_content(args.source, args.analysis, args.output, mode=args.mode)
