#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Git 提交流程状态机脚本

用于阶段放行后的 Git 提交流程，封装为状态机驱动的交互式脚本，防止 AI 跳过 Git 步骤。

使用方式：
    python vibe_git_flow.py --phase P02 --feature <feature_name> [--commit-msg "feat(P02): ..."]

状态机流程：
    GIT_CHECK → GIT_CONFIRM → GIT_COMMIT → COMPLETE

职责边界：
    - AI（脚本外部）：产出检测、放行确认、状态更新、菜单刷新
    - 脚本内部：Git 检测、Git 确认、Git 提交

输出格式：JSON
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
import argparse
import subprocess
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone, timedelta

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='[vibe_git_flow] %(message)s'
)
logger = logging.getLogger("vibe_git_flow")

# 阶段详情（用于显示阶段名称）
PHASE_DETAILS = {
    "P01": {"name": "需求采集"},
    "P02": {"name": "知识库加载"},
    "P03": {"name": "PRD标准化"},
    "P04": {"name": "服务分解"},
    "P05": {"name": "概要设计"},
    "P06-A": {"name": "方案协调"},
    "P06-B": {"name": "测试协调"},
    "P06-C": {"name": "测试验收"},
    "S01": {"name": "技术设计"},
    "S02": {"name": "任务拆解"},
    "S03": {"name": "编码实现"},
    "S04": {"name": "单元测试"},
    "S05": {"name": "集成计划"},
    "S06": {"name": "代码审查"},
    "S07": {"name": "集成执行"},
    "T01": {"name": "E2E测试"},
}


