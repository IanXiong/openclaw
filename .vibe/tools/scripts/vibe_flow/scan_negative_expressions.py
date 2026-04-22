#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
负面表达扫描+分类+报告工具 v1.0

功能概述:
    扫描 Prompt 文件中的负面表达，按类型分类，生成改造建议报告。
    
    与 convert_to_positive.py 的区别：
    - 本工具只扫描和分类，不自动替换
    - 区分安全类（保留负面）和非安全类（建议转正面）
    - 生成 Markdown 报告供人工决策

使用方法:
    python scan_negative_expressions.py
    python scan_negative_expressions.py --prompt-dir .vibe/core/prompts
    python scan_negative_expressions.py --output docs/negative_expression_report.md

输出:
    negative_expression_report.md - 负面表达改造建议报告
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
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Tuple
import logging

logger = logging.getLogger("scan_negative_expressions")


# ============================================================================
# 负面表达模式定义
# ============================================================================

# 负面关键词
NEGATIVE_KEYWORDS = [
    "禁止", "严禁", "不要", "不能", "不得", "不可", "避免", "切勿", "勿"
]

# 负面表达正则模式
NEGATIVE_PATTERNS = [
    r'[❌\-\*]\s*(严禁|禁止|不要|不得|不可|避免)[^。\n]+',
    r'(严禁|禁止)[^。\n]{2,50}',
    r'(不要|不能|不得)[^。\n]{2,50}',
    r'避免[^。\n]{2,30}',
]


# ============================================================================
# 分类规则定义
# ============================================================================

# 分类关键词映射
CATEGORY_KEYWORDS = {
    # 安全类：应保留负面表达
    'security': [
        '编造', '伪造', '凭空', '假设', '捏造', '虚构',
        '删除', '覆盖', '清空', '销毁',
        '泄露', '暴露', '硬编码',
        '幻觉', '臆想', '推测',
    ],
    # 反幻觉类：应保留负面表达
    'anti_hallucination': [
        '来源', '依据', '证据', '追溯',
        '无据', '无依据', '无来源',
    ],
    # 输出规范类：建议转正面 + 正例
    'output': [
        '输出', '生成', '返回', '显示', '展示',
        'JSON', 'Schema', '完整', '详细',
    ],
    # 粒度控制类：建议量化
    'granularity': [
        '过细', '过粗', '太多', '太少', '太长', '太短',
        '拆分', '合并', '粒度',
    ],
    # 格式规范类：建议提供正例
    'format': [
        '格式', '结构', '模板', '章节', '标题',
        '缩进', '换行', '空格',
    ],
    # 流程规范类：建议明确正确做法
    'process': [
        '跳过', '忽略', '遗漏', '省略',
        '直接', '立即', '马上',
    ],
}

# 分类对应的处理建议
CATEGORY_ACTIONS = {
    'security': {
        'action': 'keep_negative',
        'reason': '安全类约束，保留负面表达更有威慑力',
        'emoji': '🔒',
    },
    'anti_hallucination': {
        'action': 'keep_negative',
        'reason': '反幻觉约束，保留负面表达防止 AI 编造',
        'emoji': '🛡️',
    },
    'output': {
        'action': 'convert_with_example',
        'reason': '输出规范类，建议转正面表达 + 提供正例',
        'emoji': '📝',
    },
    'granularity': {
        'action': 'quantify',
        'reason': '粒度控制类，建议转换为量化标准',
        'emoji': '📏',
    },
    'format': {
        'action': 'provide_template',
        'reason': '格式规范类，建议提供模板或正例',
        'emoji': '📋',
    },
    'process': {
        'action': 'clarify_correct_way',
        'reason': '流程规范类，建议明确正确做法',
        'emoji': '✅',
    },
    'other': {
        'action': 'manual_review',
        'reason': '无法自动分类，需人工审核',
        'emoji': '👀',
    },
}


# ============================================================================
# 扫描器
# ============================================================================

