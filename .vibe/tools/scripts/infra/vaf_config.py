# -*- coding: utf-8 -*-
from __future__ import annotations
"""VAF 统一配置管理模块

从配置文件读取敏感配置，支持环境变量覆盖。

配置文件格式 (INI):
    [feishu]
    app_id = your_app_id
    app_secret = your_app_secret

    [dashscope]
    api_key = sk-xxx

    [mify]
    api_key = app-xxx
    vlm_api_key = app-xxx
    bot_api_key = app-xxx
    upload_url = https://service.mify.mioffice.cn/api/v1/files/upload
    workflow_url = https://service.mify.mioffice.cn/api/v1/workflows/run

    [vlm]
    rollout_mode = mify

配置文件路径优先级（从高到低）:
    1. ~/.vaf/config (推荐)
    2. ~/.config/vaf (XDG 兼容)

配置项优先级（从高到低）:
    1. 环境变量 (FEISHU_APP_ID, DASHSCOPE_API_KEY 等)
    2. 配置文件
    3. 命令行参数默认值（不再硬编码密钥）
"""

import sys

# 跨平台 Unicode 输出支持 (Windows GBK 兼容)
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import os
from configparser import ConfigParser
from pathlib import Path
from typing import Optional

# 配置文件路径（优先级从高到低）
VAF_CONFIG_PATH = Path.home() / ".vaf" / "config"       # 1. 优先
XDG_CONFIG_PATH = Path.home() / ".config" / "vaf"       # 2. XDG 兼容

# 兼容旧代码的别名
CONFIG_PATH = VAF_CONFIG_PATH


def get_effective_config_path() -> tuple[Path | None, str]:
    """获取实际生效的配置路径
    
    优先级: ~/.vaf/config > ~/.config/vaf
    
    Returns:
        (配置路径, 来源标识) 或 (None, "none")
    """
    if VAF_CONFIG_PATH.exists():
        return VAF_CONFIG_PATH, "~/.vaf/config"
    if XDG_CONFIG_PATH.exists():
        return XDG_CONFIG_PATH, "~/.config/vaf"
    return None, "none"


def _load_config() -> ConfigParser:
    """加载配置文件（按优先级）"""
    config = ConfigParser()
    path, _ = get_effective_config_path()
    if path:
        config.read(path, encoding="utf-8")
    return config


def get_feishu_app_id(explicit: Optional[str] = None) -> Optional[str]:
    """获取飞书 App ID
    
    优先级: explicit > 环境变量 > 配置文件
    """
    if explicit:
        return explicit
    
    env_val = os.environ.get("FEISHU_APP_ID")
    if env_val:
        return env_val
    
    config = _load_config()
    return config.get("feishu", "app_id", fallback=None)


def get_feishu_app_secret(explicit: Optional[str] = None) -> Optional[str]:
    """获取飞书 App Secret
    
    优先级: explicit > 环境变量 > 配置文件
    """
    if explicit:
        return explicit
    
    env_val = os.environ.get("FEISHU_APP_SECRET")
    if env_val:
        return env_val
    
    config = _load_config()
    return config.get("feishu", "app_secret", fallback=None)


def get_dashscope_api_key(explicit: Optional[str] = None) -> Optional[str]:
    """获取 DashScope API Key
    
    优先级: explicit > 环境变量 > 配置文件
    """
    if explicit:
        return explicit
    
    env_val = os.environ.get("DASHSCOPE_API_KEY")
    if env_val:
        return env_val
    
    config = _load_config()
    return config.get("dashscope", "api_key", fallback=None)


def get_mify_api_key(explicit: Optional[str] = None) -> Optional[str]:
    """获取 MiFy API Key (通用的或 VLM 专用的)
    
    优先级: explicit > 环境变量 > 配置文件(vlm_api_key) > 配置文件(api_key)
    """
    if explicit:
        return explicit
    
    env_val = os.environ.get("MIFY_API_KEY")
    if env_val:
        return env_val
    
    config = _load_config()
    if not config.has_section("mify"):
        return None
        
    # 优先返回 VLM 专用的，如果没有则回退到通用的
    return config.get("mify", "vlm_api_key", fallback=config.get("mify", "api_key", fallback=None))


def get_mify_bot_api_key(explicit: Optional[str] = None) -> Optional[str]:
    """获取 MiFy Bot 专用 API Key"""
    if explicit:
        return explicit
    
    config = _load_config()
    if not config.has_section("mify"):
        return None
        
    return config.get("mify", "bot_api_key", fallback=config.get("mify", "api_key", fallback=None))


