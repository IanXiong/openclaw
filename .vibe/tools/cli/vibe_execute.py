#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Vibe Execute - 阶段执行器（自动化封装）

功能：
- 自动执行前置动作（门禁校验 + 状态上报）
- 加载阶段 Prompt 并返回给 AI
- 自动执行后置动作（状态上报 + 放行确认）
- 自动执行 Git 提交和菜单刷新

使用方式：
    # 执行阶段（自动完成前置动作）
    python vibe_execute.py execute P01 --feature flash-sale
    
    # 完成阶段（自动完成后置动作）
    python vibe_execute.py finalize P01 --feature flash-sale
    
    # 放行确认（自动完成 Git 提交和菜单刷新）
    python vibe_execute.py approve P01 --feature flash-sale --turns 3
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
from pathlib import Path
from typing import Optional, Dict, List

# 导入埋点上报模块
HAS_TELEMETRY_MODULE = False
try:
    scripts_dir = Path(__file__).parent.parent / "scripts" / "vibe_flow"
    if scripts_dir.exists() and str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from vaf_telemetry import safe_report_telemetry
    HAS_TELEMETRY_MODULE = True
except ImportError:
    pass


class PhaseExecutor:
    """阶段执行器"""
    
    def __init__(self, phase: str, feature: str):
        self.phase = phase
        self.feature = feature
        self.root = self.find_project_root()
        self.cli_dir = self.root / "tools" / "cli"
        self.scripts_dir = self.root / "tools" / "scripts" / "vibe_flow"
        self.prompts_dir = self.root / "core" / "prompts"
    
    def execute(self) -> Dict:
        """执行阶段（前置动作 + 加载 Prompt）"""
        result = {
            "success": False,
            "phase": self.phase,
            "feature": self.feature,
            "steps": []
        }
        
        print(f"\n🚀 开始执行 {self.phase} 阶段...\n")
        
        # Step 1: 门禁校验
        print("📋 Step 1/3: 门禁校验...")
        gate_result = self.check_gate()
        result["steps"].append({
            "name": "门禁校验",
            "success": gate_result["success"],
            "message": gate_result.get("message", "")
        })
        
        if not gate_result["success"]:
            result["message"] = f"❌ 门禁校验失败: {gate_result.get('reason', '')}"
            print(result["message"])
            return result
        
        print("   ✅ 门禁校验通过")
        
        # Step 2: 状态上报（开始执行）
        print("\n📋 Step 2/3: 状态上报 (start)...")
        start_result = self.update_status("start")
        result["steps"].append({
            "name": "状态上报(start)",
            "success": start_result["success"],
            "message": start_result.get("message", "")
        })
        
        if not start_result["success"]:
            result["message"] = "❌ 状态上报失败"
            print(result["message"])
            return result
        
        print("   ✅ 状态已更新为 in_progress")
        
        # Step 3: 加载 Prompt
        print("\n📋 Step 3/3: 加载 Prompt...")
        prompt_path = self.find_prompt_file()
        if not prompt_path:
            result["message"] = f"❌ 未找到 {self.phase} 的 Prompt 文件"
            print(result["message"])
            return result
        
        result["steps"].append({
            "name": "加载 Prompt",
            "success": True,
            "message": f"已加载 {prompt_path.name}"
        })
        
        print(f"   ✅ 已加载 {prompt_path.name}")
        
        # 返回成功
        result["success"] = True
        result["prompt_path"] = str(prompt_path)
        result["message"] = f"\n✅ 前置动作完成，请执行 {self.phase} 核心任务"
        result["next_action"] = "execute_core_task"
        
        print(result["message"])
        print(f"\n💡 核心任务完成后，请调用: python .vibe/tools/cli/vibe_execute.py finalize {self.phase} --feature {self.feature}\n")
        
        return result
    
    def finalize(self) -> Dict:
        """完成阶段（后置动作）"""
        result = {
            "success": False,
            "phase": self.phase,
            "feature": self.feature,
            "steps": []
        }
        
        print(f"\n🎯 完成 {self.phase} 阶段...\n")
        
        # Step 1: 状态上报（待确认）
        print("📋 Step 1/2: 状态上报 (pending)...")
        pending_result = self.update_status("pending")
        result["steps"].append({
            "name": "状态上报(pending)",
            "success": pending_result["success"],
            "message": pending_result.get("message", "")
        })
        
        if not pending_result["success"]:
            result["message"] = "❌ 状态上报失败"
            print(result["message"])
            return result
        
        print("   ✅ 状态已更新为 pending_review")
        
        # Step 2: 渲染放行确认框
        print("\n📋 Step 2/2: 渲染放行确认框...")
        approval_result = self.render_approval()
        result["steps"].append({
            "name": "渲染放行确认框",
            "success": approval_result["success"],
            "message": approval_result.get("message", "")
        })
        
        if approval_result["success"]:
            print("   ✅ 放行确认框已渲染")
        
        result["success"] = True
        result["message"] = "\n✅ 后置动作完成，等待用户放行确认"
        result["next_action"] = "wait_approval"
        
        print(result["message"])
        print(f"\n💡 用户确认后，请调用: python .vibe/tools/cli/vibe_execute.py approve {self.phase} --feature {self.feature} --turns <N>\n")
        
        return result
    
    def approve(self, turns: int = 1, model: str = None) -> Dict:
        """放行确认（状态上报 + 埋点上报 + Git 提交 + 菜单刷新）"""
        result = {
            "success": False,
            "phase": self.phase,
            "feature": self.feature,
            "steps": []
        }
        
        print(f"\n🎉 放行 {self.phase} 阶段...\n")
        
        # Step 1: 状态上报（已放行，含模型信息）
        print("📋 Step 1/4: 状态上报 (approve)...")
        approve_result = self.update_status("approve", turns=turns, model=model)
        result["steps"].append({
            "name": "状态上报(approve)",
            "success": approve_result["success"],
            "message": approve_result.get("message", "")
        })
        
        if not approve_result["success"]:
            result["message"] = "❌ 放行失败"
            print(result["message"])
            return result
        
        print("   ✅ 状态已更新为 approved")
        
        # Step 2: 埋点上报（含模型信息）
        print("\n📋 Step 2/4: 埋点上报...")
        if HAS_TELEMETRY_MODULE:
            try:
                safe_report_telemetry(self.phase, self.feature, turns=turns, model=model)
                print(f"   ✅ 埋点已上报" + (f"（模型: {model}）" if model else ""))
                result["steps"].append({
                    "name": "埋点上报",
                    "success": True,
                    "message": f"模型: {model}" if model else ""
                })
            except Exception as e:
                print(f"   ⚠️ 埋点上报失败（不影响主流程）: {e}")
                result["steps"].append({
                    "name": "埋点上报",
                    "success": False,
                    "message": str(e)
                })
        else:
            print("   ⏭️ 埋点模块未加载，跳过")
            result["steps"].append({
                "name": "埋点上报",
                "success": True,
                "message": "模块未加载，跳过"
            })
        
        # Step 3: Git 提交（由 vibe_sync.py 自动完成）
        print("\n📋 Step 3/4: Git 提交...")
        print("   ✅ Git 提交已由 vibe_sync.py 自动完成")
        result["steps"].append({
            "name": "Git 提交",
            "success": True,
            "message": "由 vibe_sync.py 自动完成"
        })
        
        # Step 4: 刷新菜单
        print("\n📋 Step 4/4: 刷新菜单...")
        menu_result = self.refresh_menu()
        result["steps"].append({
            "name": "刷新菜单",
            "success": menu_result["success"],
            "message": menu_result.get("message", "")
        })
        
        if menu_result["success"]:
            print("   ✅ 菜单已刷新")
        
        result["success"] = True
        result["message"] = f"\n✅ {self.phase} 已放行，下一阶段已解锁"
        result["next_action"] = "show_menu"
        
        print(result["message"])
        
        return result
    
    # ============ 内部方法 ============
    
    def check_gate(self) -> Dict:
        """门禁校验"""
        validate_script = self.scripts_dir / "vibe_validate_choice.py"
        if not validate_script.exists():
            return {
                "success": False,
                "reason": f"门禁校验脚本不存在: {validate_script}"
            }
        
        cmd = [
            sys.executable,
            str(validate_script),
            "--choice", self.phase,
            "--feature", self.feature
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        
        if result.returncode == 0:
            return {"success": True}
        else:
            return {
                "success": False,
                "reason": result.stdout or result.stderr
            }
    
    def update_status(self, action: str, turns: int = None, model: str = None) -> Dict:
        """状态上报"""
        sync_script = self.cli_dir / "vibe_sync.py"
        if not sync_script.exists():
            return {
                "success": False,
                "message": f"状态同步脚本不存在: {sync_script}"
            }
        
        cmd = [
            sys.executable,
            str(sync_script),
            "report", self.phase,
            "--action", action,
            "--feature", self.feature
        ]
        
        if turns is not None:
            cmd.extend(["--turns", str(turns)])
        
        if model is not None:
            cmd.extend(["--model", model])
        
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        
        return {
            "success": result.returncode == 0,
            "message": result.stdout
        }
    
    def find_prompt_file(self) -> Optional[Path]:
        """查找阶段 Prompt 文件"""
        # 标准化 phase_id：P06-A → P06_A（连字符转下划线）
        phase_normalized = self.phase.replace("-", "_")
        
        # 尝试精确匹配（使用标准化后的 phase_id）
        matches = list(self.prompts_dir.glob(f"{phase_normalized}_*.md"))
        
        if matches:
            return matches[0]
        
        # 尝试原始 phase_id 匹配（兼容旧格式）
        matches = list(self.prompts_dir.glob(f"{self.phase}_*.md"))
        if matches:
            return matches[0]
        
        # 尝试不区分大小写
        for file in self.prompts_dir.glob("*.md"):
            if file.stem.upper().startswith(phase_normalized.upper()):
                return file
        
        return None
    
    def render_approval(self) -> Dict:
        """渲染放行确认框"""
        menu_script = self.scripts_dir / "vibe_render_menu.py"
        if not menu_script.exists():
            return {
                "success": False,
                "message": f"菜单渲染脚本不存在: {menu_script}"
            }
        
        cmd = [
            sys.executable,
            str(menu_script),
            "--mode", "approval",
            "--phase", self.phase,
            "--feature", self.feature
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        
        return {
            "success": result.returncode == 0,
            "message": result.stdout
        }
    
    def refresh_menu(self) -> Dict:
        """刷新菜单"""
        menu_script = self.scripts_dir / "vibe_render_menu.py"
        if not menu_script.exists():
            return {
                "success": False,
                "message": f"菜单渲染脚本不存在: {menu_script}"
            }
        
        cmd = [
            sys.executable,
            str(menu_script),
            "--mode", "progress",
            "--feature", self.feature,
            "--json"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        
        return {
            "success": result.returncode == 0,
            "message": result.stdout
        }
    
    def find_project_root(self) -> Path:
        """查找 VAF 源码仓根目录（包含 tools/cli 的目录）"""
        # 从脚本所在位置向上查找
        script_path = Path(__file__).resolve()
        current = script_path.parent.parent.parent  # 从 .vibe/tools/cli/ 向上两级
        
        # 验证是否为 VAF 源码仓（检查关键目录）
        if (current / "tools" / "cli").exists() and \
           (current / "core" / "prompts").exists():
            return current
        
        # 如果从脚本路径找不到，尝试从当前工作目录查找
        current = Path.cwd()
        while current != current.parent:
            if (current / "tools" / "cli").exists() and \
               (current / "core" / "prompts").exists():
                return current
            current = current.parent
        
        # 最后尝试：假设在 Hub 目录，通过环境变量或固定路径找 VAF 源码仓
        # 这里返回脚本所在的 VAF 源码仓路径
        return script_path.parent.parent.parent


def main():
    parser = argparse.ArgumentParser(
        description="Vibe 阶段执行器 - 自动化封装前置/后置动作"
    )
    parser.add_argument(
        "action",
        choices=["execute", "finalize", "approve"],
        help="执行动作: execute(前置动作), finalize(后置动作), approve(放行确认)"
    )
    parser.add_argument("phase", help="阶段 ID (如 P01, S01)")
    parser.add_argument("--feature", required=True, help="需求名称")
    parser.add_argument("--turns", type=int, help="对话轮次（仅 approve 时使用）")
    parser.add_argument("--model", type=str, help="AI 模型名称（仅 approve 时使用）")
    
    args = parser.parse_args()
    
    executor = PhaseExecutor(args.phase, args.feature)
    
    if args.action == "execute":
        result = executor.execute()
    elif args.action == "finalize":
        result = executor.finalize()
    elif args.action == "approve":
        result = executor.approve(args.turns or 1, getattr(args, 'model', None))
    else:
        print(f"❌ 未知动作: {args.action}")
        sys.exit(1)
    
    # 输出 JSON 结果（供脚本解析）
    print("\n" + "="*60)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("="*60)
    
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