class NegativeExpressionScanner:
    """负面表达扫描器"""
    
    def __init__(self, prompt_dir: Path):
        self.prompt_dir = prompt_dir
        self.results: List[Dict] = []
    
    def scan_file(self, file_path: Path) -> List[Dict]:
        """扫描单个文件"""
        try:
            content = file_path.read_text(encoding='utf-8')
        except Exception as e:
            logger.warning(f"读取文件失败: {file_path}, {e}")
            return []
        
        file_results = []
        lines = content.split('\n')
        
        for line_no, line in enumerate(lines, 1):
            # 跳过代码块内容
            if line.strip().startswith('```') or line.strip().startswith('#!'):
                continue
            
            # 检查负面关键词
            for keyword in NEGATIVE_KEYWORDS:
                if keyword in line:
                    # 提取完整表达（到句号或换行）
                    match = re.search(rf'[^。\n]*{keyword}[^。\n]*', line)
                    if match:
                        text = match.group(0).strip()
                        # 过滤太短的匹配
                        if len(text) < 5:
                            continue
                        # 避免重复
                        if any(r['text'] == text and r['line'] == line_no for r in file_results):
                            continue
                        
                        file_results.append({
                            'file': str(file_path.relative_to(self.prompt_dir.parent.parent)),
                            'file_name': file_path.name,
                            'line': line_no,
                            'text': text,
                            'keyword': keyword,
                            'full_line': line.strip(),
                        })
        
        return file_results
    
    def scan_all(self) -> List[Dict]:
        """扫描所有 Prompt 文件"""
        prompt_files = list(self.prompt_dir.rglob("*.md"))
        
        print(f"📂 扫描目录: {self.prompt_dir}")
        print(f"📄 发现 {len(prompt_files)} 个 Prompt 文件\n")
        
        for file_path in prompt_files:
            file_results = self.scan_file(file_path)
            self.results.extend(file_results)
        
        print(f"🔍 共发现 {len(self.results)} 处负面表达\n")
        return self.results


# ============================================================================
# 分类器
# ============================================================================

class ExpressionClassifier:
    """负面表达分类器"""
    
    def classify(self, expression: Dict) -> Dict:
        """分类单个表达"""
        text = expression['text']
        
        # 按优先级匹配分类
        for category, keywords in CATEGORY_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                expression['category'] = category
                expression['action'] = CATEGORY_ACTIONS[category]['action']
                expression['reason'] = CATEGORY_ACTIONS[category]['reason']
                expression['emoji'] = CATEGORY_ACTIONS[category]['emoji']
                return expression
        
        # 默认分类
        expression['category'] = 'other'
        expression['action'] = CATEGORY_ACTIONS['other']['action']
        expression['reason'] = CATEGORY_ACTIONS['other']['reason']
        expression['emoji'] = CATEGORY_ACTIONS['other']['emoji']
        return expression
    
    def classify_all(self, expressions: List[Dict]) -> List[Dict]:
        """分类所有表达"""
        for expr in expressions:
            self.classify(expr)
        return expressions


# ============================================================================
# 报告生成器
# ============================================================================

