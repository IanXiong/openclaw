# -*- coding: utf-8 -*-
"""Mermaid 语法验证与自修复模块

从 process_assets_with_qwen.py 拆分出来，供 DashScope 和 MiFy 链路共用。

主要功能：
- extract_mermaid_blocks: 从 Markdown 提取 Mermaid 代码块
- validate_mermaid_syntax: 静态语法校验
- validate_and_fix_mermaid: 校验 + LLM 修复
- check_mermaid_syntax: 包装函数，返回 (is_valid, error_count)
- clean_qwen_output: 清洗 Qwen 模型输出
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

import re
import subprocess
from pathlib import Path
from typing import List

# 规则文件路径
SCRIPT_DIR = Path(__file__).resolve().parent

def _resolve_mermaid_rules_path() -> Path:
    """解析 mermaid_rules.md 路径，支持部署和开发环境"""
    script_path = Path(__file__).resolve()
    
    # 检测是否在 .vibe 目录内
    if ".vibe" in script_path.parts:
        # 部署环境: .vibe/tools/scripts/validation/xxx.py
        # parents[4] 是项目根目录
        root = script_path.parents[4]
        candidate = root / ".vibe/core/rules/mermaid_rules.md"
        if candidate.exists():
            return candidate
    else:
        # 开发环境: .vibe/tools/scripts/validation/xxx.py
        # parents[3] 是 repo 根目录
        root = script_path.parents[3]
        candidate = root / ".vibe/core/rules/mermaid_rules.md"
        if candidate.exists():
            return candidate
    
    # 回退：返回预期路径
    return script_path.parents[4] / ".vibe/core/rules/mermaid_rules.md" if len(script_path.parents) > 4 else script_path.parents[3] / ".vibe/core/rules/mermaid_rules.md"

DEFAULT_MERMAID_RULES_PATH = _resolve_mermaid_rules_path()


def extract_mermaid_blocks(md_text: str) -> List[tuple]:
    """
    从 Markdown 文本中提取所有 Mermaid 代码块（支持引用块内的代码块）。
    
    Returns:
        List of (start_pos, end_pos, block_content, is_in_blockquote) tuples
        - is_in_blockquote: 代码块是否在引用块内（每行有 > 前缀）
    """
    # 先尝试匹配普通代码块
    pattern_normal = r'```mermaid\s*\n(.*?)```'
    # 匹配引用块内的代码块（每行以 > 开头，代码块内容行可能有 > 前缀）
    pattern_blockquote = r'>\s*```mermaid\s*\n((?:>.*\n)*?)>\s*```'
    
    matches = []
    
    # 匹配引用块内的代码块
    for match in re.finditer(pattern_blockquote, md_text, flags=re.DOTALL):
        # 提取代码内容并移除每行的 > 前缀
        raw_content = match.group(1)
        # 移除每行开头的 > 和可选空格
        clean_content = re.sub(r'^>\s?', '', raw_content, flags=re.MULTILINE)
        matches.append((match.start(), match.end(), clean_content, True))
    
    # 匹配普通代码块（排除已匹配的引用块内代码块）
    blockquote_ranges = [(m[0], m[1]) for m in matches]
    for match in re.finditer(pattern_normal, md_text, flags=re.DOTALL):
        # 检查是否与引用块代码块重叠
        is_overlap = any(
            not (match.end() <= bq_start or match.start() >= bq_end)
            for bq_start, bq_end in blockquote_ranges
        )
        if not is_overlap:
            matches.append((match.start(), match.end(), match.group(1), False))
    
    # 按位置排序
    matches.sort(key=lambda x: x[0])
    return matches


def validate_mermaid_syntax(mermaid_code: str) -> tuple:
    """
    验证 Mermaid 语法是否正确（基于静态规则检查）。
    
    检查项（GitHub/Cursor IDE 兼容性）：
    1. 图表类型声明是否存在且有效
    2. 节点定义是否完整（括号匹配）
    3. 边定义是否完整（箭头后有目标节点）
    4. 行尾是否有注释（Mermaid 不支持行尾注释）
    5. 保留字冲突检查
    6. 中文标签是否加引号
    7. flowchart 中禁止的 note 语法
    8. URL/路径未加引号检测（新增）
    9. [[...]] 节点形状语法错误检测（新增）
    10. 空标签检测（新增）
    11. 子图名称未加引号检测（新增）
    12. Markdown 语法检测（新增）
    13. 内部引号未转义检测（新增）
    
    Returns:
        (is_valid: bool, errors: List[str])
    """
    errors = []
    stripped_code = mermaid_code.strip()
    
    # 检测空代码块
    if not stripped_code:
        return False, ["空的 Mermaid 代码块"]
    
    lines = stripped_code.split('\n')
    
    # 检查图表类型声明
    first_non_empty = next((l.strip() for l in lines if l.strip() and not l.strip().startswith('%%')), '')
    valid_types = ['flowchart', 'graph', 'sequenceDiagram', 'classDiagram', 'stateDiagram', 
                   'erDiagram', 'journey', 'gantt', 'pie', 'mindmap', 'timeline', 'gitGraph']
    has_valid_type = any(first_non_empty.startswith(t) for t in valid_types)
    if not has_valid_type:
        errors.append(f"缺少有效的图表类型声明，首行: '{first_non_empty[:50]}...'")
    
    # 检测图表类型，用于后续跳过特定语法检查
    diagram_type = None
    for t in valid_types:
        if first_non_empty.startswith(t):
            diagram_type = t
            break
    
    # 跳过特定图表类型的括号检查（它们有自己的语法）
    skip_bracket_check_types = ['classDiagram', 'erDiagram', 'sequenceDiagram', 'gantt', 'pie', 'journey']
    skip_bracket_check = diagram_type in skip_bracket_check_types
    
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith('%%'):
            continue

        # class 语句多节点未用逗号分隔时，Mermaid 解析会失败
        class_match = re.match(r'^class\s+(.+?)\s+note;?$', stripped)
        if class_match:
            class_body = class_match.group(1)
            if ' ' in class_body and ',' not in class_body:
                errors.append(
                    f"第 {i} 行: class 多节点需用逗号分隔 - '{stripped[:60]}...'"
                )
        
        # 检查行尾注释（头号杀手）- 仅对 flowchart/graph 严格检查
        if diagram_type in ['flowchart', 'graph'] and re.search(r'\S\s+%%', line):
            errors.append(f"第 {i} 行: 行尾注释会导致渲染失败 - '{line.strip()[:60]}...'")
        
        # 检查 flowchart 中禁止的 note 语法（note left/right/over ... end）
        if diagram_type in ['flowchart', 'graph']:
            # 匹配 note 开始行：note left of X / note right of X / note over X
            if re.match(r'^note\s+(left|right|over)\s+', stripped, re.IGNORECASE):
                errors.append(f"第 {i} 行: flowchart 不支持 note 语法，请改用备注节点 - '{stripped[:60]}...'")
        
        # 检查不完整的边定义（箭头后没有目标节点）
        if re.search(r'(-->|-.->|==>|--?>)\s*\|\s*$', stripped):
            errors.append(f"第 {i} 行: 边定义不完整，箭头后缺少目标节点 - '{stripped[:60]}...'")
        
        # 检查未闭合的边标签（仅检查以 | 结尾且包含箭头的行）
        if stripped.endswith('|') and re.search(r'(-->|-.->|==>)', stripped):
            errors.append(f"第 {i} 行: 边标签未闭合 - '{stripped[:60]}...'")
        
        # 跳过特定图表类型的括号检查
        if skip_bracket_check:
            continue
        
        # 移除引号内的内容后再检查括号匹配（避免 ["a]b"] 被误判）
        line_without_quotes = re.sub(r'"[^"]*"', '""', stripped)
        
        # 检查括号匹配（简单检查）
        if line_without_quotes.count('[') != line_without_quotes.count(']'):
            # 排除 subgraph 等特殊情况
            if not stripped.startswith('subgraph') and not stripped.startswith('end'):
                errors.append(f"第 {i} 行: 方括号不匹配 - '{stripped[:60]}...'")
        
        if line_without_quotes.count('{') != line_without_quotes.count('}'):
            if not stripped.startswith('classDef') and not stripped.startswith('style'):
                errors.append(f"第 {i} 行: 花括号不匹配 - '{stripped[:60]}...'")
        
        # 检查保留字冲突
        if re.search(r'\bclassDef\s+end\b', stripped):
            errors.append(f"第 {i} 行: 'end' 是保留字，不能用作类名")
        
        # 检查中文标签未加引号（仅对 flowchart/graph 检查）
        if diagram_type in ['flowchart', 'graph']:
            # 检查方括号节点 [中文]
            if re.search(r'\[[^\]"]+[\u4e00-\u9fff][^\]"]*\]', stripped):
                errors.append(f"第 {i} 行: 中文标签建议加引号以提高兼容性 - '{stripped[:60]}...'")
            # 检查菱形节点 {中文}
            if re.search(r'\{[^\}"]+[\u4e00-\u9fff][^\}"]*\}', stripped):
                errors.append(f"第 {i} 行: 中文标签建议加引号以提高兼容性 - '{stripped[:60]}...'")
            # 检查圆括号节点 (中文)
            if re.search(r'\([^)"]+[\u4e00-\u9fff][^)"]*\)', stripped):
                errors.append(f"第 {i} 行: 中文标签建议加引号以提高兼容性 - '{stripped[:60]}...'")
        
        # ========== 新增检测规则（方案 A）==========
        
        # 8. URL/路径未加引号检测 - [/api/xxx] 会触发 Lexical error
        if diagram_type in ['flowchart', 'graph']:
            # 匹配 [/path] 或 [http://] 等未加引号的 URL/路径
            if re.search(r'\[[^"\]]*\/[^"\]]*\]', stripped):
                # 排除已加引号的情况 ["..."]
                if not re.search(r'\["[^"]*\/[^"]*"\]', stripped):
                    errors.append(f"第 {i} 行: URL/路径必须加引号 - '{stripped[:60]}...'")
        
        # 9. [[...]] 节点形状语法错误检测
        if diagram_type in ['flowchart', 'graph']:
            # 检测混合写法如 [[DB] xmstore] 或 [[xxx]（缺少闭合）
            if re.search(r'\[\[[^\]]+\][^\]]', stripped):
                errors.append(f"第 {i} 行: [[...]] 语法错误，子程序用 [[\"文本\"]]，数据库用 [(\"文本\")] - '{stripped[:60]}...'")
            # 检测 [[ 后没有正确闭合 ]]
            if '[[' in stripped and ']]' not in stripped:
                errors.append(f"第 {i} 行: [[ 未正确闭合，需要 ]] - '{stripped[:60]}...'")
        
        # 10. 空标签检测 - -- "" --> 会导致 Parse Error
        if re.search(r'--\s*""\s*-->', stripped):
            errors.append(f"第 {i} 行: 空标签会导致解析错误，请直接使用 --> - '{stripped[:60]}...'")
        
        # 11. 子图名称未加引号检测
        if re.match(r'^subgraph\s+[^\s"]+[\u4e00-\u9fff]', stripped):
            errors.append(f"第 {i} 行: subgraph 中文名称必须加引号 - '{stripped[:60]}...'")
        
        # 12. Markdown 语法检测 - ["**加粗**"] 会渲染异常
        if re.search(r'\["\*\*[^"]+\*\*"\]', stripped):
            errors.append(f"第 {i} 行: 节点文本禁止 Markdown 语法，请用 <b></b> 替代 **...** - '{stripped[:60]}...'")
        if re.search(r'\["\*[^*"]+\*"\]', stripped):
            errors.append(f"第 {i} 行: 节点文本禁止 Markdown 语法，请用 <i></i> 替代 *...* - '{stripped[:60]}...'")
        
        # 13. 内部引号未转义检测 - ["状态: "进行中""] 会解析失败
        # 检测引号内有未转义的引号（排除正确转义的 \"）
        quote_content_match = re.search(r'\["([^"\\]|\\.)+"', stripped)
        if quote_content_match:
            content = quote_content_match.group(0)
            # 检查是否有未转义的内部引号
            inner_content = content[2:-1]  # 去掉 [" 和 "
            if '"' in inner_content and '\\"' not in inner_content:
                errors.append(f"第 {i} 行: 内部引号需转义为 \\\" - '{stripped[:60]}...'")
    
    return len(errors) == 0, errors


def fix_mermaid_with_llm(
    broken_code: str, 
    errors: List[str], 
    rules: str, 
    model: str, 
    api_key: str,
    timeout: int = 60
) -> str:
    """
    使用 LLM 根据规则文档自动修复 Mermaid 代码。
    
    注意：此函数依赖 dashscope 库，仅在 enable_llm=True 时被调用。
    """
    # 延迟导入 dashscope，避免 MiFy 链路不必要的依赖
    try:
        import dashscope
    except ImportError:
        print("Warning: dashscope not installed, LLM fix disabled", file=sys.stderr)
        return broken_code
    
    error_report = "\n".join([f"- {e}" for e in errors])
    prompt = f"""
