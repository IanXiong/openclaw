#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""Copy mapped sections from a source PRD into a target template.

The script expects a mapping JSON describing how chapters in the
`source` document should be relocated into the `target` document. The
JSON can either be:

```
[
  {"raw": "1. 用户痛点分析", "target": "1. 用户痛点分析", "mode": "L1_自动"}
]
```

or wrapped in an object with a `mappings` field.  Each mapping entry
must provide `raw` (source chapter title) and `target` (template
chapter title). Entries with empty `target` will be ignored, which
enables callers to keep `UNMATCHED` rows inside一张映射表。

## 根治方案 (v2.1)
1. 标题层级标准化：将源内容中的子章节标题转换为目标层级
2. 重复内容检测：检测目标文件是否已经包含相同章节，避免重复追加
3. 图片路径重写：自动处理相对路径
4. 内容清洗：移除乱码和残缺表格行
5. 🆕 AI 视觉解析格式转换：自动将 Blockquote 格式转为 Details 格式
   - 原始格式: > 🤖 **AI 视觉解析**: ...
   - 目标格式: <details><summary>🔍 AI 图片解析</summary>...</details>
"""

from __future__ import annotations

import sys
import io

# 跨平台 Unicode 输出支持 (Windows GBK 兼容)
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)

# 匹配残缺的表格行（以 | 开头但缺少完整表头）
BROKEN_TABLE_RE = re.compile(r"^[\s|]+\|[^|]*\|[^|]*\|[^|]*\|\s*$", re.MULTILINE)

# 匹配 Blockquote 格式的 AI 视觉解析（以 > 🤖 **AI 视觉解析**: 开头）
AI_VISUAL_BLOCKQUOTE_RE = re.compile(
    r'^(>\s*🤖\s*\*\*AI\s*视觉解析\*\*:?\s*\n(?:>.*\n)*)',
    re.MULTILINE
)


@dataclass(frozen=True)
class Section:
    title: str
    level: int
    start: int
    heading_end: int
    end: int
    body: str


def normalize_heading(title: str) -> str:
    """Normalize heading text for reliable matching."""
    cleaned = title.strip()
    cleaned = cleaned.lstrip("#").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.lower()


def parse_sections(text: str) -> List[Section]:
    """Parse markdown headings into Section objects."""
    matches = list(HEADING_RE.finditer(text))
    sections: List[Section] = []
    for idx, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        start = match.start()
        # heading_end: first char after the newline of heading line
        heading_line_end = text.find("\n", match.end())
        if heading_line_end == -1:
            heading_end = len(text)
        else:
            heading_end = heading_line_end + 1
        next_start = len(text)
        for next_match in matches[idx + 1:]:
            if len(next_match.group(1)) <= level:
                next_start = next_match.start()
                break
        body = text[heading_end:next_start]
        sections.append(
            Section(
                title=title,
                level=level,
                start=start,
                heading_end=heading_end,
                end=next_start,
                body=body,
            )
        )
    return sections


def build_lookup(sections: Sequence[Section]) -> Dict[str, List[Section]]:
    lookup: Dict[str, List[Section]] = {}
    for section in sections:
        lookup.setdefault(normalize_heading(section.title), []).append(section)
    return lookup


def load_mapping(path: Path) -> List[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "mappings" in data:
        data = data["mappings"]
    if not isinstance(data, list):
        raise ValueError("映射文件格式错误：需要 list 或包含 mappings 的对象")
    return data


def content_hash(text: str) -> str:
    """计算内容的 MD5 哈希，用于重复检测"""
    # 忽略空白差异
    normalized = re.sub(r"\s+", " ", text.strip())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:16]


def normalize_heading_levels(body: str, source_level: int, target_level: int) -> str:
    """
    标准化 body 中的子章节标题层级。
    
    例如：源章节是 #### (level=4)，目标是 ## (level=2)
    源 body 中的 ##### (level=5) 应该转为 ### (level=3)
    
    规则：target_sub_level = target_level + (source_sub_level - source_level)
    """
    if source_level == target_level:
        return body
    
    level_diff = target_level - source_level
    
    def replace_heading(match: re.Match) -> str:
        hashes = match.group(1)
        title = match.group(2)
        old_level = len(hashes)
        new_level = max(1, min(6, old_level + level_diff))  # 保持在 1-6 范围内
        return "#" * new_level + " " + title
    
    return HEADING_RE.sub(replace_heading, body)


def convert_blockquote_to_details(text: str) -> str:
    """
    将 Blockquote 格式的 AI 视觉解析转换为 Details 格式。
    
    原始格式：
    > 🤖 **AI 视觉解析**:
    > # 图像分析报告
    > ## 1. 整体概览
    > ...
    
    转换为：
    <details>
    <summary>🔍 AI 图片解析</summary>
    
    (解析内容)
    
    </details>
    """
    def replace_blockquote(match: re.Match) -> str:
        blockquote = match.group(1)
        # 移除每行开头的 > 
        lines = blockquote.split('\n')
        content_lines = []
        for line in lines:
            # 移除行首的 > 和空格
            cleaned = re.sub(r'^>\s?', '', line)
            # 跳过空的 AI 视觉解析标题行
            if re.match(r'^🤖\s*\*\*AI\s*视觉解析\*\*:?\s*$', cleaned):
                continue
            # 跳过 # 图像分析报告 标题
            if re.match(r'^#\s*图像分析报告\s*$', cleaned):
                continue
            content_lines.append(cleaned)
        
        # 提取图片类型和业务场景作为 summary 的一部分
        summary_title = "🔍 AI 图片解析"
        content = '\n'.join(content_lines).strip()
        
        # 构建 details 块
        return f'''<details>
<summary>{summary_title}</summary>

{content}

</details>'''
    
    return AI_VISUAL_BLOCKQUOTE_RE.sub(replace_blockquote, text)


def clean_body_content(body: str) -> str:
    """
    清洗 body 内容，移除常见问题：
    1. 残缺的表格行（缺少表头的行）
    2. 纯空白的占位符行
    3. 重复的分隔线
    4. 转换 Blockquote 格式的 AI 视觉解析为 Details 格式
    """
    # 首先转换 Blockquote 格式的 AI 视觉解析
    body = convert_blockquote_to_details(body)
    
    lines = body.split("\n")
    cleaned_lines: List[str] = []
    prev_was_empty = False
    
    for line in lines:
        # 跳过残缺表格行（以空格+|开头，通常是搬运时的残留）
        if re.match(r"^\s+\|.*\|.*\|\s*$", line) and not line.strip().startswith("|"):
            continue
        
        # 跳过纯分隔符行（如 ------- | ------ | ------）
        if re.match(r"^[\s\-|]+$", line) and "|" in line and not re.match(r"^\|[\s\-]+\|", line):
            continue
        
        # 合并连续空行
        is_empty = not line.strip()
        if is_empty and prev_was_empty:
            continue
        
        cleaned_lines.append(line)
        prev_was_empty = is_empty
    
    return "\n".join(cleaned_lines)


def check_duplicate_content(target_body: str, source_body: str, threshold: float = 0.8) -> bool:
    """
    检测目标章节是否已经包含源内容（重复检测）。
    
    使用简单的文本相似度检测：
    - 如果目标 body 已经包含源 body 的大部分内容，返回 True
    """
    if not source_body.strip():
        return False
    
    # 提取源内容中的关键短语（去掉空白和标点）
    source_phrases = set(re.findall(r"[\u4e00-\u9fa5a-zA-Z0-9]+", source_body))
    if not source_phrases:
        return False
    
    target_text = target_body.lower()
    matched = sum(1 for phrase in source_phrases if phrase.lower() in target_text)
    
    similarity = matched / len(source_phrases)
    return similarity >= threshold


def sanitize_section_body(chunks: Sequence[str]) -> str:
    sanitized: List[str] = []
    for chunk in chunks:
        text = chunk.strip("\n")
        if text:
            # 清洗每个 chunk
            text = clean_body_content(text)
            sanitized.append(text)
    if not sanitized:
        return "\n"
    joined = "\n\n".join(sanitized)
    return f"\n{joined}\n\n"


def apply_replacements(
    text: str, 
    replacements: Dict[Section, List[Tuple[str, int, int]]],
    enable_level_normalization: bool = True
) -> str:
    """
    应用替换操作。
    
    replacements: Dict[target_section, List[(body, source_level, target_level)]]
    """
    operations = []
    for section, body_items in replacements.items():
        processed_bodies = []
        for body, source_level, target_level in body_items:
            if enable_level_normalization:
                body = normalize_heading_levels(body, source_level, target_level)
            processed_bodies.append(body)
        
        new_body = sanitize_section_body(processed_bodies)
        heading = text[section.start:section.heading_end]
        new_chunk = heading + new_body
        operations.append((section.start, section.end, new_chunk))

    # Apply from back to front to keep indices stable
    operations.sort(key=lambda item: item[0], reverse=True)
    for start, end, chunk in operations:
        text = text[:start] + chunk + text[end:]
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description="目录驱动搬运 PRD 章节 (v2.0 - 含标题标准化)")
    parser.add_argument("--source", required=True, help="原始 PRD (01 阶段) 的路径")
    parser.add_argument("--target", required=True, help="目标模板 PRD 的路径")
    parser.add_argument("--mapping", required=True, help="章节映射 JSON 文件路径")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印规划，不写回 target 文件",
    )
    parser.add_argument(
        "--no-level-normalize",
        action="store_true",
        help="禁用标题层级标准化",
    )
    parser.add_argument(
        "--skip-duplicate-check",
        action="store_true",
        help="跳过重复内容检测",
    )
    args = parser.parse_args()

    source_path = Path(args.source).expanduser().resolve()
    target_path = Path(args.target).expanduser().resolve()
    mapping_path = Path(args.mapping).expanduser().resolve()

    if not source_path.exists():
        raise SystemExit(f"源文件不存在: {source_path}")
    if not target_path.exists():
        raise SystemExit(f"目标文件不存在: {target_path}")
    if not mapping_path.exists():
        raise SystemExit(f"映射文件不存在: {mapping_path}")

    source_text = source_path.read_text(encoding="utf-8")
    target_text = target_path.read_text(encoding="utf-8")
    source_sections = build_lookup(parse_sections(source_text))
    target_sections_list = parse_sections(target_text)
    target_sections = build_lookup(target_sections_list)

    mappings = load_mapping(mapping_path)
    # 新结构：Dict[Section, List[(body, source_level, target_level)]]
    replacements: Dict[Section, List[Tuple[str, int, int]]] = {}
    skipped = 0
    duplicate_skipped = 0
    
    for item in mappings:
        raw_title = item.get("raw") or item.get("source")
        target_title = item.get("target") or item.get("template")
        if not raw_title:
            print("⚠️ 跳过：缺少 raw 字段", file=sys.stderr)
            continue
        if not target_title:
            skipped += 1
            continue
        
        raw_key = normalize_heading(raw_title)
        target_key = normalize_heading(target_title)
        raw_list = source_sections.get(raw_key)
        if not raw_list:
            print(f"⚠️ 未找到源章节: {raw_title}", file=sys.stderr)
            continue
        raw_section = raw_list.pop(0)
        target_list = target_sections.get(target_key)
        if not target_list:
            print(f"⚠️ 未找到目标章节: {target_title}", file=sys.stderr)
            continue
        target_section = target_list[0]
        
        # 重复内容检测
        if not args.skip_duplicate_check:
            if check_duplicate_content(target_section.body, raw_section.body):
                print(f"⏭️ 跳过重复: {raw_title} → {target_title}", file=sys.stderr)
                duplicate_skipped += 1
                continue
        
        # 记录源和目标的层级，用于后续标准化
        replacements.setdefault(target_section, []).append(
            (raw_section.body, raw_section.level, target_section.level)
        )
        print(f"📦 映射: {raw_title} (L{raw_section.level}) → {target_title} (L{target_section.level})")

    if not replacements:
        print("⚠️ 未找到可搬运的章节映射")
        return 0

    new_text = apply_replacements(
        target_text, 
        replacements,
        enable_level_normalization=not args.no_level_normalize
    )
    
    if args.dry_run:
        print("[Dry-Run] 目标文件不会被修改。")
        print(new_text)
        return 0

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(new_text, encoding="utf-8")
    print(
        f"✅ 已写入 {target_path.name} "
        f"(搬运 {len(replacements)} 个章节, 跳过空目标 {skipped} 个, 跳过重复 {duplicate_skipped} 个)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
