#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PRD 标准化脚本（目录映射 + 无损搬运版本）
功能：
1. 目录映射：将原始 PRD 章节与标准模板对账
2. 复制 PRD.md 到 PRD_standardized.md（100% 无损）
3. 移除源文件的 YAML 头部
4. 全局替换图片路径 + assets_analysis 路径
5. 添加标准化 YAML 头部
6. 完整性校验 + 目录对账表输出

用法：
  python prd_standardize.py <feature_path>
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
import re
from pathlib import Path
from datetime import datetime
import os


# 🔧 注意：章节过滤已迁移到 P01（larkkit 下载阶段）
# 配置位置：~/.vaf/config 或 ~/.larkkit/config 的 [download] 段
# 示例：ignore_sections = 多语言支持,数据埋点,高危元素,交付与上线,参考文献,评审记录,附录


def normalize_section_name(name: str) -> str:
    """标准化章节名称（去除序号、空格、markdown格式符号等）"""
    import re
    # 去除markdown格式符号（加粗**、斜体*等）
    name = re.sub(r'\*+', '', name)
    # 去除开头的序号（如 "1. "、"1.1 "、"一、"、"## " 等）
    name = re.sub(r'^[#\s]*', '', name)
    name = re.sub(r'^[\d\.]+\s*', '', name)
    name = re.sub(r'^[一二三四五六七八九十]+、\s*', '', name)
    name = name.strip()
    return name


def match_section(raw_section: str) -> tuple:
    """章节映射已废弃（P01阶段由larkkit过滤），保留空函数用于兼容性"""
    return (raw_section, 'L1_精确', 'High')


def generate_mapping_report(headings: list) -> dict:
    """生成目录映射报告"""
    mapping_results = []
    stats = {'total': 0, 'l1_matched': 0, 'l3_matched': 0, 'unmatched': 0}
    
    # 检测 PRD 的标题层级模式（有些 PRD 用 ### 作为主章节）
    # 找出包含"一、"或"二、"等中文序号的标题层级
    main_section_level = 1
    for level, title, anchor in headings:
        if '一、' in title or '二、' in title or '三、' in title or '四、' in title:
            main_section_level = level
            break
    
    # 计算层级偏移（如 ### = level 3，但实际是一级章节）
    level_offset = main_section_level - 1
    
    for level, title, anchor in headings:
        # 调整层级
        adjusted_level = level - level_offset if level_offset > 0 else level
        
        # 只映射调整后的 1-2 级标题
        if adjusted_level > 2 or adjusted_level < 1:
            continue
        
        stats['total'] += 1
        matched, match_type, confidence = match_section(title)
        
        if match_type == 'L1_精确':
            stats['l1_matched'] += 1
        elif match_type == 'L3_归并':
            stats['l3_matched'] += 1
        else:
            stats['unmatched'] += 1
        
        mapping_results.append({
            'raw_section': title,
            'target_section': matched or '-',
            'match_type': match_type,
            'confidence': confidence,
            'level': adjusted_level
        })
    
    return {'mappings': mapping_results, 'stats': stats}




def extract_yaml_metadata(content: str) -> dict:
    """提取 YAML 头部元数据"""
    metadata = {}
    if content.startswith('---'):
        yaml_end = content.find('---', 3)
        if yaml_end != -1:
            yaml_block = content[3:yaml_end].strip()
            for line in yaml_block.split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    metadata[key.strip()] = value.strip()
    return metadata


def generate_yaml_header(source_metadata: dict, source_file: str) -> str:
    """生成标准化 YAML 头部"""
    title = source_metadata.get('source', 'PRD').replace('.md', '')
    lines = [
        '---',
        f'title: {title}',
        f'version: v1.0',
        f'source: P01_req_intake/PRD.md',
        f'standardized_at: {datetime.now().strftime("%Y-%m-%d")}',
        f'mode: standard',
        '---'
    ]
    return '\n'.join(lines)


