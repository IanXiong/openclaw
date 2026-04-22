#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
larkkit 依赖管理模块

统一 larkkit 的检测、安装、升级逻辑，与 vaf.py 保持一致。
使用 uv 作为包管理工具。

使用方式：
    from larkkit_deps import ensure_larkkit
    
    if ensure_larkkit():
        import larkkit
        # 使用 larkkit...
"""

import sys

# Windows 编码兼容：确保 stdout/stderr 使用 UTF-8，避免 emoji 输出时 GBK 编码错误
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import subprocess
from pathlib import Path
from typing import Optional, Tuple


# ============ 配置 ============
# larkkit 安装源（与 vaf.py 保持一致）
LARKKIT_INSTALL_SPEC_SSH = "larkkit @ git+ssh://git@git.n.xiaomi.com/mit-ai-galaxy/larkkit.git@main"
LARKKIT_INSTALL_SPEC_HTTPS = "larkkit @ git+https://git.n.xiaomi.com/mit-ai-galaxy/larkkit.git@main"
LARKKIT_REPO_SHORT = "git.n.xiaomi.com/mit-ai-galaxy/larkkit"

# uv 安装超时（秒）
UV_INSTALL_TIMEOUT = 60

# 全局 verbose 标志（控制详细日志输出）
_verbose = False

# 缓存的协议类型（避免重复检测）
_detected_protocol: Optional[str] = None  # "ssh" 或 "https"


def _get_vaf_root() -> Optional[Path]:
    """获取 VAF 根目录"""
    # larkkit_deps.py 位于 .vibe/tools/scripts/infra/
    current = Path(__file__).resolve()
    # 向上查找包含 VERSION 文件的目录
    for parent in [current.parent.parent.parent.parent, current.parent.parent.parent]:
        if (parent / "VERSION").exists() and (parent / "tools" / "vaf.py").exists():
            return parent
    return None


def _check_ssh_agent_ready() -> bool:
    """检测 SSH 是否可以无交互使用（密钥无 passphrase 或 Agent 已加载）
    
    Returns:
        True: SSH 可以无交互使用
        False: SSH 需要交互输入 passphrase
    """
    import time
    _log("   [SSH检测] 开始检测 SSH 连接...", verbose_only=True)
    start_time = time.time()
    
    try:
        # 尝试连接 GitLab，超时 5 秒
        # 使用 StrictHostKeyChecking=accept-new 自动接受新 host key
        # 非 Windows 平台使用 BatchMode=yes 避免弹出 passphrase 交互提示
        # （Windows 上 BatchMode=yes 会导致 SSH 挂起，因此不使用）
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=5"]
        if sys.platform != 'win32':
            ssh_cmd += ["-o", "BatchMode=yes"]
        ssh_cmd += ["-T", "git@git.n.xiaomi.com"]
        result = subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        elapsed = time.time() - start_time
        # GitLab SSH 连接成功会返回 "Welcome to GitLab" 并退出码为 1
        # 如果需要 passphrase 或认证失败，会返回 "Permission denied" 并退出码为 255
        output = result.stdout + result.stderr
        
        if "Welcome to GitLab" in output:
            _log(f"   [SSH检测] 成功 (耗时 {elapsed:.1f}s)", verbose_only=True)
            return True
        # 检查是否有认证失败的标志
        if "Permission denied" in output or result.returncode == 255:
            _log(f"   [SSH检测] 认证失败: Permission denied (returncode={result.returncode})", verbose_only=True)
            return False
        # 其他情况（如网络问题），保守返回 True，让后续安装尝试
        _log(f"   [SSH检测] 其他响应 (returncode={result.returncode}, 耗时 {elapsed:.1f}s)", verbose_only=True)
        _log(f"   [SSH检测] stdout: {result.stdout[:100] if result.stdout else '(empty)'}", verbose_only=True)
        _log(f"   [SSH检测] stderr: {result.stderr[:100] if result.stderr else '(empty)'}", verbose_only=True)
        return True
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start_time
        _log(f"   [SSH检测] 超时 (耗时 {elapsed:.1f}s)，将回退到 HTTPS", verbose_only=True)
        return True
    except FileNotFoundError:
        _log("   [SSH检测] ssh 命令不存在", verbose_only=True)
        return False
    except Exception as e:
        _log(f"   [SSH检测] 异常: {type(e).__name__}: {e}", verbose_only=True)
        return True


def _detect_vaf_protocol() -> str:
    """检测 VAF 仓库使用的协议
    
    Returns:
        "ssh" 或 "https"
    """
    _log("   [协议检测] 正在检测 VAF 仓库协议...", verbose_only=True)
    vaf_root = _get_vaf_root()
    if not vaf_root:
        _log("   [协议检测] 无法获取 VAF 根目录，默认使用 HTTPS", verbose_only=True)
        return "https"  # 无法检测时默认使用 HTTPS
    
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=vaf_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        remote_url = result.stdout.strip()
        _log(f"   [协议检测] remote URL: {remote_url[:60]}...", verbose_only=True)
        
        # 判断协议类型
        if remote_url.startswith("git@") or "ssh://" in remote_url:
            _log("   [协议检测] 结果: SSH", verbose_only=True)
            return "ssh"
        else:
            _log("   [协议检测] 结果: HTTPS", verbose_only=True)
            return "https"
    except Exception as e:
        _log(f"   [协议检测] 异常: {type(e).__name__}: {e}，默认使用 HTTPS", verbose_only=True)
        return "https"  # 异常时默认使用 HTTPS


def _get_install_spec(protocol: Optional[str] = None) -> str:
    """获取 larkkit 安装源
    
    Args:
        protocol: 强制指定协议（"ssh" 或 "https"），不指定则自动检测
    
    Returns:
        larkkit 安装源 URL
    """
    global _detected_protocol
    
    if protocol:
        # 强制指定协议（用于重试）
        use_protocol = protocol
    else:
        # 自动检测
        if _detected_protocol is None:
            _detected_protocol = _detect_vaf_protocol()
            _log(f"   检测到 VAF 使用 {_detected_protocol.upper()} 协议", verbose_only=True)
        use_protocol = _detected_protocol
    
    if use_protocol == "ssh":
        return LARKKIT_INSTALL_SPEC_SSH
    else:
        return LARKKIT_INSTALL_SPEC_HTTPS


def set_verbose(verbose: bool):
    """设置是否输出详细日志"""
    global _verbose
    _verbose = verbose


def _log(message: str, verbose_only: bool = False):
    """输出日志（根据 verbose 标志控制）"""
    if verbose_only and not _verbose:
        return
    print(message)


# ============ uv 工具检测 ============

def is_uv_available() -> bool:
    """检测 uv 是否可用（公开接口）
    
    Returns:
        bool: uv 是否在 PATH 中可用
    """
    import shutil
    return shutil.which("uv") is not None


def get_uv_tool_dir() -> Optional[str]:
    """获取 uv tool 安装目录
    
    Returns:
        Optional[str]: 安装目录路径，失败返回 None
    """
    if not is_uv_available():
        return None
    try:
        result = subprocess.run(
            ["uv", "tool", "dir"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except:
        pass
    return None


# ============ 历史安装检测与清理 ============

def detect_pip_larkkit() -> Optional[str]:
    """检测 pip 安装的 larkkit 版本（跨平台）
    
    Returns:
        版本号字符串，未安装返回 None
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", "larkkit"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if line.startswith('Version:'):
                    return line.split(':')[1].strip()
    except:
        pass
    return None


