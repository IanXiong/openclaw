#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch process Feishu static assets with Qwen3-VL-Plus.

Note: For the new neutral entry, see .vibe/tools/scripts/process_assets_with_vlm.py

Usage example:
    tools/process_assets_with_qwen.py \
        --input docs/features/<feature>/01_requirements_processing/source_export/static \
        --output docs/features/<feature>/01_requirements_processing/assets_summary.md
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
import os
import re
import json
import threading
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Sequence, Set

# 禁用代理（如果遇到 ProxyError）
def disable_proxy_if_needed(disable: bool = False):
    """禁用代理设置，解决 ProxyError 问题"""
    if disable:
        os.environ['NO_PROXY'] = '*'
        os.environ['no_proxy'] = '*'
        # 清空代理环境变量
        for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
            if key in os.environ:
                del os.environ[key]

def _resolve_resource(anchor_path: Path, relative_path_in_vibe: str, relative_path_in_core: str) -> Path:
    """
    Resolve resource path supporting both deployed environment (.vibe) and dev environment (core).
    
    Args:
        anchor_path: Current script path (__file__)
        relative_path_in_vibe: Path relative to project root in deployed env
                              (e.g. ".vibe/core/prompts/tool_usage/xxx.md")
        relative_path_in_core: Path relative to repo root in dev env
                              (e.g. ".vibe/core/prompts/tool_usage/xxx.md")
    """
    # Script is at:
    # - Deployed: .vibe/tools/scripts/vlm/xxx.py -> parents[4] is project root
    # - Dev: .vibe/tools/scripts/vlm/xxx.py -> parents[3] is repo root
    script_path = anchor_path.resolve()
    
    # Detect environment by checking if we're inside .vibe directory
    if ".vibe" in script_path.parts:
        # Deployed environment: .vibe/tools/scripts/vlm/xxx.py
        # parents[4] is project root (containing .vibe)
        root = script_path.parents[4]
        candidate = root / relative_path_in_vibe
        if candidate.exists():
            return candidate
    else:
        # Dev environment: .vibe/tools/scripts/vlm/xxx.py
        # parents[3] is repo root
        root = script_path.parents[3]
        candidate = root / relative_path_in_core
        if candidate.exists():
            return candidate
    
    # Final fallback: try both paths
    for parent_level in [4, 3]:
        if parent_level < len(script_path.parents):
            root = script_path.parents[parent_level]
            for rel_path in [relative_path_in_vibe, relative_path_in_core]:
                candidate = root / rel_path
                if candidate.exists():
                    return candidate
    
    # Return expected path for error reporting
    return script_path.parents[4] / relative_path_in_vibe if len(script_path.parents) > 4 else script_path.parents[3] / relative_path_in_core

try:
    import dashscope
except ModuleNotFoundError as exc:  # pragma: no cover
    # Try to find requirements.txt to suggest installation
    req_path = _resolve_resource(
        Path(__file__),
        ".vibe/tools/requirements.txt",
        "tools/requirements.txt"
    )
    install_cmd = f"pip install -r {req_path}" if req_path.exists() else "pip install dashscope>=1.25.1"
    
    print(f"错误: 未找到 dashscope 库。", file=sys.stderr)
    print(f"请执行以下命令安装依赖：\n\n    {install_cmd}\n", file=sys.stderr)
    raise SystemExit(1) from exc


SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
DEFAULT_PROMPT_PATH = _resolve_resource(
    Path(__file__),
    ".vibe/core/prompts/tool_usage/qwen_vl_analysis.md",
    ".vibe/core/prompts/tool_usage/qwen_vl_analysis.md"
)
# DEFAULT_MERMAID_RULES_PATH 从 mermaid_validator 导入
DEFAULT_DETAILS_DIR_NAME = "assets_analysis"