class ReportGenerator:
    """改造报告生成器"""
    
    def __init__(self, output_path: Path):
        self.output_path = output_path
    
    def generate(self, classified_results: List[Dict]) -> str:
        """生成 Markdown 报告"""
        
        # 按分类分组
        by_category = defaultdict(list)
        for item in classified_results:
            by_category[item['category']].append(item)
        
        # 按文件分组
        by_file = defaultdict(list)
        for item in classified_results:
            by_file[item['file']].append(item)
        
        report = []
        
        # 标题
        report.append("# Prompt 负面表达改造报告")
        report.append("")
        report.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"> 扫描工具: `scan_negative_expressions.py` v1.0")
        report.append("")
        report.append("---")
        report.append("")
        
        # 统计摘要
        report.append("## 📊 统计摘要")
        report.append("")
        report.append("### 按分类统计")
        report.append("")
        report.append("| 分类 | 数量 | 建议动作 | 说明 |")
        report.append("|:---|:---:|:---|:---|")
        
        category_order = ['security', 'anti_hallucination', 'output', 'granularity', 'format', 'process', 'other']
        for cat in category_order:
            items = by_category.get(cat, [])
            if items:
                action_info = CATEGORY_ACTIONS[cat]
                report.append(f"| {action_info['emoji']} {cat} | {len(items)} | `{action_info['action']}` | {action_info['reason']} |")
        
        report.append("")
        report.append(f"**总计**: {len(classified_results)} 处负面表达")
        report.append("")
        
        # 按文件统计
        report.append("### 按文件统计（按数量排序）")
        report.append("")
        report.append("| 序号 | 文件 | 数量 | 优先级 |")
        report.append("|:---:|:---|:---:|:---:|")
        
        sorted_files = sorted(by_file.items(), key=lambda x: len(x[1]), reverse=True)
        for i, (file_path, items) in enumerate(sorted_files, 1):
            priority = "P0" if len(items) >= 10 else "P1" if len(items) >= 5 else "P2"
            report.append(f"| {i} | `{file_path}` | {len(items)} | {priority} |")
        
        report.append("")
        report.append("---")
        report.append("")
        
        # 分类详情
        report.append("## 📋 分类详情")
        report.append("")
        
        # 保留类（安全 + 反幻觉）
        keep_categories = ['security', 'anti_hallucination']
        keep_items = []
        for cat in keep_categories:
            keep_items.extend(by_category.get(cat, []))
        
        if keep_items:
            report.append("### 🔒 保留负面表达（安全类 + 反幻觉类）")
            report.append("")
            report.append("> 这些表达应**保留负面形式**，因为它们是安全约束或反幻觉约束。")
            report.append("")
            report.append("| 文件 | 行号 | 负面表达 | 分类 |")
            report.append("|:---|:---:|:---|:---|")
            for item in keep_items[:30]:  # 限制显示数量
                text = item['text'][:60] + '...' if len(item['text']) > 60 else item['text']
                report.append(f"| `{item['file_name']}` | {item['line']} | {text} | {item['category']} |")
            if len(keep_items) > 30:
                report.append(f"| ... | ... | *还有 {len(keep_items) - 30} 条* | ... |")
            report.append("")
        
        # 转换类
        convert_categories = ['output', 'granularity', 'format', 'process']
        convert_items = []
        for cat in convert_categories:
            convert_items.extend(by_category.get(cat, []))
        
        if convert_items:
            report.append("### 🔄 建议转换为正面表达")
            report.append("")
            report.append("> 这些表达建议**转换为正面表达**，并提供正例或量化标准。")
            report.append("")
            
            for cat in convert_categories:
                items = by_category.get(cat, [])
                if not items:
                    continue
                
                action_info = CATEGORY_ACTIONS[cat]
                report.append(f"#### {action_info['emoji']} {cat} 类 ({len(items)} 处)")
                report.append("")
                report.append(f"**建议动作**: {action_info['reason']}")
                report.append("")
                report.append("| 文件 | 行号 | 负面表达 | 改造建议 |")
                report.append("|:---|:---:|:---|:---|")
                
                for item in items[:20]:
                    text = item['text'][:50] + '...' if len(item['text']) > 50 else item['text']
                    suggestion = self._get_suggestion(item)
                    report.append(f"| `{item['file_name']}` | {item['line']} | {text} | {suggestion} |")
                
                if len(items) > 20:
                    report.append(f"| ... | ... | *还有 {len(items) - 20} 条* | ... |")
                report.append("")
        
        # 待审核类
        other_items = by_category.get('other', [])
        if other_items:
            report.append("### 👀 需人工审核")
            report.append("")
            report.append("> 这些表达无法自动分类，需要人工判断是否需要改造。")
            report.append("")
            report.append("| 文件 | 行号 | 负面表达 |")
            report.append("|:---|:---:|:---|")
            for item in other_items[:20]:
                text = item['text'][:60] + '...' if len(item['text']) > 60 else item['text']
                report.append(f"| `{item['file_name']}` | {item['line']} | {text} |")
            if len(other_items) > 20:
                report.append(f"| ... | ... | *还有 {len(other_items) - 20} 条* |")
            report.append("")
        
        report.append("---")
        report.append("")
        
        # 改造建议
        report.append("## 💡 改造建议")
        report.append("")
        report.append("### 处理优先级")
        report.append("")
        report.append("| 优先级 | 文件数 | 处理策略 |")
        report.append("|:---:|:---:|:---|")
        
        p0_files = [f for f, items in by_file.items() if len(items) >= 10]
        p1_files = [f for f, items in by_file.items() if 5 <= len(items) < 10]
        p2_files = [f for f, items in by_file.items() if len(items) < 5]
        
        report.append(f"| P0 | {len(p0_files)} | 优先改造，负面表达 ≥10 处 |")
        report.append(f"| P1 | {len(p1_files)} | 次优先，负面表达 5-9 处 |")
        report.append(f"| P2 | {len(p2_files)} | 低优先，负面表达 <5 处 |")
        report.append("")
        
        report.append("### 改造原则")
        report.append("")
        report.append("1. **安全类保留负面**：`严禁编造`、`禁止删除` 等安全约束保留负面表达")
        report.append("2. **输出类转正面+正例**：`禁止输出 JSON` → 提供正确输出格式示例")
        report.append("3. **粒度类量化**：`不要过细` → `控制在 5-10 行`")
        report.append("4. **格式类提供模板**：`禁止格式错误` → 提供正确格式模板")
        report.append("")
        
        report.append("---")
        report.append("")
        report.append(f"**文档版本**: v1.0")
        report.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 写入文件
        content = '\n'.join(report)
        self.output_path.write_text(content, encoding='utf-8')
        
        print(f"✅ 报告已生成: {self.output_path}")
        return content
    
    def _get_suggestion(self, item: Dict) -> str:
        """根据分类生成改造建议"""
        category = item['category']
        text = item['text']
        
        if category == 'output':
            if 'JSON' in text or 'Schema' in text:
                return "提供伪代码示例"
            elif '完整' in text:
                return "提供概要格式示例"
            else:
                return "提供正确输出示例"
        
        elif category == 'granularity':
            if '过细' in text:
                return "量化为 5-10 行"
            elif '过粗' in text:
                return "量化为可执行粒度"
            else:
                return "提供量化标准"
        
        elif category == 'format':
            return "提供正确格式模板"
        
        elif category == 'process':
            if '跳过' in text or '忽略' in text:
                return "明确必须执行的步骤"
            else:
                return "明确正确流程"
        
        return "人工审核"


