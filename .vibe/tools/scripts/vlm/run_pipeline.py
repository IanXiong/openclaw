# -*- coding: utf-8 -*-
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
import shutil
import time
from datetime import datetime
from pathlib import Path

# Import unified config module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "infra"))
from vaf_config import (
    get_vlm_rollout_mode,
    get_ignore_sections,
    get_ignore_callouts,
    get_ignore_section_callouts,
)

def run_step(cmd, description):
    """Run a subprocess command with visual feedback."""
    print(f"\n🚀 [Step] {description}...")
    start_time = time.time()
    try:
        # shell=False is safer and avoids escaping issues
        subprocess.run(cmd, check=True, shell=False)
        duration = time.time() - start_time
        print(f"✅ {description} 完成 (耗时: {duration:.2f}s)")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} 失败 (Exit Code: {e.returncode})")
        return False
    except Exception as e:
        print(f"❌ {description} 异常: {e}")
        return False


def _is_history_version_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        datetime.strptime(path.name, "%Y%m%d_%H%M%S")
        return True
    except ValueError:
        return False


def prune_history(history_dir: Path, keep_versions: int = 3):
    if keep_versions <= 0 or not history_dir.exists():
        return
    version_dirs = [p for p in history_dir.iterdir() if _is_history_version_dir(p)]
    if len(version_dirs) <= keep_versions:
        return
    version_dirs.sort(key=lambda p: p.name, reverse=True)
    for old_dir in version_dirs[keep_versions:]:
        try:
            shutil.rmtree(old_dir)
            print(f"   已清理历史版本: {old_dir.name}")
        except Exception as exc:
            print(f"   ⚠️ 无法删除历史目录 {old_dir.name}: {exc}")


def _get_output_candidates(base_dir: Path):
    """获取所有可能的产物路径列表"""
    candidates = [
        base_dir / "source_export",
        base_dir / "assets_analysis",
        base_dir / "assets_summary.md",
        base_dir / "assets_summary.md.progress.json",
        base_dir / "metadata.json",
        base_dir / "PRD.md",
        base_dir / "PRD_standardized.md",
        base_dir / "PRD_Review_Report.md",
        base_dir / "validation_log.md",
        base_dir / "resources.json",
    ]
    candidates.extend(base_dir.glob("qwen_parse_*.log"))
    candidates.extend(base_dir.glob("mify_parse_*.log"))
    candidates.extend(base_dir.glob("fetch_feishu_doc_*.log"))
    return candidates


def has_existing_outputs(base_dir: Path) -> bool:
    """检测是否存在旧产物"""
    return any(p.exists() for p in _get_output_candidates(base_dir))


def clean_existing_outputs(base_dir: Path):
    """直接删除旧产物（覆盖模式）"""
    existing = [p for p in _get_output_candidates(base_dir) if p.exists()]
    if not existing:
        print("ℹ️ 未检测到需要清理的旧产物。")
        return
    
    print("🗑️ 正在清理现有产物（覆盖模式）...")
    for item in existing:
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            print(f"   已删除 {item.name}")
        except Exception as exc:
            print(f"   ⚠️ 无法删除 {item.name}: {exc}")


def archive_existing_outputs(base_dir: Path, history_dir: Path, keep_versions: int = 1):
    """归档旧产物到 history 目录"""
    existing = [p for p in _get_output_candidates(base_dir) if p.exists()]
    if not existing:
        print("ℹ️ 未检测到需要归档的旧产物，跳过归档步骤。")
        prune_history(history_dir, keep_versions)
        return

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    archive_dir = history_dir / timestamp
    archive_dir.mkdir(parents=True, exist_ok=False)
    print(f"🗂️ 正在归档现有产物 -> history/{archive_dir.name}")
    for item in existing:
        target = archive_dir / item.name
        try:
            shutil.move(str(item), str(target))
            print(f"   已归档 {item.name}")
        except Exception as exc:
            print(f"   ⚠️ 无法归档 {item}: {exc}")
    prune_history(history_dir, keep_versions)