def detect_larkkit_alias() -> Optional[str]:
    """检测 shell 配置中的 larkkit alias（Mac/Linux）
    
    Returns:
        alias 所在的配置文件路径，未找到返回 None
    """
    if sys.platform == 'win32':
        return None  # Windows 不使用 shell alias
    
    shell_configs = [
        Path.home() / ".zshrc",
        Path.home() / ".bashrc",
        Path.home() / ".bash_profile",
    ]
    
    for config in shell_configs:
        if config.exists():
            try:
                content = config.read_text(encoding='utf-8')
                if "alias larkkit=" in content:
                    return str(config)
            except:
                pass
    return None


def detect_windows_bat() -> Optional[Path]:
    """检测 Windows 批处理文件（install.bat 创建的）
    
    Returns:
        bat 文件路径，未找到返回 None
    """
    if sys.platform != 'win32':
        return None
    
    bat_file = Path.home() / "larkkit.bat"
    if bat_file.exists():
        return bat_file
    return None


def cleanup_pip_larkkit() -> bool:
    """清理 pip 安装的 larkkit（跨平台）
    
    Returns:
        是否执行了清理操作
    """
    pip_version = detect_pip_larkkit()
    if not pip_version:
        _log("   [cleanup_pip] 未检测到 pip 安装的 larkkit", verbose_only=True)
        return False
    
    _log(f"🧹 检测到 pip 安装的 larkkit v{pip_version}，正在清理...")
    
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", "-y", "larkkit"],
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=30
        )
        _log("   ✅ 已卸载 pip 版本")
        return True
    except Exception as e:
        _log(f"   ⚠️ 卸载失败（可忽略）: {e}")
        return False