class GitFlowStateMachine:
    """Git 提交流程状态机"""
    
    # 状态定义（4 个状态，仅包含 Git 相关流程）
    STATES = [
        "GIT_CHECK",      # Git 仓库检测
        "GIT_CONFIRM",    # Git 提交确认
        "GIT_COMMIT",     # 执行 Git 提交
        "COMPLETE",       # 终态
    ]
    
    def __init__(self, phase: str, feature: str, commit_msg: Optional[str] = None):
        self.phase = phase
        self.feature = feature
        self.commit_msg = commit_msg or f"feat({feature}): {phase} 已放行"
        self.state = "GIT_CHECK"
        self.cwd = Path.cwd()
        
        # 结果数据
        self.result = {
            "success": True,
            "phase": phase,
            "phase_name": PHASE_DETAILS.get(phase, {}).get("name", phase),
            "git_committed": False,
            "git_skipped": False,
            "git_error": None,
            "message": ""
        }
        
        # Git 检测结果缓存
        self.git_info = {}
        self.changed_files = []
    
    def run(self) -> dict:
        """运行状态机直到终态"""
        while self.state != "COMPLETE":
            handler = getattr(self, f"handle_{self.state.lower()}")
            next_state = handler()
            if next_state != self.state:
                logger.info(f"状态: {self.state} → {next_state}")
            self.state = next_state
        return self.result
    
    def handle_git_check(self) -> str:
        """检测 Git 仓库和变更"""
        # 调用 vibe_git_commit.py --mode check
        script_path = Path(__file__).parent / "vibe_git_commit.py"
        try:
            result = subprocess.run(
                [sys.executable, str(script_path), "--mode", "check", "--feature", self.feature],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=self.cwd
            )
            
            if result.returncode != 0 and not result.stdout.strip():
                logger.warning(f"vibe_git_commit.py check 失败: {result.stderr}")
                git_result = self._check_git_directly()
            else:
                git_result = json.loads(result.stdout) if result.stdout.strip() else {}
            
        except Exception as e:
            # 直接检测 Git 仓库
            git_result = self._check_git_directly()
        
        self.git_info = git_result
        
        # 判断是否为 Git 仓库
        if not git_result.get("is_git_repo", False):
            print("\nℹ️ 当前目录非 Git 仓库，跳过 Git 提交流程")
            self.result["git_skipped"] = True
            self.result["message"] = "非 Git 仓库，跳过"
            return "COMPLETE"
        
        # 获取变更文件
        self.changed_files = git_result.get("changed_files", [])
        
        if not self.changed_files:
            print("\nℹ️ 无变更文件需要提交，跳过 Git 流程")
            self.result["git_skipped"] = True
            self.result["message"] = "无变更，跳过"
            return "COMPLETE"
        
        return "GIT_CONFIRM"
    
    def _check_git_directly(self) -> dict:
        """直接检测 Git 仓库（备用方案）"""
        try:
            # 检查是否是 Git 仓库
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=self.cwd
            )
            if result.returncode != 0:
                return {"is_git_repo": False}
            
            # 获取变更文件
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=self.cwd
            )
            
            changed_files = []
            if result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    if line.strip():
                        parts = line.strip().split(maxsplit=1)
                        if len(parts) >= 2:
                            changed_files.append(parts[1].strip('"'))
            
            return {
                "is_git_repo": True,
                "changed_files": changed_files,
                "changed_count": len(changed_files)
            }
        except Exception:
            return {"is_git_repo": False}
    
    def handle_git_confirm(self) -> str:
        """显示 Git 提交信息并直接提交"""
        # 显示提交信息
        self._render_git_info()
        
        # 直接进入提交流程
        logger.info("自动使用默认 commit message 提交")
        return "GIT_COMMIT"
    
    def _render_git_info(self):
        """显示 Git 提交信息"""
        phase_name = PHASE_DETAILS.get(self.phase, {}).get("name", self.phase)
        
        print("\n" + "─" * 65)
        print("📦 提交变更 (AI 生成)")
        print()
        print(f"变更文件 ({len(self.changed_files)}):")
        for f in self.changed_files[:10]:  # 最多显示 10 个
            print(f"  M  {f}")
        if len(self.changed_files) > 10:
            print(f"  ... 还有 {len(self.changed_files) - 10} 个文件")
        
        print()
        print("提交信息:")
        print("┌" + "─" * 63 + "┐")
        for line in self.commit_msg.split("\n"):
            print(f"│  {line:<59} │")
        print("└" + "─" * 63 + "┘")
        print()
    
    
    def handle_git_commit(self) -> str:
        """执行 Git 提交"""
        print("\n执行 Git 提交...")
        
        # 调用 vibe_git_commit.py --mode commit
        script_path = Path(__file__).parent / "vibe_git_commit.py"
        try:
            result = subprocess.run(
                [sys.executable, str(script_path),
                 "--mode", "commit",
                 "--feature", self.feature,
                 "--phase", self.phase,
                 "--commit-msg", self.commit_msg],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                cwd=self.cwd
            )
            
            if result.returncode != 0 and not result.stdout.strip():
                logger.error(f"vibe_git_commit.py commit 失败: {result.stderr}")
            
            git_result = json.loads(result.stdout) if result.stdout.strip() else {}
            
        except Exception as e:
            git_result = {"success": False, "message": str(e)}
        
        # 显示执行步骤
        steps = git_result.get("steps", [])
        for step in steps:
            status_icon = "✅" if step.get("status") == "success" else "❌" if step.get("status") == "failed" else "⏭️"
            print(f"  {status_icon} {step.get('action', 'unknown')}")
        
        if git_result.get("success", False):
            print("\n✅ Git 提交成功")
            self.result["git_committed"] = True
            self.result["message"] = "Git 已提交"
        else:
            # 失败处理
            self._handle_git_failure(git_result)
        
        return "COMPLETE"
    
    def _handle_git_failure(self, git_result: dict):
        """处理 Git 失败"""
        error_msg = git_result.get("message", "未知错误")
        manual_cmd = git_result.get("manual_command", "")
        
        # 找出失败的步骤
        failed_step = "unknown"
        for step in git_result.get("steps", []):
            if step.get("status") == "failed":
                failed_step = step.get("action", "unknown")
                if step.get("error"):
                    error_msg = step.get("error")
                break
        
        print()
        print("⚠️ Git 提交失败")
        print()
        print(f"失败步骤: {failed_step}")
        print(f"错误信息: {error_msg}")
        print()
        print("💡 请手动解决后执行:")
        print(f"   cd {self.cwd}")
        
        if "conflict" in error_msg.lower() or "CONFLICT" in error_msg:
            print("   git status")
            print("   # 解决冲突后:")
            print("   git add <冲突文件>")
            print("   git rebase --continue")
            print("   git push")
        elif manual_cmd:
            print(f"   {manual_cmd}")
        else:
            print("   git status")
            print("   git add .")
            print(f"   git commit -m \"{self.commit_msg.split(chr(10))[0]}\"")
            print("   git push")
        
        print()
        input("按回车继续...")
        
        self.result["git_committed"] = False
        self.result["git_error"] = error_msg
        self.result["message"] = "Git 提交失败需手动处理"


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Git 提交流程状态机脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python vibe_git_flow.py --phase P02 --feature inventory_optimize
  python vibe_git_flow.py --phase S01 --feature my_feature --commit-msg "feat(S01): 技术设计完成"
"""
    )
    
    parser.add_argument("--phase", "-p", required=True,
                        help="阶段 ID（P01-P06, S01-S07, T01）")
    parser.add_argument("--feature", "-f", required=True,
                        help="需求名称")
    parser.add_argument("--commit-msg", "-m",
                        help="预生成的 commit message（用户可修改）")
    
    args = parser.parse_args()
    
    # 创建状态机并运行
    state_machine = GitFlowStateMachine(
        phase=args.phase,
        feature=args.feature,
        commit_msg=args.commit_msg
    )
    
    result = state_machine.run()
    
    # 添加 next_action 指引
    result["next_action"] = {
        "type": "auto_continue",
        "instruction": "刷新菜单 (--mode progress)"
    }
    
    # 输出 JSON 结果
    print()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    # 返回退出码
    return 0 if result.get("success", False) else 1


if __name__ == "__main__":
    sys.exit(main())
