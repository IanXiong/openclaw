#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段输入准备脚本
将上游阶段产物拷贝到当前阶段工作目录，保持原有目录结构
用于概要设计阶段准备输入数据
"""

import sys

# Windows 编码兼容：确保 stdout/stderr 使用 UTF-8，避免 emoji 输出时 GBK 编码错误
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import os
import shutil
from pathlib import Path


def prepare_stage_inputs(feature_dir: str) -> bool:
    """
    准备概要设计阶段的输入数据
    将服务拆分阶段产物拷贝到概要设计工作目录
    
    Args:
        feature_dir: 需求目录路径，例如 features/feature-badMaterial/docs
        
    Returns:
        bool: 拷贝成功返回True，失败返回False
    """
    # 构建路径
    upstream_dir = Path(feature_dir) / "P04_service_split"
    target_dir = Path(feature_dir) / "P05_preliminary_design"
    
    # 验证上游目录存在
    if not upstream_dir.exists():
        print(f"❌ 上游阶段目录不存在: {upstream_dir}")
        return False
    
    # 创建当前阶段工作目录
    target_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"📂 源目录: {upstream_dir}")
    print(f"📂 目标目录: {target_dir}")
    print()
    
    # 需要拷贝的文件列表
    files_to_copy = [
        "service_split_decision.toon",
        "execution_manifest.json"
    ]
    
    # 拷贝文件
    copied_files = 0
    for file_name in files_to_copy:
        src_file = upstream_dir / file_name
        dst_file = target_dir / file_name
        
        if src_file.exists():
            shutil.copy2(src_file, dst_file)
            print(f"✅ 拷贝文件: {file_name}")
            copied_files += 1
        else:
            print(f"⚠️  文件不存在，跳过: {file_name}")
    
    # 拷贝services目录
    src_services_dir = upstream_dir / "services"
    dst_services_dir = target_dir / "services"
    
    if src_services_dir.exists() and src_services_dir.is_dir():
        # 删除目标services目录（如果存在）
        if dst_services_dir.exists():
            shutil.rmtree(dst_services_dir)
        
        # 递归拷贝整个services目录
        shutil.copytree(src_services_dir, dst_services_dir)
        
        # 统计拷贝的服务数量
        service_count = len([d for d in dst_services_dir.iterdir() if d.is_dir()])
        print(f"✅ 拷贝services目录: {service_count} 个服务")
        
        # 列出拷贝的服务
        for service_dir in sorted(dst_services_dir.iterdir()):
            if service_dir.is_dir():
                service_inputs = service_dir / "service_inputs.toon"
                if service_inputs.exists():
                    print(f"   - {service_dir.name}/service_inputs.toon")
    else:
        print(f"⚠️  services目录不存在，跳过")
    
    print()
    print(f"✅ 上游产物拷贝完成")
    print(f"📊 统计: 拷贝了 {copied_files} 个文件 + services目录")
    
    return True


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("用法: python prepare_stage_inputs.py <feature_dir>")
        print("示例: python prepare_stage_inputs.py d:/AI Projects/vibe-xmstore-requirements/features/feature-badMaterial/docs")
        sys.exit(1)
    
    feature_dir = sys.argv[1]
    
    print("=" * 60)
    print("阶段输入准备脚本")
    print("=" * 60)
    print()
    
    success = prepare_stage_inputs(feature_dir)
    
    if success:
        print()
        print("🎉 拷贝成功！")
        sys.exit(0)
    else:
        print()
        print("❌ 拷贝失败！")
        sys.exit(1)


if __name__ == "__main__":
    main()