def read_image_analysis(image_id: str, base_path: Path) -> str:
    """读取图片对应的MD解析文件
    
    Args:
        image_id: 图片文件名（不含扩展名），如 board_QIShwGdFVhXkb5b0HI0cPAsensd
        base_path: feature目录的基础路径
    
    Returns:
        str: MD解析内容，若文件不存在则返回空字符串
    """
    analysis_path = base_path / 'P01_req_intake' / 'assets_analysis' / f'{image_id}.md'
    if analysis_path.exists():
        return analysis_path.read_text(encoding='utf-8')
    return ''


def embed_image_analysis(content: str, base_path: Path) -> tuple:
    """将PNG引用替换为MD解析内容的嵌入块
    
    Args:
        content: PRD原始内容
        base_path: feature目录的基础路径
    
    Returns:
        tuple: (处理后的内容, 嵌入图片数量)
    """
    # 匹配图片引用：![alt](static/image_id.png)
    pattern = r'!\[([^\]]*)\]\(static/([^)]+)\.png\)'
    
    embedded_count = 0
    
    def replace_image(match):
        nonlocal embedded_count
        alt_text = match.group(1)
        image_id = match.group(2)
        
        # 读取对应的MD解析
        analysis = read_image_analysis(image_id, base_path)
        
        if not analysis:
            # 如果没有解析文件，保留原始引用
            return match.group(0)
        
        embedded_count += 1
        
        # 生成嵌入块（使用HTML注释标记来源）
        embedded_block = f'''<!-- 图片解析嵌入 (来源: P01_req_intake/assets_analysis/{image_id}.md) -->
<details>
<summary>📊 图片解析：{alt_text or '流程图/架构图'}</summary>

{analysis}

</details>
'''
        return embedded_block
    
    # 执行替换
    new_content = re.sub(pattern, replace_image, content)
    
    return new_content, embedded_count


# filter_by_template 函数已废弃（章节过滤已迁移到P01阶段）


def parse_table_row(line: str) -> list:
    """
    解析Markdown表格行，正确处理split('|')的边界情况
    
    Args:
        line: 表格行，如 "| 序号 | **功能点** | **需求说明** | **原型** |"
    
    Returns:
        list: 单元格列表，去除首尾空字符串
        例如: ['序号', '**功能点**', '**需求说明**', '**原型**']
    """
    cells = [c.strip() for c in line.split('|')]
    # 移除首尾空字符串（Markdown表格格式：| col1 | col2 |）
    if cells and not cells[0]:
        cells = cells[1:]
    if cells and not cells[-1]:
        cells = cells[:-1]
    return cells




def explode_feature_table(content: str) -> tuple:
    """
    通用表格拆解：将超长表格转换为层级章节结构
    
    第一性原则分析：
    1. 表格的本质：结构化数据（表头 + 数据行）
    2. 拆解的目的：单元格内容过长时，表格格式难以阅读，需要转换为层级结构
    3. 通用策略：
       - 检测所有表格
       - 计算单元格最大长度
       - 如果超过阈值 → 拆解为章节
       - 动态识别标题列（用于生成章节标题）
       - 其他列作为内容段落
    
    Args:
        content: PRD内容
    
    Returns:
        tuple: (处理后的内容, 拆解的表格行数)
    """
    CELL_LENGTH_THRESHOLD = 300  # 单元格长度阈值（字符）
    
    lines = content.split('\n')
    result_lines = []
    exploded_count = 0
    
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        # 检测表格开始（以 | 开头且包含至少2个 |）
        if stripped.startswith('|') and stripped.count('|') >= 3:
            # 收集完整表格
            table_lines = []
            table_start = i
            
            while i < len(lines) and lines[i].strip().startswith('|'):
                table_lines.append(lines[i])
                i += 1
            
            # 分析表格结构
            if len(table_lines) < 2:  # 至少需要表头+分隔符
                result_lines.extend(table_lines)
                continue
            
            # 解析表头
            header_cells = parse_table_row(table_lines[0])
            if not header_cells:
                result_lines.extend(table_lines)
                continue
            
            # 检查是否需要拆解（计算最大单元格长度）
            max_cell_length = 0
            for tline in table_lines[2:]:  # 跳过表头和分隔符
                row_cells = parse_table_row(tline)
                for cell in row_cells:
                    # 移除HTML标签后计算长度
                    clean_cell = re.sub(r'<[^>]+>', '', cell)  # 移除所有HTML标签
                    clean_cell = clean_cell.replace('<br>', ' ').replace('<br/>', ' ').replace('<br />', ' ')
                    max_cell_length = max(max_cell_length, len(clean_cell))
            
            # 判断是否需要拆解
            if max_cell_length > CELL_LENGTH_THRESHOLD:
                # 拆解表格（完全通用，无硬编码）
                exploded = explode_table_generic(table_lines, header_cells)
                result_lines.extend(exploded)
                exploded_count += len(table_lines) - 2  # 减去表头和分隔符
            else:
                # 单元格长度安全，保留表格格式
                result_lines.extend(table_lines)
            
            continue
        
        # 非表格行，正常保留
        result_lines.append(line)
        i += 1
    
    return '\n'.join(result_lines), exploded_count