def cleanup_larkkit_alias() -> bool:
    """清理 shell 配置中的 larkkit alias（Mac/Linux）
    
    Returns:
        是否执行了清理操作
    """
    if sys.platform == 'win32':
        _log("   [cleanup_alias] Windows 不使用 shell alias，跳过", verbose_only=True)
        return False  # Windows 不使用 shell alias
    
    alias_file = detect_larkkit_alias()
    if not alias_file:
        _log("   [cleanup_alias] 未检测到 larkkit alias", verbose_only=True)
        return False
    
    _log(f"🧹 检测到 larkkit alias 在 {alias_file}，正在清理...")
    
    try:
        config_path = Path(alias_file)
        content = config_path.read_text(encoding='utf-8')
        
        # 删除 larkkit 相关行
        lines = content.split('\n')
        new_lines = [
            line for line in lines 
            if 'alias larkkit=' not in line and '# larkkit' not in line
        ]
        
        config_path.write_text('\n'.join(new_lines), encoding='utf-8')
        _log("   ✅ 已清理 alias")
        return True
    except Exception as e:
        _log(f"   ⚠️ 清理失败（可忽略）: {e}")
        return False


def cleanup_windows_bat() -> bool:
    """清理 Windows 批处理文件（install.bat 创建的）
    
    Returns:
        是否执行了清理操作
    """
    if sys.platform != 'win32':
        _log("   [cleanup_bat] 非 Windows 系统，跳过", verbose_only=True)
        return False
    
    bat_file = detect_windows_bat()
    if not bat_file:
        _log("   [cleanup_bat] 未检测到 larkkit.bat", verbose_only=True)
        return False
    
    _log(f"🧹 检测到 Windows 批处理文件 {bat_file}，正在清理...")
    
    try:
        bat_file.unlink()
        _log("   ✅ 已删除 larkkit.bat")
        return True
    except Exception as e:
        _log(f"   ⚠️ 删除失败（可忽略）: {e}")
        return False


def cleanup_legacy_larkkit() -> dict:
    """清理所有历史安装方式的 larkkit（统一入口）
    
    设计原则：
    - 每一步失败不阻塞下一步
    - 整体清理失败不阻塞 VAF 后续流程
    - 核心目标是保证 larkkit 命令不会执行到老版本
    
    Returns:
        清理结果字典 {"pip": bool, "alias": bool, "bat": bool}
    """
    results = {
        "pip": cleanup_pip_larkkit(),
        "alias": cleanup_larkkit_alias(),
        "bat": cleanup_windows_bat(),
    }
    
    cleaned = [k for k, v in results.items() if v]
    if cleaned:
        _log(f"🧹 已清理历史安装: {', '.join(cleaned)}")
        _log("   💡 这是为了统一使用 uv 管理 larkkit，避免版本冲突")
    
    return results


# ============ larkkit 检测与安装 ============

