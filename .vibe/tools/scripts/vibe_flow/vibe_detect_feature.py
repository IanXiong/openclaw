#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Vibe Detect Feature CLI - Feature 上下文检测工具

检测当前工作区的 feature 上下文，无需任何入参。

使用方式：
    python vibe_detect_feature.py

行为：
    - 0 个 feature: 输出提示，退出码 1
    - 1 个 feature: 自动选择，输出 "✅ 已选择需求：xxx"
    - N 个 feature: 交互式列表，用户选择
"""

import sys
import io

# 跨平台 Unicode 输出支持 (Windows GBK 兼容)
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

from pathlib import Path
from typing import Optional, List, Tuple

# 导入 vibe_helpers 通用工具函数
from vibe_helpers import (
    load_json_file,
    find_project_root,
    get_workspace_type,
)


# ============ Feature 检测 ============

def get_features_dir() -> Tuple[Optional[Path], str]:
    """获取 features 目录路径"""
    root = find_project_root()
    ws_type = get_workspace_type()
    
    if ws_type == "hub":
        features_dir = root / "features"
        return (features_dir if features_dir.exists() else None, ws_type)
    elif ws_type == "service":
        features_dir = root / "docs" / "features"
        return (features_dir if features_dir.exists() else None, ws_type)
    
    return (None, ws_type)


def scan_features() -> List[str]:
    """扫描并返回所有有效的 feature 名称列表"""
    features_dir, _ = get_features_dir()
    
    if not features_dir or not features_dir.exists():
        return []
    
    features = []
    for item in features_dir.iterdir():
        if item.name.startswith('.'):
            continue
        if item.is_dir():
            features.append(item.name)
    
    return sorted(features)


def get_feature_path(feature_name: str) -> Optional[Path]:
    """获取指定 feature 的文档目录路径"""
    root = find_project_root()
    ws_type = get_workspace_type()
    
    if ws_type == "hub":
        path = root / "features" / feature_name / "docs"
        return path if path.exists() else None
    elif ws_type == "service":
        path = root / "docs" / "features" / feature_name
        return path if path.exists() else None
    
    return None


# ============ 用户交互 ============

def prompt_user_selection(features: List[str], max_retries: int = 3) -> Optional[str]:
    """提示用户选择 feature"""
    print("\n🔍 检测到多个需求，请选择：\n")
    for i, name in enumerate(features, 1):
        print(f"  [{i}] {name}")
    print()
    
    retries = 0
    while retries < max_retries:
        try:
            user_input = input(f"请输入编号 [1-{len(features)}]（输入 q 退出）：").strip()
            
            if user_input.lower() == 'q':
                return None
            
            index = int(user_input)
            if 1 <= index <= len(features):
                return features[index - 1]
            else:
                print(f"⚠️ 无效编号，请输入 1-{len(features)} 之间的数字")
                retries += 1
        except ValueError:
            print("⚠️ 无效输入，请输入数字编号")
            retries += 1
    
    print("❌ 多次输入无效，请通过 @vaf_starter.md 重新开始")
    return None


# ============ 输出 ============

def print_no_feature_hint():
    """打印无 feature 时的提示信息"""
    ws_type = get_workspace_type()
    
    print("\n❌ 未检测到任何需求\n")
    print("请先执行初始化命令：\n")
    
    if ws_type == "hub":
        print("  vaf init hub-feature --feature <需求名称>\n")
    else:
        print("  vaf init service-feature --hub-path <Hub路径> --feature <需求名称>\n")


def print_auto_select_hint(feature_name: str):
    """打印自动选择时的提示信息"""
    print(f"\n📌 自动检测到唯一需求：{feature_name}")
    print(f"   └─ 将使用此需求继续执行")
    print()
    print("💡 如需切换，请通过 @vaf_starter.md 重新选择")
    print("────────────────────────────────────────────────")


def print_selected(feature_name: str):
    """打印选择确认信息（关键输出，用于会话上下文识别）"""
    print(f"\n✅ 已选择需求：{feature_name}\n")


# ============ 主逻辑 ============

def detect_feature() -> int:
    """检测 feature 上下文
    
    Returns:
        退出码：0 成功，1 失败
    """
    ws_type = get_workspace_type()
    
    # 检查工作区类型
    if ws_type == "unknown":
        print("❌ 无法识别工作区类型，请确认当前目录包含 .vibe/workspace.json")
        return 1
    
    # 扫描 features
    features = scan_features()
    
    # 根据数量处理
    if len(features) == 0:
        print_no_feature_hint()
        return 1
    
    elif len(features) == 1:
        # 单一 feature，自动选择
        feature_name = features[0]
        print_auto_select_hint(feature_name)
        print_selected(feature_name)
        return 0
    
    else:
        # 多个 feature，用户选择
        selected = prompt_user_selection(features)
        if selected:
            print_selected(selected)
            return 0
        else:
            print("❌ 未选择需求，流程终止")
            return 1


# ============ CLI 入口 ============

def main():
    sys.exit(detect_feature())


if __name__ == "__main__":
    main()