def explode_table_generic(table_lines: list, header_cells: list) -> list:
    """
    通用表格拆解：将表格行转换为平铺结构（完全通用，无硬编码）
    
    第一性原则：
    1. 表格的本质：表头定义列名，数据行填充内容
    2. 拆解的目的：单元格过长时，表格难以阅读，需要平铺为段落
    3. 通用策略：
       - 每一行数据转换为一个条目
       - 每一列都使用表头作为段落标题
       - 完全平铺，不生成编号，不识别特殊列
    
    Args:
        table_lines: 表格所有行（包括表头和分隔符）
        header_cells: 表头单元格列表（已清理格式）
    
    Returns:
        list: 拆解后的段落行列表
    """
    result = []
    
    for line in table_lines[2:]:  # 跳过表头和分隔符
        row_cells = parse_table_row(line)
        
        if not row_cells:
            continue
        
        # 检查是否为空行（所有单元格都为空或只有分隔符）
        if all(not cell.strip() or cell.strip() == '|' for cell in row_cells):
            continue
        
        # 每一行数据转换为一个条目
        # 每一列都作为内容段落，使用表头作为段落标题
        row_fields = []  # 存储当前行的所有字段
        
        for col_idx, header in enumerate(header_cells):
            if col_idx >= len(row_cells):
                break
            
            cell_content = row_cells[col_idx].strip()
            
            # 跳过空单元格
            if not cell_content or cell_content == '|':
                continue
            
            # 保留原表头格式（包括加粗），只去除首尾空格
            header_clean = header.strip()
            if not header_clean:
                continue
            
            # 处理单元格内容：转换<br>为换行，清理HTML标签
            cell_text = cell_content.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
            # 移除HTML标签但保留文本内容
            cell_text = re.sub(r'<[^>]+>', '', cell_text)
            # 去除首尾空白和换行，但保留内部换行
            cell_text = cell_text.strip()
            # 去除所有前导的换行和空白（确保表头后直接跟内容）
            cell_text = cell_text.lstrip('\n\r\t ')
            
            # 生成字段：表头作为标题，单元格内容作为正文（保留原格式，包括加粗）
            # 紧凑格式：表头后直接跟内容，无换行
            row_fields.append(f'{header_clean}：{cell_text}')
        
        # 同一行的字段之间用换行连接（不空行），行末添加空行分隔不同行
        if row_fields:
            result.append('\n'.join(row_fields) + '\n\n')
    
    return result


def validate_completeness(source: str, target: str) -> dict:
    """校验完整性"""
    checks = {}
    
    # 图片数量
    source_images = source.count('![')
    target_images = target.count('![')
    checks['images'] = {'source': source_images, 'target': target_images, 'pass': source_images == target_images}
    
    # 行内摘要数量
    source_summaries = source.count('> 📎')
    target_summaries = target.count('> 📎')
    checks['summaries'] = {'source': source_summaries, 'target': target_summaries, 'pass': source_summaries == target_summaries}
    
    # HTML 表格
    source_tables = source.count('<table>')
    target_tables = target.count('<table>')
    checks['html_tables'] = {'source': source_tables, 'target': target_tables, 'pass': source_tables == target_tables}
    
    # 三级标题
    source_h3 = source.count('\n### ')
    target_h3 = target.count('\n### ')
    checks['h3_headings'] = {'source': source_h3, 'target': target_h3, 'pass': source_h3 == target_h3}
    
    # 四级标题
    source_h4 = source.count('\n#### ')
    target_h4 = target.count('\n#### ')
    checks['h4_headings'] = {'source': source_h4, 'target': target_h4, 'pass': source_h4 == target_h4}
    
    all_pass = all(c['pass'] for c in checks.values())
    return {'checks': checks, 'all_pass': all_pass}


