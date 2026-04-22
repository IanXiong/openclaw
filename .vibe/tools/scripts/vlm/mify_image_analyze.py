# -*- coding: utf-8 -*-
"""MiFy image analysis with concurrent upload and concurrent workflow calls.

- Upload images concurrently
- Workflow calls in batches (default batch_size=1), concurrently
- Optional log file for detailed steps
- Default timeout 300s, insecure SSL by default
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
import json
import mimetypes
import ssl
import uuid
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List
from urllib import error, request

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
SCRIPT_DIR = Path(__file__).resolve().parent
MODE_LABEL = "mify"

# Import unified config module
import sys as _sys
_sys.path.insert(0, str(SCRIPT_DIR.parent / "infra"))
from vaf_config import get_mify_api_key, get_mify_upload_url, get_mify_workflow_url, get_dashscope_api_key


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


DEFAULT_PROMPT_PATH = _resolve_resource(
    Path(__file__),
    ".vibe/core/prompts/tool_usage/qwen_vl_analysis.md",
    ".vibe/core/prompts/tool_usage/qwen_vl_analysis.md"
)
DEFAULT_MERMAID_RULES_PATH = _resolve_resource(
    Path(__file__),
    ".vibe/core/rules/mermaid_rules.md",
    ".vibe/core/rules/mermaid_rules.md"
)

# 导入 Mermaid 校验函数（从独立模块导入）
# 延迟导入，避免循环依赖和路径问题
def _get_mermaid_validators():
    import sys
    validation_dir = str(SCRIPT_DIR.parent / "validation")
    if validation_dir not in sys.path:
        sys.path.insert(0, validation_dir)
    from mermaid_validator import (
        validate_and_fix_mermaid,
        check_mermaid_syntax,
        clean_qwen_output,
    )
    return validate_and_fix_mermaid, check_mermaid_syntax, clean_qwen_output

def get_mify_config() -> Dict[str, str]:
    """获取 MiFy 相关配置
    
    优先级: 环境变量 > ~/.vaf/config
    """
    api_key = get_mify_api_key()
    
    if not api_key:
        raise RuntimeError(
            "MiFy API Key 未配置。请在 ~/.vaf/config 中配置 [mify] 部分，\n"
            "或设置环境变量 MIFY_API_KEY。详见: docs/configuration.md"
        )
    
    return {
        "api_key": api_key,
        "upload_url": get_mify_upload_url(),
        "workflow_url": get_mify_workflow_url(),
    }


class UploadError(RuntimeError):
    """Raised when the Mify endpoint returns an error."""


class ThreadSafeLogger:
    def __init__(self, log_path: Path | None = None):
        self.log_path = log_path
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, message: str, show_console: bool = True) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        if show_console:
            print(line)
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


def upload_file(
    file_path: Path,
    user: str = "default",
    timeout: float = 300.0,
    insecure: bool = True,
    max_retries: int = 1,
    logger: ThreadSafeLogger | None = None,
    mify_config: Dict[str, str] | None = None,
) -> str:
    path = file_path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    cfg = mify_config or get_mify_config()
    boundary = f"Boundary-{uuid.uuid4().hex}"
    body = _encode_multipart_form(path, user or "default", boundary)
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }

    req = request.Request(cfg["upload_url"], data=body, headers=headers, method="POST")
    context = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            with request.urlopen(req, timeout=timeout, context=context) as resp:  # type: ignore[arg-type]
                payload = resp.read()
                data = json.loads(payload.decode("utf-8"))
                up_id = data.get("id")
                if not up_id:
                    raise UploadError("Upload response missing 'id'")
                return up_id
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            last_exc = UploadError(f"Upload failed ({exc.code}): {detail}")
        except error.URLError as exc:
            last_exc = UploadError(f"Network error: {exc.reason}")
        except TimeoutError:
            last_exc = UploadError("Network timeout during upload")
        if attempt + 1 < max_retries and logger:
            logger.log(f"重试上传 {path.name} ({attempt+1}/{max_retries})")
    assert last_exc is not None
    raise last_exc


def run_workflow_single(
    upload_file_ids: List[str],
    prompt: str,
    user: str,
    timeout: float,
    insecure: bool,
    max_retries: int,
    logger: ThreadSafeLogger | None = None,
    mify_config: Dict[str, str] | None = None,
) -> str:
    cfg = mify_config or get_mify_config()
    imgs_payload = [
        {
            "transfer_method": "local_file",
            "upload_file_id": upload_id,
            "type": "image",
        }
        for upload_id in upload_file_ids
    ]
    body = json.dumps(
        {
            "inputs": {
                "imgs": imgs_payload,
                "prompt": prompt,
            },
            "response_mode": "blocking",
            "user": user,
        }
    ).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    req = request.Request(cfg["workflow_url"], data=body, headers=headers, method="POST")
    context = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            with request.urlopen(req, timeout=timeout, context=context) as resp:  # type: ignore[arg-type]
                payload = resp.read()
                return payload.decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            last_exc = UploadError(f"Workflow failed ({exc.code}): {detail}")
        except error.URLError as exc:
            last_exc = UploadError(f"Network error: {exc.reason}")
        except TimeoutError:
            last_exc = UploadError("Network timeout during workflow run")
        if attempt + 1 < max_retries and logger:
            logger.log(f"重试工作流 batch({len(upload_file_ids)} 张) ({attempt+1}/{max_retries})")
    assert last_exc is not None
    raise last_exc


def discover_images(static_dir: Path) -> List[Path]:
    if not static_dir.exists() or not static_dir.is_dir():
        raise FileNotFoundError(f"图片目录不存在或无效: {static_dir}")
    results: List[Path] = []
    for path in static_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTS:
            results.append(path)
    if not results:
        raise FileNotFoundError(f"目录 {static_dir} 内未发现受支持的图片文件")
    results.sort()
    return results


def load_prompt(prompt_ref: str) -> str:
    path = Path(prompt_ref).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"提示词文件不存在: {path}")
    return path.read_text(encoding="utf-8").strip()


def load_mermaid_rules(path: Path | None = None) -> str:
    """加载 Mermaid 规则文件（与 DashScope 链路保持一致）"""
    target = path or DEFAULT_MERMAID_RULES_PATH
    if target.is_file():
        return target.read_text(encoding="utf-8").strip()
    return ""


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def extract_workflow_text(response_text: str) -> str:
    try:
        data = json.loads(response_text)
    except Exception:
        return response_text
    if isinstance(data, dict):
        outputs = data.get("data", {}).get("outputs") if isinstance(data.get("data"), dict) else None
        if isinstance(outputs, dict):
            text = outputs.get("text")
            if isinstance(text, str):
                return text
    return response_text


def build_summary_table(rows: List[Dict[str, Any]], base_dir: Path) -> str:
    """生成与 DashScope 格式一致的资产解析汇总索引表"""
    import os
    lines = [
        "# 资产解析汇总",
        "",
        "| 序号 | 图片 | 结果文件 | 模式/模型 | 备注 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        img_rel = os.path.relpath(row["image"], base_dir)
        detail_rel = os.path.relpath(row["detail"], base_dir)
        note = row.get("note", "解析完成")
        lines.append(
            f"| {row['index']} | `{img_rel}` | [查看]({detail_rel}) | {MODE_LABEL} | {note} |"
        )
    return "\n".join(lines) + "\n"


def _encode_multipart_form(path: Path, user: str, boundary: str) -> bytes:
    mime_type, _ = mimetypes.guess_type(path.name)
    mime_type = mime_type or "application/octet-stream"
    buffer = io.BytesIO()

    def write(data: str | bytes) -> None:
        buffer.write(data.encode("utf-8") if isinstance(data, str) else data)

    crlf = "\r\n"

    write(f"--{boundary}{crlf}")
    write("Content-Disposition: form-data; name=\"user\"" + crlf + crlf)
    write(user)
    write(crlf)

    write(f"--{boundary}{crlf}")
    disposition = (
        f"Content-Disposition: form-data; name=\"file\"; filename=\"{path.name}\""
    )
    write(disposition + crlf)
    write(f"Content-Type: {mime_type}{crlf}{crlf}")
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(8192)
            if not chunk:
                break
            write(chunk)
    write(crlf)

    write(f"--{boundary}--{crlf}")
    return buffer.getvalue()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量上传图片并调用 Mify 工作流")
    parser.add_argument("--input", required=True, help="包含图片资源的 static 目录路径")
    parser.add_argument(
        "--output",
        required=True,
        help="汇总输出文件（例如 assets_summary.md）的路径",
    )
    parser.add_argument(
        "--details-dir",
        default=None,
        help="单图分析结果输出目录（默认为 output 同级的 assets_analysis）",
    )
    parser.add_argument(
        "--prompt",
        default=str(DEFAULT_PROMPT_PATH),
        help=f"提示词 Markdown 文件路径（默认: {DEFAULT_PROMPT_PATH}）",
    )
    parser.add_argument("--user", default="default", help="上传文件时使用的 Mify user")
    parser.add_argument(
        "--workflow-user",
        dest="workflow_user",
        default="default",
        help="调用工作流时的 user 字段",
    )
    parser.add_argument("--timeout", type=float, default=300.0, help="单次请求的超时时间（秒）")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="本次最多处理多少张图片，默认处理全部",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=1,
        help="请求失败时的最大重试次数（默认1，不重试）",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        default=True,
        help="跳过 SSL 证书校验（默认开启；生产如需校验证书请移除此参数并配置可信 CA）",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="可选：写入详细日志到指定文件",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="并发上传/工作流线程数（默认10）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续跑：跳过 assets_analysis 目录中已成功解析的图片",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_dir = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    # 初始化 details_dir（与 DashScope 格式一致）
    if args.details_dir:
        details_dir = Path(args.details_dir).expanduser().resolve()
    else:
        details_dir = output_path.parent / "assets_analysis"
    details_dir.mkdir(parents=True, exist_ok=True)

    logger = ThreadSafeLogger(Path(args.log_file)) if args.log_file else None

    # 预加载 MiFy 配置，提前校验
    try:
        mify_cfg = get_mify_config()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        if logger:
            logger.log(str(exc), show_console=False)
        return 1

    try:
        prompt_body = load_prompt(args.prompt)
        mermaid_rules = load_mermaid_rules()
        # 与 DashScope 链路保持一致：主提示词 + Mermaid 规则
        prompt_text = (
            f"{prompt_body}\n\n---\n\n"
            "以下为 Mermaid 详细规则（必须完整遵守，不得删减）：\n\n"
            f"{mermaid_rules}"
        ) if mermaid_rules else prompt_body
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        if logger:
            logger.log(str(exc), show_console=False)
        return 1

    try:
        images = discover_images(input_dir)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        if logger:
            logger.log(str(exc), show_console=False)
        return 1

    # Resume 模式：检测已成功解析的图片并跳过
    skipped_count = 0
    if args.resume and details_dir.exists():
        already_done = {f.stem for f in details_dir.glob("*.md") if f.stat().st_size > 100}
        original_count = len(images)
        images = [img for img in images if img.stem not in already_done]
        skipped_count = original_count - len(images)
        if skipped_count > 0:
            print(f"🔄 [Resume] 跳过已完成的 {skipped_count} 张图片，剩余 {len(images)} 张待处理")
            if logger:
                logger.log(f"[Resume] 跳过已完成的 {skipped_count} 张图片", show_console=False)

    if args.limit is not None:
        images = images[: max(args.limit, 0)]
    if not images:
        if skipped_count > 0:
            print(f"✅ 所有图片均已处理完成（共 {skipped_count} 张）")
            if logger:
                logger.log(f"所有图片均已处理完成（共 {skipped_count} 张）", show_console=False)
            return 0
        print("未找到可处理的图片", file=sys.stderr)
        if logger:
            logger.log("未找到可处理的图片", show_console=False)
        return 1

    total = len(images)
    print(f"共发现 {total} 张图片，开始上传…")
    if logger:
        logger.log(f"共发现 {total} 张图片，开始上传…", show_console=False)

    # 上传阶段：记录 (image_path, upload_id, error)
    upload_results: List[tuple[Path, str | None, str | None]] = []

    def _upload_task(idx_img: tuple[int, Path]) -> tuple[int, Path, str | None, str | None]:
        idx, image = idx_img
        try:
            upload_id = upload_file(
                file_path=image,
                user=args.user,
                timeout=args.timeout,
                insecure=args.insecure,
                max_retries=args.max_retries,
                logger=logger,
                mify_config=mify_cfg,
            )
            return idx, image, upload_id, None
        except Exception as exc:
            return idx, image, None, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(_upload_task, (idx, img)): (idx, img) for idx, img in enumerate(images, start=1)}
        for fut in as_completed(futures):
            idx, img = futures[fut]
            try:
                _, image, up_id, err = fut.result()
                upload_results.append((image, up_id, err))
                if up_id:
                    msg = f"[{idx}/{total}] 上传 {image.name} 成功"
                    print(msg)
                    if logger:
                        logger.log(msg, show_console=False)
                else:
                    msg = f"[{idx}/{total}] 上传 {image.name} 失败: {err}"
                    print(msg, file=sys.stderr)
                    if logger:
                        logger.log(msg, show_console=False)
            except Exception as exc:  # pragma: no cover - defensive
                msg = f"[{idx}/{total}] 上传 {img.name} 失败: {exc}"
                print(msg, file=sys.stderr)
                if logger:
                    logger.log(msg, show_console=False)

    # 建立 upload_id -> image_path 映射
    upload_map: Dict[str, Path] = {}
    for image, up_id, _ in upload_results:
        if up_id:
            upload_map[up_id] = image

    if not upload_map:
        print("未成功上传任何图片，终止。", file=sys.stderr)
        if logger:
            logger.log("未成功上传任何图片，终止。", show_console=False)
        return 1

    # 工作流调用：逐图调用（batch_size 固定为 1）
    workflow_workers = max(1, args.workers)
    # 构建任务列表：[(upload_id, image_path), ...]
    workflow_tasks = list(upload_map.items())

    print(f"所有可用图片上传完成，开始调用工作流… (逐图调用, 并发={workflow_workers})")
    if logger:
        logger.log(f"所有可用图片上传完成，开始调用工作流… (逐图调用, 并发={workflow_workers})", show_console=False)

    # 存储解析结果行
    summary_rows: List[Dict[str, Any]] = []
    row_lock = __import__("threading").Lock()

    def _workflow_task(idx_task: tuple[int, str, Path]) -> tuple[int, Path, str | None, Path | None]:
        idx, upload_id, image_path = idx_task
        detail_path = details_dir / f"{image_path.stem}.md"
        try:
            resp_text = run_workflow_single(
                upload_file_ids=[upload_id],
                prompt=prompt_text,
                user=args.workflow_user,
                timeout=args.timeout,
                insecure=args.insecure,
                max_retries=args.max_retries,
                logger=logger,
                mify_config=mify_cfg,
            )
            text_output = extract_workflow_text(resp_text)
            
            # 清洗输出并进行 Mermaid 校验与修复（与 DashScope 链路保持一致）
            validate_and_fix_mermaid, check_mermaid_syntax, clean_qwen_output = _get_mermaid_validators()
            cleaned_output = clean_qwen_output(text_output)
            is_valid, error_count = check_mermaid_syntax(cleaned_output)
            
            if not is_valid:
                # 方案 B：MiFy 链路启用 LLM 修复回退
                # 尝试获取 DashScope API Key，如果可用则启用 LLM 修复
                dashscope_api_key = get_dashscope_api_key()
                enable_llm_fix = bool(dashscope_api_key)
                
                if enable_llm_fix:
                    # 有 DashScope 配置，启用 LLM 自愈修复
                    fixed_output = validate_and_fix_mermaid(
                        cleaned_output,
                        model="qwen-vl-max",  # 使用 Qwen-VL 进行修复
                        api_key=dashscope_api_key,
                        mermaid_rules=mermaid_rules,
                        enable_llm=True,  # 启用 LLM 修复
                        max_retries=2,  # 最多重试 2 次
                    )
                    if logger:
                        logger.log(f"[{image_path.name}] Mermaid 语法问题（{error_count} 个错误），已启用 LLM 自愈修复", show_console=False)
                else:
                    # 无 DashScope 配置，仅做静态校验
                    fixed_output = validate_and_fix_mermaid(
                        cleaned_output,
                        model="",
                        api_key="",
                        mermaid_rules=mermaid_rules,
                        enable_llm=False,
                    )
                    if logger:
                        logger.log(f"[{image_path.name}] Mermaid 语法问题（{error_count} 个错误），未配置 DashScope，跳过 LLM 修复", show_console=False)
            else:
                fixed_output = cleaned_output
            
            ensure_parent(detail_path)
            detail_path.write_text(fixed_output, encoding="utf-8")
            return idx, image_path, None, detail_path
        except Exception as exc:
            # 写入错误占位文件
            ensure_parent(detail_path)
            detail_path.write_text(f"# 解析失败\n\n错误: {exc}", encoding="utf-8")
            return idx, image_path, str(exc), detail_path

    success_count = 0
    fail_count = 0
    with ThreadPoolExecutor(max_workers=workflow_workers) as executor:
        task_list = [(idx, uid, img) for idx, (uid, img) in enumerate(workflow_tasks, start=1)]
        futures = {executor.submit(_workflow_task, t): t for t in task_list}
        for fut in as_completed(futures):
            idx, uid, img = futures[fut]
            try:
                _, image_path, err, detail_path = fut.result()
                if err:
                    msg = f"[WF {idx}/{len(workflow_tasks)}] {image_path.name} 失败: {err}"
                    print(msg, file=sys.stderr)
                    if logger:
                        logger.log(msg, show_console=False)
                    fail_count += 1
                    note = f"解析失败: {err[:50]}"
                else:
                    msg = f"[WF {idx}/{len(workflow_tasks)}] {image_path.name} 完成"
                    print(msg)
                    if logger:
                        logger.log(msg, show_console=False)
                    success_count += 1
                    note = "解析完成"
                # 记录行信息
                with row_lock:
                    summary_rows.append({
                        "index": idx,
                        "image": image_path,
                        "detail": detail_path,
                        "note": note,
                    })
            except Exception as exc:  # pragma: no cover - defensive
                msg = f"[WF {idx}/{len(workflow_tasks)}] 执行异常: {exc}"
                print(msg, file=sys.stderr)
                if logger:
                    logger.log(msg, show_console=False)
                fail_count += 1

    if success_count == 0:
        print("未成功解析任何图片，终止。", file=sys.stderr)
        if logger:
            logger.log("未成功解析任何图片，终止。", show_console=False)
        return 1

    # 生成索引表（与 DashScope 格式一致）
    summary_rows.sort(key=lambda r: r["index"])
    summary_content = build_summary_table(summary_rows, output_path.parent)
    ensure_parent(output_path)
    output_path.write_text(summary_content, encoding="utf-8")

    if logger and logger.log_path:
        logger.log("处理完成", show_console=False)
    print(f"\n{'='*60}")
    print(f"✓ 成功: {success_count} 张")
    print(f"✗ 失败: {fail_count} 张")
    print(f"📁 单图分析: {details_dir}")
    print(f"📋 汇总索引: {output_path}")
    print(f"{'='*60}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