你是一个 Mermaid 代码修复专家。
检测到以下 Mermaid 代码存在语法错误，请根据提供的规则进行修复。

### 错误报告
{error_report}

### 原始代码
```mermaid
{broken_code}
```

### 必须遵守的 Mermaid 规则
{rules}

### 任务要求
1. 仅输出修复后的 Mermaid 代码块。
2. 不要包含任何解释性文字。
3. 确保修复了所有报告的错误。
4. 保持原有业务逻辑不变。
"""
    
    messages = [{"role": "user", "content": [{"text": prompt}]}]
    
    try:
        response = dashscope.MultiModalConversation.call(
            api_key=api_key,
            model=model,
            messages=messages,
            timeout=timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(f"API error: {response.status_code}")
        return response.output.choices[0].message.content[0]["text"]
    except Exception as e:
        print(f"Warning: LLM fix failed: {e}", file=sys.stderr)
        return broken_code


def validate_and_fix_mermaid(
    md_text: str, 
    model: str, 
    api_key: str,
    mermaid_rules: str,
    max_retries: int = 2,
    enable_llm: bool = True,
    enable_node_check: bool = False,
    node_check_script: str | None = None
) -> str:
    """
    验证并自动修复 Markdown 文本中的所有 Mermaid 代码块。
    
    流程：
    1. 提取所有 Mermaid 代码块
    2. 对每个代码块进行语法验证（本地正则）
    3. 如果验证失败，调用 LLM 进行基于规则的自愈（Self-Correction）
    4. 重复验证直到通过或达到最大重试次数
    """
    blocks = extract_mermaid_blocks(md_text)
    
    if not blocks:
        return md_text
    
    result = md_text
    
    # 从后往前替换，避免索引偏移
    for block_tuple in reversed(blocks):
        start, end, block_content = block_tuple[0], block_tuple[1], block_tuple[2]
        is_in_blockquote = block_tuple[3] if len(block_tuple) > 3 else False
        
        current_code = block_content
        is_valid, errors = validate_mermaid_syntax(current_code)

        if enable_node_check and node_check_script:
            try:
                completed = subprocess.run(
                    ["node", node_check_script],
                    input=current_code.encode("utf-8"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False
                )
                if completed.returncode != 0:
                    errors.append(completed.stdout.decode("utf-8", errors="ignore").strip() or "Node 校验失败")
                    is_valid = False
            except FileNotFoundError:
                errors.append("未找到 node 或校验脚本，跳过 Node 校验")
                is_valid = False
            except Exception as exc:  # pragma: no cover
                errors.append(f"Node 校验异常: {exc}")
                is_valid = False

        if is_valid:
            continue
        
        # 如果禁用 LLM 修复，则跳过自愈循环
        if not enable_llm:
            print(f"    [AutoFix] 发现 Mermaid 语法错误（{len(errors)} 个），已禁用 LLM 自动修复，跳过", flush=True)
            continue
        
        # LLM 自愈循环
        for i in range(max_retries):
            print(f"    [AutoFix] 发现 Mermaid 语法错误，正在尝试第 {i+1}/{max_retries} 次 LLM 修复...", flush=True)
            
            # 调用 LLM 修复
            fixed_code_raw = fix_mermaid_with_llm(
                current_code, 
                errors, 
                mermaid_rules, 
                model, 
                api_key
            )
            
            # 提取返回内容中的代码块
            fixed_blocks = extract_mermaid_blocks(fixed_code_raw)
            if fixed_blocks:
                fixed_code = fixed_blocks[0][2]
            else:
                fixed_code = fixed_code_raw.replace("```mermaid", "").replace("```", "").strip()

            is_fixed, new_errors = validate_mermaid_syntax(fixed_code)
            
            if is_fixed:
                current_code = fixed_code
                print("    [AutoFix] 修复成功！")
                break
            
            current_code = fixed_code
            errors = new_errors
            print(f"    [AutoFix] 修复未完全成功，剩余错误: {len(errors)}", flush=True)
        
        # 替换原代码块
        if current_code != block_content:
            # 注意：由于是倒序遍历，字符串替换需要精确定位
            if is_in_blockquote:
                # 引用块内的代码块：每行需要添加 > 前缀
                code_lines = current_code.rstrip('\n').split('\n')
                quoted_code = '\n'.join(f'> {line}' for line in code_lines)
                replacement = f"> ```mermaid\n{quoted_code}\n> ```"
            else:
                replacement = f"```mermaid\n{current_code}```"
            result = result[:start] + replacement + result[end:]
    
    return result


def check_mermaid_syntax(md_text: str) -> tuple[bool, int]:
    """
    检查 Markdown 文本中所有 Mermaid 代码块的语法。
    
    Returns:
        tuple: (all_valid, error_count) - 是否全部有效，错误总数
    """
    blocks = extract_mermaid_blocks(md_text)
    if not blocks:
        return True, 0
    
    total_errors = 0
    for block_tuple in blocks:
        block_content = block_tuple[2]
        is_valid, errors = validate_mermaid_syntax(block_content)
        if not is_valid:
            total_errors += len(errors)
    
    return total_errors == 0, total_errors


def clean_qwen_output(md_text: str) -> str:
    """
    清洗 Qwen 模型输出，移除前置确认文字。
    
    处理模式：
    - "已收到 X 张图片，开始分析..."
    - 其后可能跟随的 "---" 分隔线
    """
    # 匹配 "已收到 X 张图片，开始分析..." 及其后的分隔线
    pattern = r'^已收到\s*\d+\s*张图片[，,]?\s*开始分析[.。…]*\s*[\r\n]*[-—]{2,}[\r\n]*'
    cleaned = re.sub(pattern, '', md_text, flags=re.MULTILINE)
    return cleaned.strip()