def standardize_prd(feature_path: str) -> dict:
    """执行 PRD 标准化（无损搬运版本）"""
    print('[DEBUG] 进入 standardize_prd', flush=True)
    base = Path(feature_path)
    source_prd = base / 'P01_req_intake' / 'PRD.md'
    target_prd = base / 'P03_prd_review' / 'PRD_standardized.md'
    
    print(f'[DEBUG] 源文件: {source_prd}', flush=True)
    if not source_prd.exists():
        return {'success': False, 'error': f'源文件不存在: {source_prd}'}
    
    target_prd.parent.mkdir(parents=True, exist_ok=True)
    
    print('[DEBUG] 读取文件...', flush=True)
    raw_content = source_prd.read_text(encoding='utf-8')
    original_lines = len(raw_content.split('\n'))
    print(f'[DEBUG] 文件读取完成, {original_lines} 行', flush=True)
    
    # 0. 提取原始 YAML 元数据
    source_metadata = extract_yaml_metadata(raw_content)
    
    # 1. 移除 YAML 头部（保留正文）
    content = raw_content
    if content.startswith('---'):
        yaml_end = content.find('---', 3)
        if yaml_end != -1:
            content = content[yaml_end + 3:].lstrip('\n')
    print('[DEBUG] YAML 头部已处理', flush=True)
    
    # 2. 替换PNG图片引用为MD解析文件引用
    # 支持两种格式：
    # - Markdown: ![画板](static/board_xxx.png) 
    # - HTML: <img src="static/board_xxx.png" ...>
    # 
    # 🔧 修复：区分表格内和独立行的替换格式
    # - 表格内：使用内联链接格式，避免 | 和 > 符号破坏表格结构
    # - 独立行：使用引用块格式，更美观
    import re
    png_count = 0
    
    def is_in_table_row(text: str, match_start: int) -> bool:
        """检查匹配位置是否在表格行内"""
        # 找到当前行的起始位置
        line_start = text.rfind('\n', 0, match_start) + 1
        # 找到当前行的结束位置
        line_end = text.find('\n', match_start)
        if line_end == -1:
            line_end = len(text)
        # 获取当前行内容
        current_line = text[line_start:line_end]
        # 表格行特征：以|开头或包含多个|
        stripped = current_line.strip()
        return stripped.startswith('|') or current_line.count('|') >= 2
    
    def replace_markdown_png(match):
        nonlocal png_count
        alt_text = match.group(1) or '流程图'
        png_filename = match.group(2)
        # 提取纯文件ID（去掉路径前缀和.png后缀）
        file_id = png_filename.split('/')[-1].replace('.png', '')
        png_count += 1
        md_path = f'../P01_req_intake/assets_analysis/{file_id}.md'
        
        if is_in_table_row(content, match.start()):
            # 表格内：使用内联链接格式，不含 | 和 >
            return f'[📎{alt_text}]({md_path})'
        else:
            # 独立行：若下一行已有摘要行（📎），则删除PNG行避免冗余
            # 返回空字符串，让后续清理连续空行
            return ''
    
    def replace_html_img(match):
        nonlocal png_count
        png_filename = match.group(1)
        # 提取纯文件ID（去掉路径前缀和.png后缀）
        file_id = png_filename.split('/')[-1].replace('.png', '')
        png_count += 1
        md_path = f'../P01_req_intake/assets_analysis/{file_id}.md'
        
        if is_in_table_row(content, match.start()):
            # 表格内：使用内联链接格式，不含 | 和 >
            return f'[📎图片]({md_path})'
        else:
            # 独立行：若下一行已有摘要行，则删除PNG行避免冗余
            return ''
    
    # 替换Markdown格式的PNG引用
    content = re.sub(r'!\[([^\]]*)\]\((?:static/)?([^)]+\.png)\)', replace_markdown_png, content)
    # 替换HTML格式的PNG引用
    content = re.sub(r'<img\s+src="(?:static/)?([^"]+\.png)"[^>]*>', replace_html_img, content)
    print(f'[DEBUG] PNG引用已替换为MD文件引用：{png_count} 个', flush=True)
    
    # 3. 替换 assets_analysis 路径（其他引用）
    content = content.replace('](assets_analysis/', '](../P01_req_intake/assets_analysis/')
    print('[DEBUG] assets_analysis 路径已替换', flush=True)
    
    # 3.1 简化摘要行格式：📎 [类型] 描述... | [详情](path) → [📎类型](path)
    # 匹配格式：📎 [类型] 任意描述 | [详情](路径)
    summary_pattern = r'📎 \[([^\]]+)\][^|]*\| \[详情\]\(([^)]+)\)'
    summary_count = len(re.findall(summary_pattern, content))
    content = re.sub(summary_pattern, r'[📎\1](\2)', content)
    print(f'[DEBUG] 摘要行已简化：{summary_count} 处', flush=True)
    
    # 3.5 🆕 清理对AI无用的格式内容（减少token浪费）
    cleanup_stats = {'strikethrough': 0, 'color_span': 0, 'bg_span': 0, 'underline': 0, 'at_user': 0}
    
    # 3.5.1 移除删除线内容（~~xxx~~ 表示已废弃/本次不做）
    strikethrough_pattern = r'~~[^~]+~~'
    cleanup_stats['strikethrough'] = len(re.findall(strikethrough_pattern, content))
    content = re.sub(strikethrough_pattern, '', content)
    
    # 3.5.2 移除颜色span标签，保留内部文本
    # <span style="color:#E6B800">逆退</span> → 逆退
    color_span_pattern = r'<span\s+style="color:[^"]*">([^<]*)</span>'
    cleanup_stats['color_span'] = len(re.findall(color_span_pattern, content))
    content = re.sub(color_span_pattern, r'\1', content)
    
    # 3.5.3 移除背景色span标签，保留内部文本
    # <span style="background-color:#FFF7E6">xxx</span> → xxx
    bg_span_pattern = r'<span\s+style="background-color:[^"]*">([^<]*)</span>'
    cleanup_stats['bg_span'] = len(re.findall(bg_span_pattern, content))
    content = re.sub(bg_span_pattern, r'\1', content)
    
    # 3.5.4 移除下划线标签，保留内部文本
    # <u>备注</u> → 备注
    underline_pattern = r'<u>([^<]*)</u>'
    cleanup_stats['underline'] = len(re.findall(underline_pattern, content))
    content = re.sub(underline_pattern, r'\1', content)
    
    # 3.5.5 移除@用户ID（飞书用户引用，对AI无意义）
    # @ou_43c1b... 或 @ou_d8d41...
    at_user_pattern = r'@ou_[a-zA-Z0-9]+\.{3}'
    cleanup_stats['at_user'] = len(re.findall(at_user_pattern, content))
    content = re.sub(at_user_pattern, '', content)
    
    # 3.5.6 清理连续空格（删除内容后可能产生）
    content = re.sub(r'  +', ' ', content)
    
    # 3.5.6b 清理连续空行（PNG行删除后产生）
    content = re.sub(r'\n{3,}', '\n\n', content)
    
    # 3.5.7 🆕 移除所有引用块符号（对AI无用的视觉格式）
    # 引用块会在Markdown渲染时显示灰色背景，但AI不需要这种视觉效果
    quote_block_count = content.count('\n> ')
    content = re.sub(r'^>\s*', '', content, flags=re.MULTILINE)  # 移除行首的 >
    content = re.sub(r'\n>\s*', '\n', content)  # 移除换行后的 >
    cleanup_stats['quote_block'] = quote_block_count
    
    total_cleanup = sum(cleanup_stats.values())
    print(f'[DEBUG] 已清理无用格式：{total_cleanup} 处 (删除线:{cleanup_stats["strikethrough"]}, 颜色:{cleanup_stats["color_span"]}, 背景色:{cleanup_stats["bg_span"]}, 下划线:{cleanup_stats["underline"]}, @用户:{cleanup_stats["at_user"]}, 引用块:{cleanup_stats.get("quote_block", 0)})', flush=True)
    
    # 3.6 🆕 通用表格拆解：将超长表格转换为层级章节（解决表格单元格截断问题）
    content, exploded_count = explode_feature_table(content)
    if exploded_count > 0:
        print(f'[DEBUG] 功能表格已拆解：{exploded_count} 个功能点', flush=True)
    
    # 4. 章节过滤已迁移到P01（larkkit下载阶段），此处不再处理
    print('[DEBUG] 章节过滤：已在P01阶段完成（larkkit配置）', flush=True)
    
    # 5. 替换评论文件路径（source_export/*.md，排除 static 目录）
    content = re.sub(
        r'\]\(source_export/([^/]+\.md)\)',
        r'](../P01_req_intake/source_export/\1)',
        content
    )
    print('[DEBUG] 评论文件路径已替换', flush=True)
    
    # 6. 生成标准化 YAML 头部
    yaml_header = generate_yaml_header(source_metadata, str(source_prd))
    
    # 7. 组合最终内容
    final_content = yaml_header + '\n\n---\n\n' + content
    
    # 8. 添加来源追溯
    images_count = final_content.count('> 📎')  # 统计MD引用数量
    summaries_count = final_content.count('> 📎')
    final_content += f'''
---

> **来源**: P01_req_intake/PRD.md
> **图片解析引用**: {images_count}
> **章节过滤**: ✅ 已在P01阶段完成（larkkit配置）
'''
    
    # 9. 写入目标文件
    print('[DEBUG] 写入文件...', flush=True)
    target_prd.write_text(final_content, encoding='utf-8')
    print('[DEBUG] 写入完成', flush=True)
    
    # 10. 完整性校验
    validation = validate_completeness(raw_content, final_content)
    
    # 10. 目录映射（仅用于对账，不生成 TOC）
    # 内联提取标题用于目录映射
    headings = []
    for line in content.split('\n'):
        line = line.strip()
        if not line.startswith('#'):
            continue
        level = 0
        for c in line:
            if c == '#':
                level += 1
            else:
                break
        if level > 4 or level == 0:
            continue
        title = line[level:].strip()
        if not title:
            continue
        anchor = title.lower().replace(' ', '-')
        anchor = ''.join(c for c in anchor if c.isalnum() or c == '-' or '\u4e00' <= c <= '\u9fff')
        headings.append((level, title, anchor))
    
    mapping_report = generate_mapping_report(headings)
    
    # 11. 生成目录对账表文件
    mapping_table = generate_mapping_table(mapping_report)
    
    # 12. 关键章节完整性检测
    critical_check = check_critical_sections(headings)
    
    return {
        'success': True,
        'source': str(source_prd),
        'target': str(target_prd),
        'original_lines': original_lines,
        'final_lines': len(final_content.split('\n')),
        'images_count': images_count,
        'validation': validation,
        'mapping': mapping_report,
        'mapping_table': mapping_table,
        'critical_check': critical_check,
    }


