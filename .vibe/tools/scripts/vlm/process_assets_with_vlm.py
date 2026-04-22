# -*- coding: utf-8 -*-
"""Unified VLM entry: route image analysis to different backends.

Default: DashScope (qwen3-vl-plus). Optional: MiFy (workflow-based).

This is the new neutral entry; legacy scripts can thin-wrap to this.
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
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

# Import unified config module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "infra"))
from vaf_config import get_vlm_rollout_mode

# Backends identifiers
BACKEND_DASHSCOPE = "dashscope"
BACKEND_MIFY = "mify"
ROLLOUT_CLASSIC = "classic"
ROLLOUT_DUAL = "dual"
ROLLOUT_MIFY = "mify"


# Simple provider registry
class InferenceResult:
    def __init__(self, text: str, used_model: str, latency_ms: float | None = None):
        self.text = text
        self.used_model = used_model
        self.latency_ms = latency_ms


class InferenceProvider:
    name: str

    def generate(
        self,
        image_path: Path,
        prompt: str,
        model: str,
        timeout: float,
        extra: Dict[str, Any] | None = None,
    ) -> InferenceResult:
        raise NotImplementedError


class ProviderRegistry:
    def __init__(self):
        self._providers: Dict[str, InferenceProvider] = {}

    def register(self, provider: InferenceProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> InferenceProvider:
        if name not in self._providers:
            raise ValueError(f"Unknown backend: {name}")
        return self._providers[name]


# DashScope provider (thin wrapper around existing script)
class DashScopeProvider(InferenceProvider):
    name = BACKEND_DASHSCOPE

    def __init__(self, script_dir: Path):
        self.script = script_dir / "process_assets_with_qwen.py"

    def generate(
        self,
        image_path: Path,
        prompt: str,
        model: str,
        timeout: float,
        extra: Dict[str, Any] | None = None,
    ) -> InferenceResult:
        # Delegates to existing CLI (process_assets_with_qwen.py)
        output_path: Path = extra["output"]
        limit: Optional[int] = extra.get("limit") if extra else None
        workers = extra.get("workers") if extra else None
        cmd = [
            sys.executable,
            str(self.script),
            "--input",
            str(image_path),
            "--output",
            str(output_path),
            "--model",
            model,
            "--timeout",
            str(int(timeout)),
        ]
        if prompt:
            cmd += ["--prompt", prompt]
        if limit is not None:
            cmd += ["--limit", str(limit)]
        if workers is not None:
            cmd += ["--workers", str(workers)]
        log_file = extra.get("log_file") if extra else None
        if log_file:
            cmd += ["--log-file", str(log_file)]
        if extra and extra.get("resume"):
            cmd.append("--resume")
        subprocess.run(cmd, check=True)
        return InferenceResult(text=str(output_path), used_model=model)


# MiFy provider (placeholder for future integration with workflow API)
class MifyProvider(InferenceProvider):
    name = BACKEND_MIFY

    def __init__(self, script_dir: Path):
        self.script = script_dir / "mify_image_analyze.py"

    def generate(
        self,
        image_path: Path,
        prompt: str,
        model: str,
        timeout: float,
        extra: Dict[str, Any] | None = None,
    ) -> InferenceResult:
        output_path: Path = extra["output"]
        details_dir: Path | None = extra.get("details_dir") if extra else None
        limit: Optional[int] = extra.get("limit") if extra else None
        insecure: bool = bool(extra.get("insecure")) if extra else True
        workers: Optional[int] = extra.get("workers") if extra else None
        cmd = [
            sys.executable,
            str(self.script),
            "--input",
            str(image_path),
            "--output",
            str(output_path),
            "--timeout",
            str(int(timeout)),
        ]
        if details_dir:
            cmd += ["--details-dir", str(details_dir)]
        if prompt:
            cmd += ["--prompt", prompt]
        if limit is not None:
            cmd += ["--limit", str(limit)]
        if insecure:
            cmd.append("--insecure")
        if workers is not None:
            cmd += ["--workers", str(workers)]
        log_file = extra.get("log_file") if extra else None
        if log_file:
            cmd += ["--log-file", str(log_file)]
        if extra and extra.get("resume"):
            cmd.append("--resume")
        subprocess.run(cmd, check=True)
        return InferenceResult(text=str(output_path), used_model=model)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process assets with VLM backends")
    parser.add_argument("--input", required=True, help="图片目录（static）")
    parser.add_argument("--output", required=True, help="汇总输出文件路径")
    parser.add_argument(
        "--details-dir",
        default=None,
        help="单图分析结果输出目录（默认为 output 同级的 assets_analysis）",
    )
    parser.add_argument(
        "--backend",
        default=BACKEND_DASHSCOPE,
        choices=[BACKEND_DASHSCOPE, BACKEND_MIFY],
        help="选择 VLM 后端",
    )
    parser.add_argument(
        "--rollout-mode",
        default=None,
        choices=[ROLLOUT_CLASSIC, ROLLOUT_DUAL, ROLLOUT_MIFY],
        help="灰度模式：classic/dual/mify",
    )
    parser.add_argument(
        "--model",
        default="qwen3-vl-plus",
        help="首选模型（视后端而定）",
    )
    parser.add_argument("--timeout", type=float, default=300.0, help="单次请求超时时间（秒）")
    parser.add_argument("--limit", type=int, default=None, help="最多处理多少张图片")
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="并发线程数（透传给下游 DashScope/MiFy 脚本）",
    )
    parser.add_argument("--prompt", default=None, help="提示词文件路径，如不填由下游脚本处理")
    parser.add_argument("--insecure", action="store_true", help="跳过 SSL 校验（仅对 MiFy 生效）")
    parser.add_argument(
        "--log-file",
        default=None,
        help="日志文件路径（透传给下游脚本）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续跑：跳过已成功解析的图片（透传给下游脚本）",
    )
    return parser.parse_args()


def load_rollout_from_config() -> str:
    """从 ~/.vaf/config 读取 ROLLOUT_MODE"""
    return get_vlm_rollout_mode()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    # 初始化 details_dir
    if args.details_dir:
        details_dir = Path(args.details_dir).expanduser().resolve()
    else:
        details_dir = output_path.parent / "assets_analysis"

    if not input_dir.exists() or not input_dir.is_dir():
        print(f"输入目录不存在或无效: {input_dir}", file=sys.stderr)
        return 1

    # Registry setup (delegates to existing scripts)
    scripts_dir = Path(__file__).resolve().parent
    registry = ProviderRegistry()
    registry.register(DashScopeProvider(scripts_dir))
    registry.register(MifyProvider(scripts_dir))

    config_mode = load_rollout_from_config(Path(args.config)) if args.rollout_mode is None else None
    mode = args.rollout_mode or config_mode or ROLLOUT_CLASSIC
    if mode == ROLLOUT_CLASSIC:
        provider = registry.get(BACKEND_DASHSCOPE)
        extra = {
            "output": output_path,
            "limit": args.limit,
            "insecure": args.insecure,
            "workers": args.workers,
            "log_file": args.log_file,
            "resume": args.resume,
        }
        try:
            provider.generate(
                image_path=input_dir,
                prompt=args.prompt,
                model=args.model,
                timeout=args.timeout,
                extra=extra,
            )
        except subprocess.CalledProcessError as exc:
            print(f"子进程执行失败（{provider.name}）：{exc}", file=sys.stderr)
            return exc.returncode or 1
        except Exception as exc:  # pragma: no cover - defensive
            print(f"执行失败：{exc}", file=sys.stderr)
            return 1
        return 0

    if mode == ROLLOUT_MIFY:
        provider = registry.get(BACKEND_MIFY)
        extra = {
            "output": output_path,
            "details_dir": details_dir,
            "limit": args.limit,
            "insecure": args.insecure,
            "workers": args.workers,
            "log_file": args.log_file,
            "resume": args.resume,
        }
        try:
            provider.generate(
                image_path=input_dir,
                prompt=args.prompt,
                model=args.model,
                timeout=args.timeout,
                extra=extra,
            )
        except subprocess.CalledProcessError as exc:
            print(f"子进程执行失败（{provider.name}）：{exc}", file=sys.stderr)
            return exc.returncode or 1
        except Exception as exc:  # pragma: no cover - defensive
            print(f"执行失败：{exc}", file=sys.stderr)
            return 1
        return 0

    if mode == ROLLOUT_DUAL:
        # 主产物用 DashScope，MiFy 用于对比/日志（此处先串行执行，后续可并行和比对实现）
        dash_output = output_path
        mify_output = output_path.parent / (output_path.stem + "_mify" + output_path.suffix)
        mify_details_dir = output_path.parent / "assets_analysis_mify"

        # DashScope
        try:
            registry.get(BACKEND_DASHSCOPE).generate(
                image_path=input_dir,
                prompt=args.prompt,
                model=args.model,
                timeout=args.timeout,
                extra={
                    "output": dash_output,
                    "limit": args.limit,
                    "insecure": args.insecure,
                    "workers": args.workers,
                },
            )
        except subprocess.CalledProcessError as exc:
            print(f"子进程执行失败（dashscope）：{exc}", file=sys.stderr)
            return exc.returncode or 1
        except Exception as exc:
            print(f"dashscope 执行失败：{exc}", file=sys.stderr)
            return 1

        # MiFy（对比用途）
        mify_status = 0
        try:
            registry.get(BACKEND_MIFY).generate(
                image_path=input_dir,
                prompt=args.prompt,
                model=args.model,
                timeout=args.timeout,
                extra={
                    "output": mify_output,
                    "details_dir": mify_details_dir,
                    "limit": args.limit,
                    "insecure": args.insecure,
                    "workers": args.workers,
                },
            )
        except subprocess.CalledProcessError as exc:
            mify_status = exc.returncode or 1
            print(f"MiFy 对比子进程失败：{exc}", file=sys.stderr)
        except Exception as exc:
            mify_status = 1
            print(f"MiFy 对比执行失败：{exc}", file=sys.stderr)

        if mify_status == 0:
            print(f"对比结果（MiFy）输出: {mify_output}")
        else:
            print("MiFy 对比未生成，仍以 DashScope 产物为准。", file=sys.stderr)
        return 0

    print(f"未知 rollout 模式: {mode}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