def get_mify_upload_url(explicit: Optional[str] = None) -> str:
    """获取 MiFy 上传 URL"""
    if explicit:
        return explicit
    
    env_val = os.environ.get("MIFY_UPLOAD_URL")
    if env_val:
        return env_val
    
    config = _load_config()
    return config.get(
        "mify", "upload_url",
        fallback="https://service.mify.mioffice.cn/api/v1/files/upload"
    )


def get_mify_workflow_url(explicit: Optional[str] = None) -> str:
    """获取 MiFy Workflow URL"""
    if explicit:
        return explicit
    
    env_val = os.environ.get("MIFY_WORKFLOW_URL")
    if env_val:
        return env_val
    
    config = _load_config()
    return config.get(
        "mify", "workflow_url",
        fallback="https://service.mify.mioffice.cn/api/v1/workflows/run"
    )


def get_vlm_rollout_mode(explicit: Optional[str] = None) -> str:
    """获取 VLM 灰度模式
    
    可选值: classic, mify, dual
    """
    if explicit:
        return explicit
    
    env_val = os.environ.get("VLM_ROLLOUT_MODE")
    if env_val:
        return env_val
    
    config = _load_config()
    return config.get("vlm", "rollout_mode", fallback="mify")


def get_ignore_sections(explicit: list[str] | None = None) -> list[str]:
    """获取忽略的章节关键字列表
    
    优先级: explicit > 环境变量 > 配置文件
    """
    if explicit:
        return explicit
    
    env_val = os.environ.get("VAF_IGNORE_SECTIONS")
    if env_val:
        return [s.strip() for s in env_val.split(",") if s.strip()]
    
    config = _load_config()
    val = config.get("download", "ignore_sections", fallback="")
    return [s.strip() for s in val.split(",") if s.strip()]


def get_ignore_callouts(explicit: list[str] | None = None) -> list[str]:
    """获取忽略的引用块关键字列表
    
    优先级: explicit > 环境变量 > 配置文件
    """
    if explicit:
        return explicit
    
    env_val = os.environ.get("VAF_IGNORE_CALLOUTS")
    if env_val:
        return [s.strip() for s in env_val.split(",") if s.strip()]
    
    config = _load_config()
    val = config.get("download", "ignore_callouts", fallback="")
    return [s.strip() for s in val.split(",") if s.strip()]


def get_ignore_section_callouts(explicit: list[str] | None = None) -> list[str]:
    """获取忽略引用块的章节列表
    
    优先级: explicit > 环境变量 > 配置文件
    """
    if explicit:
        return explicit
    
    env_val = os.environ.get("VAF_IGNORE_SECTION_CALLOUTS")
    if env_val:
        return [s.strip() for s in env_val.split(",") if s.strip()]
    
    config = _load_config()
    val = config.get("download", "ignore_section_callouts", fallback="")
    return [s.strip() for s in val.split(",") if s.strip()]


def get_filter_keywords(explicit: list[str] | None = None) -> list[str]:
    """获取过滤关键字列表（过滤包含关键字的句子）
    
    优先级: explicit > 环境变量 > 配置文件
    """
    if explicit:
        return explicit
    
    env_val = os.environ.get("VAF_FILTER_KEYWORDS")
    if env_val:
        return [s.strip() for s in env_val.split(",") if s.strip()]
    
    config = _load_config()
    val = config.get("download", "filter_keywords", fallback="")
    return [s.strip() for s in val.split(",") if s.strip()]


def require_feishu_credentials() -> tuple[str, str]:
    """获取飞书凭证，未配置时抛出异常并提示"""
    app_id = get_feishu_app_id()
    app_secret = get_feishu_app_secret()
    
    if not app_id or not app_secret:
        _print_config_hint("feishu")
        raise ValueError(
            "飞书凭证未配置。请在 ~/.vaf/config 中配置 [feishu] 部分，"
            "或设置环境变量 FEISHU_APP_ID 和 FEISHU_APP_SECRET"
        )
    
    return app_id, app_secret


def require_dashscope_api_key() -> str:
    """获取 DashScope API Key，未配置时抛出异常并提示"""
    api_key = get_dashscope_api_key()
    
    if not api_key:
        _print_config_hint("dashscope")
        raise ValueError(
            "DashScope API Key 未配置。请在 ~/.vaf/config 中配置 [dashscope] 部分，"
            "或设置环境变量 DASHSCOPE_API_KEY"
        )
    
    return api_key