def main():
    parser = argparse.ArgumentParser(description="Hello-Vibe Requirement Intake Pipeline")
    parser.add_argument("url", help="飞书文档 URL (Feishu Doc URL)")
    parser.add_argument("output_dir", help="输出目录 (e.g. docs/features/xxx/01_...)")
    parser.add_argument("--workers", type=int, default=10, help="解析并发线程数")
    parser.add_argument(
        "--archive-existing",
        action="store_true",
        help="[已废弃] 默认行为已改为自动归档，此参数保留仅为兼容",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="覆盖模式：检测到旧产物时直接删除（不归档）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制覆盖：跳过冲突检测，直接删除旧产物",
    )
    parser.add_argument(
        "--vlm-rollout",
        choices=["classic", "dual", "mify"],
        default=None,
        help="图片解析后端灰度模式（默认读取配置/回退 classic）",
    )
    parser.add_argument(
        "--vlm-insecure",
        action="store_true",
        help="将 VLM 解析切换为跳过 SSL 校验（MiFy 调用时生效）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续跑：跳过已完成的步骤（飞书文档下载、图片解析），从中断处继续",
    )
    # ignore 参数（透传给 fetch_feishu_doc.py）
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
    
    args = parser.parse_args()
    
    # Resolve paths
    base_dir = Path(args.output_dir).resolve()
    scripts_dir = Path(__file__).parent.resolve()
    python_exe = sys.executable
    
    # Sub-paths
    source_export_dir = base_dir / "source_export"
    static_dir = source_export_dir / "static"
    assets_summary = base_dir / "assets_summary.md"
    assets_analysis_dir = base_dir / "assets_analysis"
    prd_file = base_dir / "PRD.md"
    history_dir = base_dir / "history"

    print(f"📂 工作目录: {base_dir}")

    # 冲突处理逻辑（resume 模式下跳过归档）
    if not args.resume:
        if args.force or args.clean:
            # 覆盖模式：直接删除旧产物
            clean_existing_outputs(base_dir)
        elif has_existing_outputs(base_dir):
            # 默认模式：自动归档旧产物
            history_dir.mkdir(parents=True, exist_ok=True)
            archive_existing_outputs(base_dir, history_dir)
    else:
        print("🔄 [Resume] 断点续跑模式，保留现有产物")

    # --- 1. 下载文档 ---
    # Resume 模式：如果 source_export 目录已存在且有 .md 文件，跳过下载步骤
    skip_fetch = False
    if args.resume and source_export_dir.exists():
        md_files = list(source_export_dir.glob("*.md"))
        if md_files:
            skip_fetch = True
            print(f"🔄 [Resume] 跳过飞书文档下载（已存在 {len(md_files)} 个 .md 文件）")
    
    if not skip_fetch:
        # 合并 CLI 参数与配置文件（CLI 优先）
        ignore_sections = get_ignore_sections(args.ignore_sections)
        ignore_callouts = get_ignore_callouts(args.ignore_callouts)
        ignore_section_callouts = get_ignore_section_callouts(args.ignore_section_callouts)
        
        cmd_fetch = [
            python_exe, str(scripts_dir.parent / "infra" / "fetch_feishu_doc.py"),
            args.url,
            str(source_export_dir)
        ]
        # 透传 ignore 参数（已合并配置）
        if ignore_sections:
            cmd_fetch.extend(["--ignore-sections"] + ignore_sections)
        if ignore_callouts:
            cmd_fetch.extend(["--ignore-callouts"] + ignore_callouts)
        if ignore_section_callouts:
            cmd_fetch.extend(["--ignore-section-callouts"] + ignore_section_callouts)
        if not run_step(cmd_fetch, "下载飞书文档"):
            sys.exit(1)

    # --- 2. 并行解析资产 ---
    # Only run if static directory exists (it might be a text-only doc)
    if static_dir.exists():
        # 动态计算线程数：min(图片数量, 用户指定值, 20)，最少1个
        supported_exts = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
        image_count = sum(1 for f in static_dir.rglob('*') if f.suffix.lower() in supported_exts)
        dynamic_workers = max(1, min(image_count, args.workers, 20))
        print(f"📊 检测到 {image_count} 张图片，使用 {dynamic_workers} 个并行线程")
        
        # VLM 入口（支持 classic/dual/mify，通过 config 或参数决定）
        rollout_mode = args.vlm_rollout
        if not rollout_mode:
            # 从 ~/.vaf/config 读取 ROLLOUT_MODE
            rollout_mode = get_vlm_rollout_mode()
        if rollout_mode not in {"classic", "dual", "mify"}:
            rollout_mode = "mify"

        # 生成日志文件路径（与 Qwen 直连模式保持一致的命名规范）
        log_date = datetime.now().strftime("%Y%m%d")
        if rollout_mode == "mify":
            vlm_log_file = base_dir / f"mify_parse_{log_date}.log"
        else:
            vlm_log_file = base_dir / f"qwen_parse_{log_date}.log"

        cmd_analyze = [
            python_exe, str(scripts_dir / "process_assets_with_vlm.py"),
            "--rollout-mode", rollout_mode,
            "--input", str(static_dir),
            "--output", str(assets_summary),
            "--timeout", "300",
            "--workers", str(dynamic_workers),
            "--log-file", str(vlm_log_file),
        ]
        if args.vlm_insecure:
            cmd_analyze.append("--insecure")
        if args.resume:
            cmd_analyze.append("--resume")
        if not run_step(cmd_analyze, "并行解析图片资产（VLM）"):
            sys.exit(1)
    else:
        print("⚠️ 未检测到静态资源目录，跳过图片解析步骤。")

    # --- 3. 生成整合版 PRD（精简模式，行内摘要注释）---
    cmd_merge = [
        python_exe, str(scripts_dir.parent / "prd" / "merge_prd_assets.py"),
        "--source", str(source_export_dir),
        "--analysis", str(assets_analysis_dir),
        "--output", str(prd_file),
        "--mode", "compact"
    ]
    if not run_step(cmd_merge, "生成整合版 PRD"):
        sys.exit(1)

    # --- 3.5 P01 自动质检 ---
    # 注意：validate_prd_engineering.py 是 P03 阶段的校验脚本，不适用于 P01 原始 PRD
    # P01 阶段只检查：元数据完整性、图片解析覆盖率、PRD.md 存在
    print("\n🔍 [Step] 执行自动质检...")
    errors = []
    metadata_path = base_dir / "metadata.json"
    metadata = {}
    if not metadata_path.exists():
        errors.append("❌ Metadata 元数据文件缺失")
    else:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"❌ Metadata 解析失败: {exc}")

    failed_images = []
    if metadata:
        if not metadata.get("doc_url"):
            errors.append("❌ Metadata 缺少 doc_url")
        if not metadata.get("download_time"):
            errors.append("❌ Metadata 缺少 download_time")
        failed_images = metadata.get("failed_images") or []
        if failed_images:
            preview = ", ".join(failed_images[:3])
            suffix = "..." if len(failed_images) > 3 else ""
            errors.append(f"❌ 飞书图片下载失败 {len(failed_images)} 张: {preview}{suffix}")
    if not prd_file.exists():
        errors.append("❌ PRD.md 主文档生成失败")

    download_log_lines = []
    download_log_name = metadata.get("download_log") if metadata else None
    if download_log_name:
        log_candidate = base_dir / download_log_name
        if not log_candidate.exists():
            log_candidate = history_dir / download_log_name
        if log_candidate.exists():
            try:
                log_text = log_candidate.read_text(encoding="utf-8")
                download_log_lines = [
                    line.strip() for line in log_text.splitlines()
                    if "Failed to download image" in line
                ]
            except Exception as exc:
                errors.append(f"❌ 无法读取下载日志 {download_log_name}: {exc}")
    if download_log_lines and not failed_images:
        joined = "; ".join(download_log_lines[:2])
        suffix = "..." if len(download_log_lines) > 2 else ""
        errors.append(f"❌ 下载日志检测到图片失败: {joined}{suffix}")

    if static_dir.exists():
        # Check 1:1 mapping
        imgs = {f.stem for f in static_dir.glob('*') if f.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.bmp'}}
        mds = {f.stem for f in assets_analysis_dir.glob('*.md')}
        missing = imgs - mds
        if missing:
            errors.append(f"❌ 缺失解析: {len(missing)} 张 (如: {list(missing)[:2]}...)")
    
    if errors:
        print("\n".join(errors))
        print("❌ 质检未通过，请检查上述错误。")
        sys.exit(1)
    else:
        print("✅ 所有检查通过：元数据完整，资产解析 1:1 匹配，PRD 已生成。")

    # --- 5. 清理日志文件 ---
    # 质检通过后删除 qwen_parse_*.log 和 fetch_feishu_doc_*.log
    deleted_logs = []
    for log_pattern in ["qwen_parse_*.log", "fetch_feishu_doc_*.log", "mify_parse_*.log"]:
        for log_file in base_dir.glob(log_pattern):
            try:
                log_file.unlink()
                deleted_logs.append(log_file.name)
            except Exception as e:
                print(f"⚠️ 无法删除日志文件 {log_file.name}: {e}")
    
    if deleted_logs:
        print(f"🧹 已清理日志文件: {', '.join(deleted_logs)}")

    # --- 6. 醒目完成提醒 ---
    print("\n" + "=" * 60)
    print("🎉🎉🎉  P01 需求采集阶段任务完成！ 🎉🎉🎉")
    print("=" * 60)
    print(f"\n📁 输出路径: {prd_file}")
    print("\n📄 产出物清单:")
    print("   - PRD.md（整合后的 PRD）")
    print("   - assets_analysis/*.md（图片解析结果）")
    print("   - assets_summary.md（图片解析汇总表）")
    print("   - metadata.json（下载元数据）")
    print("\n👉 建议进入 P02 知识库加载 阶段")
    print("   提示词: .vibe/core/prompts/P02_context_analysis.md")
    print("=" * 60)

if __name__ == "__main__":
    main()
