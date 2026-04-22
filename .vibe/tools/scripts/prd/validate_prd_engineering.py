#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate engineered PRD structure against template constraints."""

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
import re
from collections import Counter
from pathlib import Path
from typing import List, Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE = ROOT / "core" / "templates" / "prd_template.md"

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
BARE_IMAGE_RE = re.compile(r"\]\(\s*source_export/static/", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate engineered PRD output")
    parser.add_argument(
        "engineered",
        type=Path,
        help="Path to docs/features/<Selected_Dir>/P03_prd_review/PRD_standardized.md",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE,
        help="Template PRD path (default: .vibe/core/templates/requirements/prd_template.md)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional JSON report output path (written even on success)",
    )
    return parser.parse_args()


def collect_headings(text: str) -> List[str]:
    return [match.group(0).strip() for match in HEADING_RE.finditer(text)]


def check_image_paths(text: str) -> tuple[bool, str | None]:
    if BARE_IMAGE_RE.search(text):
        return False, "检测到未重写的图片路径 source_export/static/"
    return True, None


def normalize_heading(heading: str) -> str:
    """Normalize heading text for comparison."""
    heading = heading.strip()
    heading = re.sub(r"\s+", " ", heading)
    return heading


def check_headings(template_text: str, target_text: str) -> tuple[bool, str | None]:
    template_headings = [normalize_heading(h) for h in collect_headings(template_text)]
    target_headings_raw = collect_headings(target_text)
    target_headings = [normalize_heading(h) for h in target_headings_raw]

    bold_headings = [h for h in target_headings_raw if "**" in h or "__" in h]
    if bold_headings:
        return False, f"标题存在加粗样式: {bold_headings[:3]}"

    template_set = set(template_headings)
    duplicates = [
        heading
        for heading, count in Counter(h for h in target_headings if h in template_set).items()
        if count > 1
    ]
    if duplicates:
        preview = ", ".join(duplicates[:3])
        return False, f"模板标题重复: {preview}"

    missing = [h for h in template_headings if h not in target_headings]
    if missing:
        preview = ", ".join(missing[:3])
        return False, f"缺少模板标题: {preview}"

    target_sequence = [h for h in target_headings if h in template_set]
    if target_sequence != template_headings:
        return False, "模板目录顺序不正确或存在额外模板标题"

    return True, None


def main() -> int:
    args = parse_args()
    engine_path = args.engineered.expanduser().resolve()
    template_path = args.template.expanduser().resolve()

    if not engine_path.exists():
        raise SystemExit(f"PRD 文件不存在: {engine_path}")
    if not template_path.exists():
        raise SystemExit(f"模板不存在: {template_path}")

    engine_text = engine_path.read_text(encoding="utf-8")
    template_text = template_path.read_text(encoding="utf-8")

    checks: Sequence[tuple[str, tuple[bool, str | None]]] = (
        ("image_paths", check_image_paths(engine_text)),
        ("template_headings", check_headings(template_text, engine_text)),
    )

    errors = [f"[{name}] {message}" for name, (ok, message) in checks if not ok and message]
    status = "passed" if not errors else "failed"

    if args.report:
        report = {
            "engineered": str(engine_path),
            "template": str(template_path),
            "status": status,
            "errors": errors,
            "checks": {name: (ok and message is None) for name, (ok, message) in checks},
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if errors:
        print("❌ validation failed:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("✅ validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