# ============================================================================
# 主入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='扫描 Prompt 中的负面表达，分类并生成改造报告'
    )
    parser.add_argument(
        '--prompt-dir',
        type=Path,
        default=Path('.vibe/core/prompts'),
        help='Prompt 目录路径 (默认: .vibe/core/prompts)'
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=Path('docs/negative_expression_report.md'),
        help='输出报告路径 (默认: docs/negative_expression_report.md)'
    )
    
    args = parser.parse_args()
    
    # 确定项目根目录
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent.parent
    
    prompt_dir = project_root / args.prompt_dir
    output_path = project_root / args.output
    
    if not prompt_dir.exists():
        print(f"❌ Prompt 目录不存在: {prompt_dir}")
        return 1
    
    print("=" * 60)
    print("负面表达扫描+分类+报告工具 v1.0")
    print("=" * 60)
    print()
    
    # 1. 扫描
    scanner = NegativeExpressionScanner(prompt_dir)
    results = scanner.scan_all()
    
    if not results:
        print("✅ 未发现负面表达")
        return 0
    
    # 2. 分类
    classifier = ExpressionClassifier()
    classified = classifier.classify_all(results)
    
    # 3. 生成报告
    output_path.parent.mkdir(parents=True, exist_ok=True)
    reporter = ReportGenerator(output_path)
    reporter.generate(classified)
    
    print()
    print("=" * 60)
    print("扫描完成！")
    print("=" * 60)
    
    return 0


if __name__ == '__main__':
    exit(main())