def require_mify_api_key() -> str:
    """获取 MiFy API Key，未配置时抛出异常并提示"""
    api_key = get_mify_api_key()
    
    if not api_key:
        _print_config_hint("mify")
        raise ValueError(
            "MiFy API Key 未配置。请在 ~/.vaf/config 中配置 [mify] 部分，"
            "或设置环境变量 MIFY_API_KEY"
        )
    
    return api_key


def _print_config_hint(section: str) -> None:
    """打印配置提示"""
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║  ⚠️  VAF 配置缺失: [{section}]                                    
╠══════════════════════════════════════════════════════════════════╣
║  请创建配置文件: ~/.vaf/config                             
║  （或 ~/.config/vaf，优先级较低）                                  
║                                                                  
║  快速创建: vaf config init                                        
║                                                                  
║  示例内容:                                                        
║  ┌────────────────────────────────────────────────────────────┐  
║  │ [feishu]                                                   │  
║  │ app_id = cli_xxxxxxxx                                      │  
║  │ app_secret = xxxxxxxxxxxxxxxxxxxxxxxx                      │  
║  │                                                            │  
║  │ [dashscope]                                                │  
║  │ api_key = sk-xxxxxxxxxxxxxxxx                              │  
║  │                                                            │  
║  │ [mify]                                                     │  
║  │ vlm_api_key = app-xxxxxxxxxxxxxxxx                         │  
║  │ bot_api_key = app-xxxxxxxxxxxxxxxx                         │  
║  │                                                            │  
║  │ [vlm]                                                      │  
║  │ rollout_mode = mify                                        │  
║  └────────────────────────────────────────────────────────────┘  
║                                                                  
║  详细说明: docs/configuration.md                                  
╚══════════════════════════════════════════════════════════════════╝
""", file=sys.stderr)


def check_config_status() -> dict:
    """检查配置状态，返回各项配置是否已设置"""
    effective_path, source = get_effective_config_path()
    return {
        "config_file_exists": effective_path is not None,
        "config_path": str(effective_path) if effective_path else str(VAF_CONFIG_PATH),
        "config_source": source,
        "vaf_config_exists": VAF_CONFIG_PATH.exists(),
        "xdg_config_exists": XDG_CONFIG_PATH.exists(),
        "feishu_app_id": bool(get_feishu_app_id()),
        "feishu_app_secret": bool(get_feishu_app_secret()),
        "dashscope_api_key": bool(get_dashscope_api_key()),
        "mify_api_key": bool(get_mify_api_key()),
        "vlm_rollout_mode": get_vlm_rollout_mode(),
    }


def print_config_status() -> None:
    """打印配置状态摘要"""
    status = check_config_status()
    
    print("VAF 配置状态检查")
    print("=" * 50)
    
    # 展示配置路径优先级
    print("配置路径（按优先级）:")
    if status['vaf_config_exists']:
        print(f"  1. {VAF_CONFIG_PATH}  ✅ 存在（当前使用）")
    else:
        print(f"  1. {VAF_CONFIG_PATH}  ❌ 不存在")
    
    if status['xdg_config_exists']:
        if status['vaf_config_exists']:
            print(f"  2. {XDG_CONFIG_PATH}  ⏸️ 存在（已跳过）")
        else:
            print(f"  2. {XDG_CONFIG_PATH}  ✅ 存在（当前使用）")
    else:
        print(f"  2. {XDG_CONFIG_PATH}  ❌ 不存在")
    
    print()
    if status['config_file_exists']:
        print(f"当前生效: {status['config_source']}")
    else:
        print("❌ 无配置文件，运行 'vaf config init' 创建")
    
    print()
    print("凭证配置:")
    print(f"  [feishu] app_id:      {'✓ 已配置' if status['feishu_app_id'] else '✗ 未配置'}")
    print(f"  [feishu] app_secret:  {'✓ 已配置' if status['feishu_app_secret'] else '✗ 未配置'}")
    print(f"  [dashscope] api_key:  {'✓ 已配置' if status['dashscope_api_key'] else '✗ 未配置'}")
    print(f"  [mify] api_key:       {'✓ 已配置' if status['mify_api_key'] else '✗ 未配置'}")
    print()
    print(f"VLM 模式: {status['vlm_rollout_mode']}")
    print()
    print("💡 配置路径优先级: ~/.vaf/config > ~/.config/vaf")


if __name__ == "__main__":
    print_config_status()