def _check_larkkit_installed_uv() -> bool:
    """检测 larkkit 是否已通过 uv 安装"""
    _log("   [检测] 正在检查 larkkit 是否已通过 uv 安装...", verbose_only=True)
    try:
        result = subprocess.run(
            ["uv", "tool", "list"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        installed = "larkkit" in result.stdout
        if installed:
            # 提取版本信息
            import re
            match = re.search(r'larkkit\s+v?([\d.]+)', result.stdout)
            version = match.group(1) if match else "unknown"
            _log(f"   [检测] larkkit 已安装 (v{version})", verbose_only=True)
        else:
            _log("   [检测] larkkit 未安装", verbose_only=True)
            _log(f"   [检测] uv tool list 输出: {result.stdout[:200] if result.stdout else '(空)'}", verbose_only=True)
        return installed
    except subprocess.TimeoutExpired:
        _log("   [检测] uv tool list 超时", verbose_only=True)
        return False
    except Exception as e:
        _log(f"   [检测] 检测异常: {type(e).__name__}: {e}", verbose_only=True)
        return False


def _try_install_with_protocol(protocol: str) -> Tuple[bool, str]:
    """尝试使用指定协议安装 larkkit
    
    Args:
        protocol: "ssh" 或 "https"
    
    Returns:
        (success, error_msg)
    """
    import time
    
    install_spec = _get_install_spec(protocol)
    start_time = time.time()
    
    _log(f"   [安装] 使用 {protocol.upper()} 协议安装...", verbose_only=True)
    _log(f"   [安装] 命令: uv tool install --force \"{install_spec}\"", verbose_only=True)
    
    try:
        # 不使用 capture_output，让 SSH 能与终端交互输入 passphrase
        # 输出直接流向终端，不需要 Python 处理编码
        result = subprocess.run(
            ["uv", "tool", "install", "--force", install_spec],
            timeout=UV_INSTALL_TIMEOUT
        )
        
        elapsed = time.time() - start_time
        
        if result.returncode == 0:
            version = _get_larkkit_version() or "unknown"
            _log(f"✅ larkkit 安装完成 (v{version})")
            _log(f"   [安装] 耗时: {elapsed:.1f}s", verbose_only=True)
            return True, ""
        else:
            _log(f"   [安装] 失败 (returncode={result.returncode}, 耗时 {elapsed:.1f}s)", verbose_only=True)
            return False, "安装失败（详见上方输出）"
            
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start_time
        _log(f"   [安装] 超时 (耗时 {elapsed:.1f}s)", verbose_only=True)
        return False, "安装超时"
    except Exception as e:
        elapsed = time.time() - start_time
        _log(f"   [安装] 异常: {type(e).__name__}: {e} (耗时 {elapsed:.1f}s)", verbose_only=True)
        return False, str(e)


def _install_larkkit_with_uv() -> bool:
    """使用 uv tool install 安装 larkkit（带协议回退机制）"""
    _log("📦 正在安装 larkkit...")
    _log(f"   来源: {LARKKIT_REPO_SHORT}", verbose_only=True)
    
    # Step 1: 检测 VAF 使用的协议
    primary_protocol = _detect_vaf_protocol()
    
    _log(f"   协议: {primary_protocol.upper()}（基于 VAF 仓库检测）", verbose_only=True)
    
    # Step 2: 如果使用 SSH，先检测 SSH Agent 是否就绪
    if primary_protocol == "ssh":
        if _check_ssh_agent_ready():
            _log("   ✅ SSH Agent 已就绪", verbose_only=True)
        else:
            # SSH Agent 未就绪，直接提示用户配置，不尝试安装
            _log("   [_install_larkkit_with_uv] SSH Agent 未就绪，返回 False", verbose_only=True)
            _log("")
            _log("⚠️  检测到 SSH 需要交互输入 passphrase")
            _log("   uv 工具无法处理 SSH 交互认证")
            _print_ssh_agent_setup_help()
            return False
    
    # Step 3: 使用主协议尝试安装
    success, error_msg = _try_install_with_protocol(primary_protocol)
    if success:
        return True
    
    # Step 4: SSH 失败时不回退到 HTTPS（避免弹出 Git Credential Manager）
    # 因为 HTTPS 需要 Personal Access Token，用户体验更差
    if primary_protocol == "ssh":
        _log("   [_install_larkkit_with_uv] SSH 安装失败，不回退 HTTPS，返回 False", verbose_only=True)
        _log("❌ larkkit 安装失败")
        _log(f"   错误: {error_msg}", verbose_only=True)
        _print_larkkit_install_help()
        return False
    
    # Step 5: HTTPS 失败时尝试 SSH
    _log(f"   HTTPS 安装失败，尝试 SSH...", verbose_only=True)
    if _check_ssh_agent_ready():
        success, error_msg = _try_install_with_protocol("ssh")
        if success:
            return True
    
    # Step 6: 两种协议都失败
    _log("   [_install_larkkit_with_uv] 两种协议均失败，返回 False", verbose_only=True)
    _log("❌ larkkit 安装失败")
    _log(f"   错误: {error_msg}", verbose_only=True)
    _print_larkkit_install_help()
    return False


def _print_ssh_agent_setup_help():
    """打印 SSH Agent 配置帮助"""
    import sys
    install_spec = _get_install_spec("ssh")
    
    _log("")
    _log("=" * 60)
    _log("💡 请先配置 SSH Agent，让密钥免交互使用")
    _log("=" * 60)
    
    if sys.platform == 'win32':
        _log("")
        _log("【Windows 配置步骤】（管理员 PowerShell）：")
        _log("")
        _log("   # 1. 启用 SSH Agent 服务")
        _log("   Set-Service ssh-agent -StartupType Automatic")
        _log("   Start-Service ssh-agent")
        _log("")
        _log("   # 2. 添加 SSH 密钥（输入一次 passphrase）")
        _log("   ssh-add $env:USERPROFILE\\.ssh\\id_rsa")
        _log("")
        _log("   # 3. 验证密钥已添加")
        _log("   ssh-add -l")
    else:
        _log("")
        _log("【Mac/Linux 配置步骤】：")
        _log("")
        _log("   # 1. 启动 ssh-agent")
        _log("   eval $(ssh-agent -s)")
        _log("")
        _log("   # 2. 添加 SSH 密钥（输入一次 passphrase）")
        _log("   ssh-add ~/.ssh/id_rsa")
        _log("")
        _log("   # 3. 验证密钥已添加")
        _log("   ssh-add -l")
    
    _log("")
    _log("配置完成后，重新执行：vaf update 或 vaf deps upgrade")
    _log("")
    _log("📖 详细指引: https://git.n.xiaomi.com/mit-ai-galaxy/vibe-agentic-flow/-/blob/main/docs/vaf_install_guide.md")
    _log("=" * 60)


def _print_larkkit_install_help():
    """打印 larkkit 手动安装帮助"""
    import sys
    install_spec = _get_install_spec()
    _log("")
    _log("=" * 60)
    _log("❌ larkkit 安装失败")
    _log("=" * 60)
    _log("")
    
    # Windows 用户特殊提示
    if sys.platform == 'win32':
        _log("💡 Windows 用户请先配置 SSH Agent（管理员 PowerShell）：")
        _log("")
        _log("   # 1. 启用 SSH Agent 服务")
        _log("   Set-Service ssh-agent -StartupType Automatic")
        _log("   Start-Service ssh-agent")
        _log("")
        _log("   # 2. 添加 SSH 密钥（输入一次 passphrase）")
        _log("   ssh-add $env:USERPROFILE\\.ssh\\id_rsa")
        _log("")
        _log("   # 3. 验证密钥已添加")
        _log("   ssh-add -l")
        _log("")
        _log("配置完成后，重新执行：")
        _log(f'   uv tool install --force "{install_spec}"')
    else:
        _log("💡 请手动执行以下命令：")
        _log(f'   uv tool install --force "{install_spec}"')
        _log("")
        _log("如果 SSH 需要 passphrase，请先配置 ssh-agent：")
        _log("   eval $(ssh-agent -s)")
        _log("   ssh-add ~/.ssh/id_rsa")
    
    _log("")
    _log("📖 详细指引: https://git.n.xiaomi.com/mit-ai-galaxy/vibe-agentic-flow/-/blob/main/docs/vaf_install_guide.md")
    _log("=" * 60)


def _get_larkkit_cmd() -> list:
    """获取 larkkit 命令前缀
    
    优先使用 uv tool install 安装到 PATH 中的 larkkit 可执行文件，
    避免 uvx 尝试从 PyPI 解析导致 Git 源包找不到的问题。
    """
    import shutil
    
    # 优先检测 larkkit 是否在 PATH 中（uv tool install 后会添加到 PATH）
    if shutil.which("larkkit"):
        cmd = ["larkkit"]
        _log(f"   [_get_larkkit_cmd] 使用已安装的 larkkit: {shutil.which('larkkit')}", verbose_only=True)
    else:
        # 降级到 uvx
        cmd = ["uvx", "larkkit"]
        _log(f"   [_get_larkkit_cmd] larkkit 不在 PATH，降级使用 uvx", verbose_only=True)
    
    return cmd


def _get_larkkit_version() -> str:
    """获取 larkkit 版本号"""
    try:
        result = subprocess.run(
            _get_larkkit_cmd() + ["version"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        # 从输出中提取版本号
        import re
        match = re.search(r'(\d+\.\d+\.\d+)', result.stdout)
        if match:
            return match.group(1)
    except:
        pass
    
    # 备选方案：从 uv tool list 获取版本
    try:
        result = subprocess.run(
            ["uv", "tool", "list"],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10
        )
        # 格式: "larkkit v2.3.0" 或 "larkkit 2.3.0"
        import re
        match = re.search(r'larkkit\s+v?(\d+\.\d+\.\d+)', result.stdout)
        if match:
            return match.group(1)
    except:
        pass
    
    return ""


# ============ 公共接口 ============

def ensure_larkkit() -> bool:
    """
    确保 larkkit 可用（统一入口）
    
    职责：
    1. 清理历史安装方式（pip/alias/bat）- 失败不阻塞
    2. 检测/安装 uv 版本的 larkkit
    
    注意：环境前置检测（Python/uv/SSH）和飞书配置检测由 vaf.py 负责
    
    Returns:
        bool: larkkit 是否可用
    """
    _log("\n🔧 [ensure_larkkit] 开始检查 larkkit 可用性...", verbose_only=True)
    
    # Step 1-3: 清理所有历史安装方式（pip/alias/bat）
    # 设计原则：失败不阻塞，核心目标是保证 larkkit 命令不会执行到老版本
    _log("   [Step 1] 清理历史安装...", verbose_only=True)
    cleanup_legacy_larkkit()
    
    # Step 2: 检测 larkkit 是否已通过 uv 安装
    _log("   [Step 2] 检测 uv 安装状态...", verbose_only=True)
    if _check_larkkit_installed_uv():
        _log("✅ larkkit 已就绪", verbose_only=True)
        return True
    
    # Step 3: 安装 larkkit（如果未安装）
    _log("   [Step 3] 开始安装 larkkit...", verbose_only=True)
    result = _install_larkkit_with_uv()
    _log(f"   [ensure_larkkit] 结果: {'成功' if result else '失败'}", verbose_only=True)
    return result


def upgrade_larkkit() -> bool:
    """
    升级 larkkit 到最新版本（带协议回退机制）
    
    职责：
    1. 清理历史安装方式（pip/alias/bat）- 失败不阻塞
    2. 升级 uv 版本的 larkkit
    
    注意：环境前置检测（Python/uv）由 vaf.py 负责
    
    Returns:
        bool: 是否升级成功
    """
    _log("\n🔧 [upgrade_larkkit] 开始升级 larkkit...", verbose_only=True)
    
    # Step 1: 清理所有历史安装方式（pip/alias/bat）
    _log("   [Step 1] 清理历史安装...", verbose_only=True)
    cleanup_legacy_larkkit()
    
    # Step 2: 检测 larkkit 是否已安装
    _log("   [Step 2] 检测当前安装状态...", verbose_only=True)
    if not _check_larkkit_installed_uv():
        _log("📦 larkkit 未安装，正在安装...", verbose_only=True)
        if not _install_larkkit_with_uv():
            _log("   [upgrade_larkkit] 首次安装失败", verbose_only=True)
            return False
        _log("✅ 首次安装完成，无需升级")
        return True
    
    _log("🔄 正在升级 larkkit...")
    
    # Step 3: 检测 VAF 使用的协议
    _log("   [Step 3] 检测 Git 协议...", verbose_only=True)
    primary_protocol = _detect_vaf_protocol()
    fallback_protocol = "https" if primary_protocol == "ssh" else "ssh"
    _log(f"   协议: {primary_protocol.upper()}（基于 VAF 仓库检测）", verbose_only=True)
    _log(f"   回退协议: {fallback_protocol.upper()}", verbose_only=True)
    
    # Step 4: 使用主协议尝试升级
    _log(f"   [Step 4] 使用 {primary_protocol.upper()} 协议升级...", verbose_only=True)
    success, error_msg = _try_install_with_protocol(primary_protocol)
    if success:
        _log("   [upgrade_larkkit] 升级成功", verbose_only=True)
        return True
    
    # Step 5: 主协议失败，尝试回退协议
    _log(f"   [Step 5] {primary_protocol.upper()} 失败，尝试 {fallback_protocol.upper()}...", verbose_only=True)
    _log(f"   主协议错误: {error_msg}", verbose_only=True)
    success, error_msg = _try_install_with_protocol(fallback_protocol)
    if success:
        _log("   [upgrade_larkkit] 回退协议升级成功", verbose_only=True)
        return True
    
    # Step 6: 两种协议都失败
    _log("❌ larkkit 升级失败")
    _log(f"   回退协议错误: {error_msg}", verbose_only=True)
    _log("   [upgrade_larkkit] 两种协议均失败", verbose_only=True)
    return False


def run_larkkit_command(args: list) -> int:
    """
    执行 larkkit 命令
    
    Args:
        args: larkkit 命令参数，如 ["download", "URL", "-o", "./output"]
        
    Returns:
        命令退出码
    """
    _log(f"   [run_larkkit_command] 参数: {args[:3]}...", verbose_only=True)
    if not ensure_larkkit():
        _log("   [run_larkkit_command] larkkit 不可用", verbose_only=True)
        return 1
    
    cmd = _get_larkkit_cmd() + args
    _log(f"   [run_larkkit_command] 执行: {' '.join(cmd[:5])}...", verbose_only=True)
    result = subprocess.run(cmd)
    _log(f"   [run_larkkit_command] 退出码: {result.returncode}", verbose_only=True)
    return result.returncode


def run_larkkit_download(
    url: str,
    output_path: str,
    *,
    use_user_token: bool = True,
    download_images: bool = True,
    include_comments: bool = True,
    ignore_sections: list = None,
    ignore_callouts: list = None,
    ignore_section_callouts: list = None,
    filter_keywords: list = None,
    timeout: int = 300,
) -> tuple:
    """
    调用 larkkit CLI 下载飞书文档
    
    Args:
        url: 飞书文档 URL
        output_path: 输出目录
        use_user_token: 使用 user token（默认 True）
        download_images: 下载图片（默认 True）
        include_comments: 包含评论（默认 True）
        ignore_sections: 忽略的章节关键字列表
        ignore_callouts: 忽略的引用块关键字列表
        ignore_section_callouts: 忽略的章节引用块关键字列表
        filter_keywords: 过滤的关键字列表
        timeout: 超时时间（秒，默认 300）
        
    Returns:
        (success: bool, result_path: str, error_msg: str)
        - success: 是否成功
        - result_path: 下载的 .md 文件路径（失败时为空）
        - error_msg: 错误信息（成功时为空）
    """
    _log(f"   [run_larkkit_download] URL: {url[:50]}...", verbose_only=True)
    if not ensure_larkkit():
        _log("   [run_larkkit_download] larkkit 不可用，返回 False", verbose_only=True)
        return False, "", "larkkit 未安装"
    
    cmd = _get_larkkit_cmd() + ["download", url, "-o", output_path]
    _log(f"   [run_larkkit_download] 执行命令: {' '.join(cmd[:6])}...", verbose_only=True)
    
    if use_user_token:
        cmd.append("--use-user-token")
    if not download_images:
        cmd.append("--no-images")
    if not include_comments:
        cmd.append("--no-comments")
    if ignore_sections:
        cmd.extend(["--ignore-sections"] + list(ignore_sections))
    if ignore_callouts:
        cmd.extend(["--ignore-callouts"] + list(ignore_callouts))
    if ignore_section_callouts:
        cmd.extend(["--ignore-section-callouts"] + list(ignore_section_callouts))
    if filter_keywords:
        cmd.extend(["--filter-keywords"] + list(filter_keywords))
    
    try:
        result = subprocess.run(
            cmd,
            input="3\n",  # 自动选择选项3（保留覆盖），与原 API 行为一致
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout
        )
        
        _log(f"   [run_larkkit_download] 返回码: {result.returncode}", verbose_only=True)
        if result.returncode == 0:
            output_dir = Path(output_path)
            md_files = list(output_dir.glob("*.md"))
            if md_files:
                _log(f"   [run_larkkit_download] 成功，文件: {md_files[0].name}", verbose_only=True)
                return True, str(md_files[0]), ""
            _log(f"   [run_larkkit_download] 成功，目录: {output_path}", verbose_only=True)
            return True, output_path, ""
        else:
            error_msg = result.stderr.strip() if result.stderr else "未知错误"
            _log(f"   [run_larkkit_download] 失败: {error_msg[:200]}", verbose_only=True)
            _log(f"   [run_larkkit_download] stdout: {result.stdout[:200] if result.stdout else '(空)'}", verbose_only=True)
            return False, "", error_msg
            
    except subprocess.TimeoutExpired:
        _log("   [run_larkkit_download] 超时", verbose_only=True)
        return False, "", "下载超时"
    except Exception as e:
        _log(f"   [run_larkkit_download] 异常: {type(e).__name__}: {e}", verbose_only=True)
        return False, "", str(e)


def get_larkkit_version() -> str:
    """
    获取 larkkit 版本号（供外部调用）
    
    Returns:
        版本号字符串，如 "2.3.0"，未安装时返回 "not_installed"
    """
    version = _get_larkkit_version()
    return version if version else "not_installed"


# ============ Bitable CLI 封装 ============

def run_larkkit_bitable_resolve_token(
    url: str,
    timeout: int = 30,
) -> tuple:
    """
    调用 larkkit CLI 解析 Wiki URL 获取 app_token
    
    Args:
        url: Wiki 内嵌多维表格 URL
        timeout: 超时时间（秒，默认 30）
        
    Returns:
        (success: bool, app_token: str, table_id: str, error_msg: str)
    """
    _log(f"   [bitable_resolve_token] URL: {url[:50]}...", verbose_only=True)
    if not ensure_larkkit():
        _log("   [bitable_resolve_token] larkkit 不可用", verbose_only=True)
        return False, "", "", "larkkit 未安装"
    
    cmd = _get_larkkit_cmd() + ["bitable", "resolve-token", url, "--output", "json"]
    _log(f"   [bitable_resolve_token] 执行命令: {' '.join(cmd[:6])}...", verbose_only=True)
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout
        )
        
        _log(f"   [bitable_resolve_token] 返回码: {result.returncode}", verbose_only=True)
        if result.returncode == 0:
            import json
            # CLI 输出可能包含调试信息，只解析最后一行 JSON
            lines = result.stdout.strip().split('\n')
            for line in reversed(lines):
                line = line.strip()
                if line.startswith('{') and line.endswith('}'):
                    data = json.loads(line)
                    _log(f"   [bitable_resolve_token] 成功解析 app_token", verbose_only=True)
                    return True, data.get("app_token", ""), data.get("table_id", ""), ""
            _log(f"   [bitable_resolve_token] 未找到 JSON，stdout: {result.stdout[:200]}", verbose_only=True)
            return False, "", "", "未找到 JSON 输出"
        else:
            error_msg = result.stderr.strip() if result.stderr else "未知错误"
            _log(f"   [bitable_resolve_token] 失败: {error_msg[:200]}", verbose_only=True)
            _log(f"   [bitable_resolve_token] stdout: {result.stdout[:200] if result.stdout else '(空)'}", verbose_only=True)
            return False, "", "", error_msg
            
    except subprocess.TimeoutExpired:
        _log("   [bitable_resolve_token] 超时", verbose_only=True)
        return False, "", "", "用户令牌无效或已过期，请执行 vaf lark auth 重新授权"
    except Exception as e:
        _log(f"   [bitable_resolve_token] 异常: {type(e).__name__}: {e}", verbose_only=True)
        return False, "", "", str(e)


def run_larkkit_bitable_list(
    url: str,
    table_id: str,
    *,
    filter_expr: str = None,
    page_size: int = 100,
    timeout: int = 60,
) -> tuple:
    """
    调用 larkkit CLI 查询多维表格记录
    
    Args:
        url: 多维表格 URL 或 app_token
        table_id: 数据表 ID
        filter_expr: 筛选条件
        page_size: 每页记录数
        timeout: 超时时间（秒，默认 60）
        
    Returns:
        (success: bool, records: list, error_msg: str)
    """
    _log(f"   [bitable_list] table_id: {table_id}", verbose_only=True)
    if not ensure_larkkit():
        _log("   [bitable_list] larkkit 不可用", verbose_only=True)
        return False, [], "larkkit 未安装"
    
    cmd = _get_larkkit_cmd() + ["bitable", "list", url, "--table", table_id, "--output", "json"]
    _log(f"   [bitable_list] 执行命令: {' '.join(cmd[:7])}...", verbose_only=True)
    
    if filter_expr:
        cmd.extend(["--filter", filter_expr])
    if page_size:
        cmd.extend(["--limit", str(page_size)])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout
        )
        
        _log(f"   [bitable_list] 返回码: {result.returncode}", verbose_only=True)
        if result.returncode == 0:
            import json
            # CLI 输出可能包含日志信息，需要提取 JSON 部分
            stdout = result.stdout.strip()
            if not stdout:
                _log("   [bitable_list] 空输出，无记录", verbose_only=True)
                return True, [], ""  # 空输出表示无记录
            
            # 查找 JSON 数组的起始位置
            json_start = stdout.find('[')
            if json_start == -1:
                _log("   [bitable_list] 无 JSON 数组，无记录", verbose_only=True)
                return True, [], ""  # 无 JSON 数组，表示无记录
            
            json_str = stdout[json_start:]
            try:
                records = json.loads(json_str)
                _log(f"   [bitable_list] 成功获取 {len(records) if isinstance(records, list) else 0} 条记录", verbose_only=True)
                return True, records if isinstance(records, list) else [], ""
            except json.JSONDecodeError as e:
                _log(f"   [bitable_list] JSON 解析失败: {e}", verbose_only=True)
                return False, [], f"JSON 解析失败: {e}"
        else:
            error_msg = result.stderr.strip() if result.stderr else "未知错误"
            _log(f"   [bitable_list] 失败: {error_msg[:200]}", verbose_only=True)
            _log(f"   [bitable_list] stdout: {result.stdout[:200] if result.stdout else '(空)'}", verbose_only=True)
            return False, [], error_msg
            
    except subprocess.TimeoutExpired:
        _log("   [bitable_list] 超时", verbose_only=True)
        return False, [], "查询超时"
    except Exception as e:
        _log(f"   [bitable_list] 异常: {type(e).__name__}: {e}", verbose_only=True)
        return False, [], str(e)


def run_larkkit_bitable_create(
    url: str,
    table_id: str,
    fields: dict,
    timeout: int = 30,
) -> tuple:
    """
    调用 larkkit CLI 新增多维表格记录
    
    Args:
        url: 多维表格 URL 或 app_token
        table_id: 数据表 ID
        fields: 字段数据
        timeout: 超时时间（秒，默认 30）
        
    Returns:
        (success: bool, record_id: str, error_msg: str)
    """
    if not ensure_larkkit():
        return False, "", "larkkit 未安装"
    
    import json as json_module
    cmd = _get_larkkit_cmd() + [
        "bitable", "create", url,
        "--table", table_id,
        "--data", json_module.dumps(fields, ensure_ascii=False)
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout
        )
        
        if result.returncode == 0:
            # 从输出中提取 record_id
            import re
            match = re.search(r'记录 ID[：:]\s*(\S+)', result.stdout)
            record_id = match.group(1) if match else ""
            return True, record_id, ""
        else:
            error_msg = result.stderr.strip() if result.stderr else "未知错误"
            return False, "", error_msg
            
    except subprocess.TimeoutExpired:
        return False, "", "创建超时"
    except Exception as e:
        return False, "", str(e)


def run_larkkit_bitable_update(
    url: str,
    table_id: str,
    record_id: str,
    fields: dict,
    timeout: int = 30,
) -> tuple:
    """
    调用 larkkit CLI 更新多维表格记录
    
    Args:
        url: 多维表格 URL 或 app_token
        table_id: 数据表 ID
        record_id: 记录 ID
        fields: 更新的字段数据
        timeout: 超时时间（秒，默认 30）
        
    Returns:
        (success: bool, error_msg: str)
    """
    if not ensure_larkkit():
        return False, "larkkit 未安装"
    
    import json as json_module
    cmd = _get_larkkit_cmd() + [
        "bitable", "update", url,
        "--table", table_id,
        "--record", record_id,
        "--data", json_module.dumps(fields, ensure_ascii=False)
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout
        )
        
        if result.returncode == 0:
            return True, ""
        else:
            error_msg = result.stderr.strip() if result.stderr else "未知错误"
            return False, error_msg
            
    except subprocess.TimeoutExpired:
        return False, "更新超时"
    except Exception as e:
        return False, str(e)