# Import unified config module
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "infra"))
from vaf_config import get_dashscope_api_key

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="使用 Qwen3-VL-Plus 批量解析飞书导出的图片资产")
    parser.add_argument("--input", required=True, help="static 目录路径，脚本会递归扫描图片")
    parser.add_argument(
        "--output",
        required=True,
        help="汇总输出文件（assets_summary.md）的路径",
    )
    parser.add_argument(
        "--prompt",
        default=str(DEFAULT_PROMPT_PATH),
        help=f"提示词 Markdown 文件路径（默认: {DEFAULT_PROMPT_PATH}）",
    )
    parser.add_argument(
        "--model",
        default="qwen3-vl-plus",
        help="Qwen 模型名称，默认 qwen3-vl-plus",
    )
    parser.add_argument(
        "--details-dir",
        default=None,
        help=(
            "单图解析结果保存目录；默认位于输出文件同级目录下的"
            f" `{DEFAULT_DETAILS_DIR_NAME}/`"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="本次最多解析多少张图片（按排序后的顺序），默认全部",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="DashScope API Key；若不指定则尝试从环境变量 DASHSCOPE_API_KEY 读取，否则使用内置默认 Key",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="根据 progress 文件跳过已完成的图片，支持断点续跑",
    )
    parser.add_argument(
        "--progress-file",
        default=None,
        help="进度文件路径，默认与 --output 同名并附加 .progress.json",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help=(
            "关键日志输出文件；默认写入 output 同级目录，命名为 qwen_parse_<timestamp>.log"
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="并发处理的线程数，默认 10",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="API 请求超时时间（秒），默认 300 秒",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="禁用代理（解决 ProxyError 问题），如果遇到代理连接错误，使用此选项",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="单张图片解析失败时的最大重试次数，默认 3 次",
    )
    return parser.parse_args()


def load_prompt(prompt_path: Path) -> str:
    if not prompt_path.exists():
        raise FileNotFoundError(f"提示词文件不存在: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def load_mermaid_rules(rules_path: Path) -> str:
    if not rules_path.exists():
        raise FileNotFoundError(f"Mermaid 规则文件不存在: {rules_path}")
    return rules_path.read_text(encoding="utf-8").strip()


def discover_images(static_dir: Path) -> List[Path]:
    if not static_dir.exists():
        raise FileNotFoundError(f"图片目录不存在: {static_dir}")
    results: List[Path] = []
    for path in static_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTS:
            results.append(path)
    if not results:
        raise FileNotFoundError(f"目录 {static_dir} 内未发现受支持的图片文件")
    results.sort()
    return results


def resolve_api_key(explicit: str | None) -> str:
    """
    解析 API Key，优先级：
    1. 命令行参数 --api-key
    2. 环境变量 DASHSCOPE_API_KEY
    3. ~/.vaf/config 配置文件
    """
    api_key = get_dashscope_api_key(explicit)
    if not api_key:
        print("错误: DashScope API Key 未配置。", file=sys.stderr)
        print("请配置 ~/.vaf/config 或设置环境变量 DASHSCOPE_API_KEY", file=sys.stderr)
        print("详见: docs/configuration.md", file=sys.stderr)
        raise SystemExit(1)
    return api_key


class RateLimitError(Exception):
    """API 限流错误"""
    pass


def call_dashscope(messages: list, model: str, api_key: str, timeout: int = 120) -> str:
    """调用 DashScope API 的通用方法"""
    response = dashscope.MultiModalConversation.call(
        api_key=api_key,
        model=model,
        messages=messages,
        timeout=timeout,
    )
    if response.status_code != 200:
        error_code = getattr(response, 'code', 'Unknown')
        error_msg = getattr(response, 'message', 'Unknown error')
        
        # 检测限流错误（429 或特定错误码）
        if response.status_code == 429 or 'rate' in error_msg.lower() or 'limit' in error_msg.lower() or 'throttl' in error_msg.lower():
            raise RateLimitError(f"[限流] HTTP {response.status_code}: {error_code} - {error_msg}")
        
        raise RuntimeError(f"[API错误] HTTP {response.status_code}: {error_code} - {error_msg}")
    return response.output.choices[0].message.content[0]["text"]


def call_qwen(image_path: Path, prompt: str, model: str, api_key: str, timeout: int = 120) -> str:
    messages = [
        {
            "role": "user",
            "content": [
                {"image": str(image_path)},
                {"text": prompt},
            ],
        }
    ]
    return call_dashscope(messages, model, api_key, timeout)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_progress(progress_path: Path) -> Set[str]:
    if not progress_path.exists():
        return set()
    try:
        data = json.loads(progress_path.read_text(encoding="utf-8"))
        return set(data)
    except json.JSONDecodeError:
        return set()


def save_progress(progress_path: Path, items: Set[str]) -> None:
    ensure_parent(progress_path)
    progress_path.write_text(json.dumps(sorted(items), ensure_ascii=False, indent=2), encoding="utf-8")


class ThreadSafeProgress:
    """线程安全的进度管理器"""
    def __init__(self, progress_path: Path, resume: bool = False):
        self.progress_path = progress_path
        self.processed: Set[str] = load_progress(progress_path) if resume else set()
        self.lock = threading.Lock()
    
    def is_processed(self, key: str) -> bool:
        with self.lock:
            return key in self.processed
    
    def mark_processed(self, key: str) -> None:
        with self.lock:
            self.processed.add(key)
            save_progress(self.progress_path, self.processed)
    
    def get_all(self) -> Set[str]:
        with self.lock:
            return self.processed.copy()


class ThreadSafeLogger:
    """线程安全的日志记录器"""
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.lock = threading.Lock()
        ensure_parent(log_path)
    
    def log(self, message: str, show_console: bool = True) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        with self.lock:
            with self.log_path.open("a", encoding="utf-8") as fp:
                fp.write(line)
        if show_console:
            with self.lock:
                print(line, end="")


# ============================================================================
# Mermaid 校验模块（从 mermaid_validator.py 导入）
# ============================================================================
from mermaid_validator import (
    extract_mermaid_blocks,
    validate_mermaid_syntax,
    validate_and_fix_mermaid,
    check_mermaid_syntax,
    clean_qwen_output,
    DEFAULT_MERMAID_RULES_PATH,
)


def save_detail(
    md_text: str, 
    image_path: Path, 
    details_dir: Path,
    model: str,
    api_key: str,
    mermaid_rules: str,
    skip_llm_fix: bool = False,
    llm_max_retries: int = 3
) -> Path:
    """
    保存单图解析结果，包含 Mermaid 语法验证与自动修复。
    
    Args:
        skip_llm_fix: 如果为 True，跳过 LLM 修复（用于重新解析策略）
        llm_max_retries: LLM 修复的最大重试次数，默认 3 次
    """
    rel_name = image_path.stem + ".md"
    detail_path = details_dir / rel_name
    ensure_parent(detail_path)
    
    # 清洗前置文字
    cleaned_text = clean_qwen_output(md_text)
    
    # Mermaid 语法验证与自动修复（可选 LLM 驱动）
    fixed_text = validate_and_fix_mermaid(
        cleaned_text,
        model=model,
        api_key=api_key,
        mermaid_rules=mermaid_rules,
        max_retries=llm_max_retries,  # LLM 修复重试次数
        enable_llm=not skip_llm_fix   # 根据参数决定是否启用 LLM 修复
    )
    
    detail_path.write_text(fixed_text, encoding="utf-8")
    return detail_path


def build_summary_table(rows: Sequence[dict], base_dir: Path) -> str:
    mode_label = "dashscope"
    lines = [
        "# 资产解析汇总",
        "",
        "| 序号 | 图片 | 结果文件 | 模式/模型 | 备注 |",
        "| --- | --- | --- | --- | --- |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        img_rel = os.path.relpath(row["image"], base_dir)
        detail_rel = os.path.relpath(row["detail"], base_dir)
        note = row.get("note", "解析完成")
        lines.append(
            f"| {row['index']} | `{img_rel}` | [查看]({detail_rel}) | {mode_label} | {note} |"
        )
    return "\n".join(lines) + "\n"


def build_rows_from_paths(paths: Sequence[str], details_dir: Path) -> List[dict]:
    rows = []
    for idx, key in enumerate(sorted(paths), start=1):
        img_path = Path(key)
        detail_path = details_dir / f"{img_path.stem}.md"
        note = "解析完成" if detail_path.exists() else "结果缺失，需复核"
        rows.append({"index": idx, "image": img_path, "detail": detail_path, "note": note})
    return rows


def process_single_image(
    image: Path,
    prompt: str,
    mermaid_rules: str,  # 新增参数
    model: str,
    api_key: str,
    details_dir: Path,
    logger: ThreadSafeLogger,
    progress: ThreadSafeProgress,
    index: int,
    total: int,
    timeout: int = 120,
    max_retries: int = 3,  # API 调用失败的最大重试次数
    max_mermaid_retries: int = 5,  # Mermaid 语法错误时的最大重新解析次数
) -> dict:
    """
    处理单张图片，返回处理结果。
    
    重试策略：
    1. API 调用失败（网络错误、限流等）：最多重试 max_retries 次
    2. Mermaid 语法错误：重新调用 Qwen-VL 解析，最多 max_mermaid_retries 次
    """
    image_key = str(image)
    start_time = time.perf_counter()
    last_error = None
    best_content = None  # 保存最佳解析结果（错误最少的）
    best_error_count = float('inf')
    mermaid_retry_count = 0
    
    for attempt in range(1, max_retries + 1):
        try:
            if attempt == 1:
                logger.log(f"[{index}/{total}] 开始解析 {image.name}", show_console=False)
            else:
                # 根据错误类型决定退避时间
                if isinstance(last_error, RateLimitError):
                    wait_time = 5 * attempt  # 限流：5s, 10s, 15s
                    logger.log(f"[{index}/{total}] ⚠️ 检测到限流，等待 {wait_time}s 后重试 ({attempt}/{max_retries}): {image.name}", show_console=True)
                else:
                    wait_time = 2 * attempt  # 其他错误：2s, 4s, 6s
                    logger.log(f"[{index}/{total}] 重试第 {attempt}/{max_retries} 次: {image.name}", show_console=True)
                time.sleep(wait_time)
            
            content = call_qwen(image, prompt, model, api_key, timeout=timeout)
            
            # 清洗输出并检查 Mermaid 语法
            cleaned_content = clean_qwen_output(content)
            is_valid, error_count = check_mermaid_syntax(cleaned_content)
            
            # 记录最佳结果（错误最少的）
            if error_count < best_error_count:
                best_content = content
                best_error_count = error_count
            
            # 如果 Mermaid 语法有错误，尝试重新解析
            if not is_valid and mermaid_retry_count < max_mermaid_retries:
                mermaid_retry_count += 1
                logger.log(
                    f"[{index}/{total}] 🔄 {image.name} 检测到 Mermaid 语法错误（{error_count} 个），重新解析 ({mermaid_retry_count}/{max_mermaid_retries})",
                    show_console=True
                )
                # 短暂等待后重试
                time.sleep(1)
                continue
            
            # 使用最佳内容
            final_content = best_content if best_content else content
            llm_fix_applied = False
            
            # 兜底策略：如果重新解析 5 次后仍有错误，尝试 LLM 修复（最多 3 次）
            if best_error_count > 0:
                logger.log(
                    f"[{index}/{total}] 🔧 {image.name} 重新解析 {max_mermaid_retries} 次后仍有 {best_error_count} 个错误，启用 LLM 兜底修复",
                    show_console=True
                )
                # 保存时启用 LLM 修复（max_retries=3）
                detail_path = save_detail(
                    final_content, 
                    image, 
                    details_dir,
                    model=model,
                    api_key=api_key,
                    mermaid_rules=mermaid_rules,
                    skip_llm_fix=False,  # 启用 LLM 兜底修复
                    llm_max_retries=3    # LLM 修复最多尝试 3 次
                )
                llm_fix_applied = True
                
                # 重新检查修复后的语法错误数
                fixed_text = detail_path.read_text(encoding="utf-8")
                _, final_error_count = check_mermaid_syntax(fixed_text)
                best_error_count = final_error_count
            else:
                # 无错误，直接保存
                detail_path = save_detail(
                    final_content, 
                    image, 
                    details_dir,
                    model=model,
                    api_key=api_key,
                    mermaid_rules=mermaid_rules,
                    skip_llm_fix=True  # 跳过 LLM 修复
                )
            
            progress.mark_processed(image_key)
            duration = time.perf_counter() - start_time
            
            # 构建重试说明
            retry_notes = []
            if attempt > 1:
                retry_notes.append(f"API重试 {attempt - 1} 次")
            if mermaid_retry_count > 0:
                retry_notes.append(f"Mermaid重解析 {mermaid_retry_count} 次")
            if llm_fix_applied:
                retry_notes.append("LLM兜底修复")
            retry_note = f"（{', '.join(retry_notes)}）" if retry_notes else ""
            
            # 如果最终仍有 Mermaid 错误，添加警告
            if best_error_count > 0:
                logger.log(
                    f"[{index}/{total}] ⚠️ {image.name} 完成但仍有 {best_error_count} 个 Mermaid 语法问题（{duration:.1f}s）{retry_note}",
                    show_console=True
                )
            else:
                logger.log(
                    f"[{index}/{total}] {image.name} 完成（{duration:.1f}s）{retry_note}",
                    show_console=True
                )
            
            return {
                "success": True,
                "image": image,
                "detail_path": detail_path,
                "duration": duration,
                "content": final_content,
                "retries": attempt - 1,
                "mermaid_retries": mermaid_retry_count,
                "mermaid_errors": best_error_count,
                "llm_fix_applied": llm_fix_applied,
            }
        except RateLimitError as exc:
            last_error = exc
            # 限流错误：详细输出到控制台
            logger.log(
                f"[{index}/{total}] 🚫 {image.name} 限流: {exc}",
                show_console=True
            )
            if attempt >= max_retries:
                break
            continue
        except Exception as exc:
            last_error = exc
            # 其他错误：输出到日志（首次也输出到控制台帮助诊断）
            show_in_console = (attempt == 1)  # 首次失败时输出到控制台
            logger.log(
                f"[{index}/{total}] ❌ {image.name} 第 {attempt} 次失败: {type(exc).__name__}: {exc}",
                show_console=show_in_console
            )
            if attempt >= max_retries:
                break
            continue
    
    # 所有重试都失败
    duration = time.perf_counter() - start_time
    logger.log(
        f"[{index}/{total}] {image.name} 失败（已重试 {max_retries} 次）: {last_error} ({duration:.1f}s)",
        show_console=True
    )
    return {
        "success": False,
        "image": image,
        "error": str(last_error),
        "duration": duration,
        "retries": max_retries,
    }


def main() -> int:
    args = parse_args()
    
    # 如果指定了 --no-proxy，禁用代理
    if args.no_proxy:
        disable_proxy_if_needed(disable=True)
        print("[Qwen] 已禁用代理设置（--no-proxy）")
    
    static_dir = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    prompt_path = Path(args.prompt).expanduser().resolve()
    details_dir = (
        Path(args.details_dir).expanduser().resolve()
        if args.details_dir
        else output_path.parent / DEFAULT_DETAILS_DIR_NAME
    )
    progress_path = (
        Path(args.progress_file).expanduser().resolve()
        if args.progress_file
        else output_path.with_suffix(output_path.suffix + ".progress.json")
    )
    log_path = (
        Path(args.log_file).expanduser().resolve()
        if args.log_file
        else output_path.parent / f"qwen_parse_{datetime.now():%Y%m%d}.log"
    )

    # 初始化线程安全的日志和进度管理器
    logger = ThreadSafeLogger(log_path)
    progress = ThreadSafeProgress(progress_path, resume=args.resume)

    logger.log("Qwen 解析任务启动（并行模式）")
    logger.log(f"图片目录: {static_dir}")
    logger.log(f"汇总输出: {output_path}")
    logger.log(f"结果目录: {details_dir}")
    logger.log(f"进度文件: {progress_path}")
    logger.log(f"日志文件: {log_path}")
    logger.log(f"并发线程数: {args.workers}")
    logger.log(f"超时设置: {args.timeout} 秒")
    logger.log(f"Mermaid 规则: {DEFAULT_MERMAID_RULES_PATH}")

    print(f"[Qwen] 图片目录: {static_dir}")
    print(f"[Qwen] 汇总输出: {output_path}")
    print(f"[Qwen] 单图结果目录: {details_dir}")
    print(f"[Qwen] 模型: {args.model}")
    print(f"[Qwen] 提示词: {prompt_path}")
    print(f"[Qwen] Mermaid 规则: {DEFAULT_MERMAID_RULES_PATH}")
    print(f"[Qwen] 并发数: {args.workers} 线程")
    print(f"[Qwen] 超时设置: {args.timeout} 秒")
    print(f"[Qwen] 日志: {log_path}")

    prompt_body = load_prompt(prompt_path)
    mermaid_rules = load_mermaid_rules(DEFAULT_MERMAID_RULES_PATH)
    prompt = (
        f"{prompt_body}\n\n---\n\n"
        "以下为 Mermaid 详细规则（必须完整遵守，不得删减）：\n\n"
        f"{mermaid_rules}"
    )
    images = discover_images(static_dir)
    if not images:
        print("[Qwen] 没有需要处理的图片，请确认 static 目录是否包含受支持的文件", file=sys.stderr)
        return 0
    
    api_key = resolve_api_key(args.api_key)
    total = len(images)
    
    # 过滤出需要处理的图片
    to_process = []
    for idx, image in enumerate(images, start=1):
        image_key = str(image)
        if args.resume and progress.is_processed(image_key):
            logger.log(f"[{idx}/{total}] 跳过 {image.name} (已完成)")
            continue
        if args.limit is not None and len(to_process) >= args.limit:
            logger.log(f"已达到 --limit={args.limit}，停止添加任务")
            break
        to_process.append((idx, image))
    
    if not to_process:
        logger.log("所有图片均已处理完成")
    else:
        logger.log(f"准备并行处理 {len(to_process)} 张图片")
        print(f"\n{'='*60}")
        print(f"开始并行处理 {len(to_process)} 张图片，使用 {args.workers} 个线程")
        print(f"{'='*60}\n")
        
        # 使用线程池并行处理
        overall_start = time.perf_counter()
        results = []
        
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            # 提交所有任务
            future_to_image = {
                executor.submit(
                    process_single_image,
                    image,
                    prompt,
                    mermaid_rules,  # 传入规则内容
                    args.model,
                    api_key,
                    details_dir,
                    logger,
                    progress,
                    idx,
                    total,
                    args.timeout,
                    args.max_retries,  # 传入重试次数
                ): (idx, image)
                for idx, image in to_process
            }
            
            # 收集完成的任务
            completed = 0
            for future in as_completed(future_to_image):
                idx, image = future_to_image[future]
                try:
                    result = future.result()
                    results.append(result)
                    completed += 1
                    
                    # 显示进度
                    if result["success"]:
                        print(f"\r进度: {completed}/{len(to_process)} 完成", end="", flush=True)
                except Exception as exc:
                    logger.log(f"任务异常 [{idx}/{total}] {image.name}: {exc}")
        
        print(f"\n\n{'='*60}")
        overall_duration = time.perf_counter() - overall_start
        
        # 统计结果
        success_count = sum(1 for r in results if r["success"])
        failed_count = len(results) - success_count
        total_api_time = sum(r["duration"] for r in results)
        avg_duration = total_api_time / len(results) if results else 0
        
        logger.log(f"并行处理完成: 成功 {success_count}，失败 {failed_count}")
        logger.log(f"总耗时: {overall_duration:.1f}s，API累计时间: {total_api_time:.1f}s")
        logger.log(f"平均单图耗时: {avg_duration:.1f}s，加速比: {total_api_time/overall_duration:.2f}x")
        
        print(f"✓ 成功: {success_count} 张")
        print(f"✗ 失败: {failed_count} 张")
        print(f"⏱ 总耗时: {overall_duration:.1f}s (API累计: {total_api_time:.1f}s)")
        print(f"📈 加速比: {total_api_time/overall_duration:.2f}x")
        print(f"{'='*60}\n")

    # 生成汇总文档
    all_processed = progress.get_all()
    if args.resume:
        summary_rows = build_rows_from_paths(all_processed, details_dir)
    else:
        summary_rows = build_rows_from_paths([str(path) for path in images], details_dir)

    ensure_parent(output_path)
    summary_md = build_summary_table(summary_rows, output_path.parent)
    output_path.write_text(summary_md, encoding="utf-8")
    print(f"[Qwen] 汇总写入 {output_path}")
    logger.log("Qwen 解析任务完成")
    logger.log(f"共处理 {len(all_processed)} 张图片，详情见 {output_path}")
    
    # 清理临时文件（进度文件）
    if progress_path.exists():
        try:
            progress_path.unlink()
            print(f"[Qwen] 已清理进度文件: {progress_path.name}")
        except Exception as e:
            print(f"[Qwen] 清理进度文件失败: {e}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