# 关键章节定义（缺失时输出警告）
CRITICAL_SECTIONS = [
    '需求背景',      # 需求背景/目标
    '产品介绍',
    '用户痛点',      # 用户痛点分析
    '关键解法',
    '产品方案',
    '业务逻辑',      # 业务逻辑/流程
    '系统流程图',
    '产品功能清单',  # 产品功能清单及功能优先级
    '高危元素',      # 高危元素（重要）
]


def check_critical_sections(headings: list) -> dict:
    """检测关键章节是否缺失
    
    Returns:
        dict: {'missing': [...], 'found': [...]}
    """
    # 将所有标题合并为一个字符串用于模糊匹配
    all_titles = ' '.join([title for _, title, _ in headings]).lower()
    
    missing = []
    found = []
    
    for section in CRITICAL_SECTIONS:
        # 模糊匹配：检查关键词是否出现在任一标题中
        if section.lower() in all_titles:
            found.append(section)
        else:
            missing.append(section)
    
    return {'missing': missing, 'found': found}


def generate_mapping_table(mapping_report: dict) -> str:
    """生成 Markdown 格式的目录对账表"""
    lines = [
        '## 目录对账表 (Directory Mapping)',
        '',
        '| 原始章节 | 目标章节 | 映射方式 | 置信度 |',
        '|:---|:---|:---|:---|'
    ]
    
    for m in mapping_report['mappings']:
        status = '✅' if m['match_type'] != 'UNMATCHED' else '❌'
        lines.append(f"| {status} {m['raw_section']} | {m['target_section']} | {m['match_type']} | {m['confidence']} |")
    
    stats = mapping_report['stats']
    lines.extend([
        '',
        '**统计**：',
        f"- 总章节数：{stats['total']}",
        f"- L1 精确匹配：{stats['l1_matched']}",
        f"- L3 归并匹配：{stats['l3_matched']}",
        f"- 未匹配：{stats['unmatched']}"
    ])
    
    return '\n'.join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    feature_path = sys.argv[1]
    print(f'🚀 PRD 标准化开始...', flush=True)
    print(f'📂 特性目录: {feature_path}', flush=True)
    
    result = standardize_prd(feature_path)
    
    if result['success']:
        validation = result['validation']
        checks = validation['checks']
        mapping = result['mapping']
        
        print(f'''
✅ 标准化完成！

📊 统计信息：
  - 源文件: {result['source']}
  - 目标文件: {result['target']}
  - 原始行数: {result['original_lines']}
  - 最终行数: {result['final_lines']}
  - 📎 图片解析引用: {result['images_count']} 个

🔍 完整性校验：''')
        for key, check in checks.items():
            status = '✅' if check['pass'] else '❌'
            print(f"  - {key}: {check['source']} → {check['target']} {status}")
        
        # 章节过滤已迁移到P01阶段
        print('\n 章节过滤：已在P01阶段完成（配置：~/.vaf/config [download] ignore_sections）')
        
        # 目录映射结果
        stats = mapping['stats']
        print(f'''
📋 目录映射：
  - 总章节数: {stats['total']}
  - L1 精确匹配: {stats['l1_matched']} ✅
  - L3 归并匹配: {stats['l3_matched']} 🔶
  - 未匹配: {stats['unmatched']} ❌''')
        
        # 显示未匹配项
        unmatched_items = [m for m in mapping['mappings'] if m['match_type'] == 'UNMATCHED']
        if unmatched_items:
            print('\n⚠️ 未匹配章节（需人工确认）：')
            for item in unmatched_items:
                print(f"  - ❌ {item['raw_section']}")
        
        # 关键章节完整性检测
        critical_check = result.get('critical_check', {})
        missing_critical = critical_check.get('missing', [])
        if missing_critical:
            print(f'\n🚨 关键章节缺失警告（可能由 ignore 配置导致）：')
            for section in missing_critical:
                print(f"  - ⚠️ 缺失: {section}")
            print('  💡 提示: 若为有意忽略，可忽略此警告；否则请检查 P01 的 ignore 配置')
        
        # 综合判定
        if validation['all_pass'] and stats['unmatched'] == 0 and not missing_critical:
            print('\n🎉 所有校验通过，目录映射完整！')
        elif validation['all_pass'] and not missing_critical:
            print(f"\n⚠️ 内容校验通过，但有 {stats['unmatched']} 个章节未匹配模板")
        elif validation['all_pass']:
            print(f"\n⚠️ 内容校验通过，但有 {len(missing_critical)} 个关键章节缺失")
        else:
            print('\n⚠️ 存在校验未通过项，请检查')
        
        # 输出目录对账表
        print('\n' + '='*50)
        print(result['mapping_table'])
    else:
        print(f'❌ 标准化失败: {result["error"]}')
        sys.exit(1)


if __name__ == '__main__':
    main()
