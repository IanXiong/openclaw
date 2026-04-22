#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VAF 埋点模块 - 飞书多维表格操作

功能：
1. 飞书表格配置解析
2. 记录查询/新增/更新
3. IDE-模型映射读取
4. larkkit 安装检测
"""

import sys

# Windows 编码兼容：确保 stdout/stderr 使用 UTF-8，避免 emoji 输出时 GBK 编码错误
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import re
import subprocess
from typing import Optional, Dict, Any

# ============ 飞书埋点表格配置 ============
# VAF 公共埋点表 URL（内置，无需配置）
_BITABLE_WIKI_URL = "https://mi.feishu.cn/wiki/Kcj7wqWzDihlbZkrrLqca4DFnUc?table=tbliFQXrjx1BsKT3"

# IDE-模型映射表 URL
_IDE_MODEL_WIKI_URL = "https://mi.feishu.cn/wiki/Kcj7wqWzDihlbZkrrLqca4DFnUc?table=tblqfzdnGFaJZkLd"

# 缓存解析结果，避免重复调用 API
_bitable_cache: dict = {}


# 默认模型列表（飞书表读取失败时使用，需与飞书表格保持同步）
_DEFAULT_MODELS = [
    "GPT-5.2-Codex",
    "GPT-5.1-Codex",
    "GPT-5.1",
    "GPT-5.2",
    "Claude Opus 4.5",
    "Claude Sonnet 4.5",
    "Gemini 3 Pro",
    "Gemini 2.5 Pro",
    "GLM 4.7",
    "MiMo-V2-Flash",
    "DeepSeek-V3.1",
    "其他",
]

# 整体匹配表（小写 -> 标准名称）- 用于快速路径
_MODEL_FULL_MAP = {m.lower(): m for m in _DEFAULT_MODELS}

# 片段标准化映射（小写 -> 标准格式）
_SEGMENT_MAP = {
    "gpt": "GPT",
    "codex": "Codex",
    "claude": "Claude",
    "opus": "Opus",
    "sonnet": "Sonnet",
    "gemini": "Gemini",
    "pro": "Pro",
    "mimo": "MiMo",
    "flash": "Flash",
    "glm": "GLM",
    "deepseek": "DeepSeek",
    "v2": "V2",
    "v3": "V3",
}

# 模型系列 -> 分隔符（根据标准模型名确定）
_MODEL_SEPARATOR = {
    "GPT": "-",       # GPT-5.2-Codex
    "Claude": " ",    # Claude Opus 4.5
    "Gemini": " ",    # Gemini 3 Pro
    "MiMo": "-",      # MiMo-V2-Flash
    "GLM": " ",       # GLM 4.7
    "DeepSeek": "-",  # DeepSeek-V3.1
}


def normalize_model_name(model: str) -> str:
    """标准化模型名称（忽略大小写，统一分隔符）
    
    处理流程：
    1. 快速路径：整体匹配（忽略大小写）
    2. 分段匹配：按 - 或空格分割，逐段标准化，按模型系列确定分隔符
    
    示例：
    - gpt-5.2-codex -> GPT-5.2-Codex
    - GPT 5.2 Codex -> GPT-5.2-Codex
    - Gemini-2.5-Pro -> Gemini 2.5 Pro
    - claude opus 4.5 -> Claude Opus 4.5
    """
    if not model:
        return model
    
    model = model.strip()
    
    # 1. 快速路径：整体匹配
    if model.lower() in _MODEL_FULL_MAP:
        return _MODEL_FULL_MAP[model.lower()]
    
    # 2. 分段匹配
    # 按 - 或空格分割
    segments = re.split(r'[-\s]+', model)
    if not segments:
        return model
    
    # 标准化每个片段
    normalized = []
    for seg in segments:
        normalized.append(_SEGMENT_MAP.get(seg.lower(), seg))
    
    # 根据首段确定分隔符
    first_seg = normalized[0]
    separator = _MODEL_SEPARATOR.get(first_seg, "-")
    
    return separator.join(normalized)


def _parse_bitable_url(url: str) -> tuple:
    """从 URL 解析 wiki_token 和 table_id
    
    Returns:
        (wiki_token, table_id)
    """
    # 解析 table_id
    table_match = re.search(r'[?&]table=([^&]+)', url)
    table_id = table_match.group(1) if table_match else None
    
    # 解析 wiki_token
    wiki_match = re.search(r'/wiki/([^?/]+)', url)
    wiki_token = wiki_match.group(1) if wiki_match else None
    
    return wiki_token, table_id


# ============ CLI 封装导入 ============

def _get_larkkit_deps():
    """动态导入 larkkit_deps 模块"""
    import os
    infra_dir = os.path.join(os.path.dirname(__file__), '..', 'infra')
    if infra_dir not in sys.path:
        sys.path.insert(0, os.path.abspath(infra_dir))
    import larkkit_deps
    return larkkit_deps


def _get_bitable_app_token_from_wiki(wiki_token: str) -> Optional[str]:
    """通过 larkkit CLI 从 wiki_token 获取真实的 app_token"""
    try:
        larkkit_deps = _get_larkkit_deps()
        
        # 构造 wiki URL
        wiki_url = f"https://mi.feishu.cn/wiki/{wiki_token}"
        
        success, app_token, _, error_msg = larkkit_deps.run_larkkit_bitable_resolve_token(wiki_url)
        if success and app_token:
            return app_token
        else:
            if error_msg:
                print(f"⚠️ 获取 app_token 失败: {error_msg}")
            return None
    except Exception as e:
        print(f"⚠️ 获取 app_token 失败: {e}")
        return None


def get_bitable_app_token() -> Optional[str]:
    """获取飞书多维表格 App Token（用于埋点上报）
    
    自动从内置 URL 解析，无需配置
    """
    # 检查缓存（只缓存成功的结果）
    if _bitable_cache.get("app_token"):
        return _bitable_cache["app_token"]
    
    # 从内置 URL 解析
    wiki_token, _ = _parse_bitable_url(_BITABLE_WIKI_URL)
    if wiki_token:
        app_token = _get_bitable_app_token_from_wiki(wiki_token)
        if app_token:
            _bitable_cache["app_token"] = app_token
            return app_token
    
    return None


def get_bitable_table_id() -> Optional[str]:
    """获取飞书多维表格 Table ID（用于埋点上报）
    
    自动从内置 URL 解析，无需配置
    """
    # 检查缓存
    if "table_id" in _bitable_cache:
        return _bitable_cache["table_id"]
    
    # 从内置 URL 解析
    _, table_id = _parse_bitable_url(_BITABLE_WIKI_URL)
    if table_id:
        _bitable_cache["table_id"] = table_id
        return table_id
    
    return None


def _ensure_larkkit_installed() -> bool:
    """确保 larkkit 已安装（使用 uv 方案，与 vaf.py 保持一致）
    
    Returns:
        True: larkkit 已就绪，可继续执行
        False: 安装失败
    """
    # 添加 infra 目录到 path 以导入 larkkit_deps
    import os
    infra_dir = os.path.join(os.path.dirname(__file__), '..', 'infra')
    if infra_dir not in sys.path:
        sys.path.insert(0, os.path.abspath(infra_dir))
    
    from larkkit_deps import ensure_larkkit
    return ensure_larkkit()


def _extract_single_select_value(field_value) -> Optional[str]:
    """从单选字段提取值

    单选字段可能返回：
    - 字符串: "Claude Sonnet 4"
    - dict: {"text": "Claude Sonnet 4", "id": "xxx"}
    - list: [{"text": "Claude Sonnet 4", "type": "text"}]
    - None
    """
    if field_value is None:
        return None
    if isinstance(field_value, str):
        return field_value
    if isinstance(field_value, list):
        parts = []
        for item in field_value:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts) or None
    if isinstance(field_value, dict):
        return field_value.get("text") or field_value.get("value")
    return str(field_value)


# 注意：_get_all_models_from_table 函数已废弃，不再使用
# 改造后 get_ide_model_options 直接返回 _DEFAULT_MODELS 作为降级方案


def get_ide_model_options(ide_tool: str) -> list:
    """根据 IDE 工具获取可选的模型列表（使用 CLI 方式）
    
    从飞书多维表格读取 IDE-模型映射关系，按排序字段返回
    
    表格结构：每行一个模型，字段包括 IDE工具(单选)、模型(单选)、排序(数字)
    
    Args:
        ide_tool: IDE 工具名称（如 Cursor, Windsurf）
    
    Returns:
        模型名称列表，按排序字段升序排列
        如果读取失败或无匹配，返回所有模型（按排序去重）
    """
    # 确保 larkkit 已安装（未安装时自动安装）
    if not _ensure_larkkit_installed():
        return _DEFAULT_MODELS
    
    try:
        larkkit_deps = _get_larkkit_deps()
        
        # 解析 URL
        wiki_token, table_id = _parse_bitable_url(_IDE_MODEL_WIKI_URL)
        if not wiki_token or not table_id:
            return _DEFAULT_MODELS
        
        # 获取 app_token（通过 CLI）
        wiki_url = f"https://mi.feishu.cn/wiki/{wiki_token}"
        success, app_token, _, _ = larkkit_deps.run_larkkit_bitable_resolve_token(wiki_url)
        
        if not success or not app_token:
            return _DEFAULT_MODELS
        
        # 查询表格记录（按 IDE 筛选，通过 CLI）
        filter_expr = f'CurrentValue.[IDE工具]="{ide_tool}"'
        success, records, _ = larkkit_deps.run_larkkit_bitable_list(
            url=app_token,  # 使用 app_token 作为 URL
            table_id=table_id,
            filter_expr=filter_expr,
        )
        
        if not success:
            return _DEFAULT_MODELS
        
        # 解析模型列表，按排序字段排序
        if records:
            model_with_order = []
            for record in records:
                fields = record.get("fields", {})
                order = fields.get("排序", 9999)
                model_field = fields.get("模型")
                model_name = _extract_single_select_value(model_field)
                if model_name:
                    model_with_order.append((order, model_name))
            
            model_with_order.sort(key=lambda x: x[0])
            models = []
            for _, model_name in model_with_order:
                if model_name not in models:
                    models.append(model_name)
            if models:
                return models
        
        # 未找到匹配记录，返回默认模型
        return _DEFAULT_MODELS
        
    except Exception:
        return _DEFAULT_MODELS


def find_existing_record(req_pool: str, service: str, branch: str, phase: str) -> Optional[str]:
    """根据唯一键查找现有记录（使用 CLI 方式）
    
    唯一键：需求池 + 服务 + 分支名 + 执行阶段
    注意：Hub 模式下服务为空，需要特殊处理
    
    Returns:
        record_id 或 None
    """
    try:
        larkkit_deps = _get_larkkit_deps()
        
        # 构建筛选条件（字段名必须与飞书表一致）
        if service:
            filter_expr = f'AND(CurrentValue.[需求池]="{req_pool}", CurrentValue.[服务]="{service}", CurrentValue.[分支名]="{branch}", CurrentValue.[执行阶段]="{phase}")'
        else:
            filter_expr = f'AND(CurrentValue.[需求池]="{req_pool}", CurrentValue.[分支名]="{branch}", CurrentValue.[执行阶段]="{phase}")'
        
        print(f"   查询参数: 需求池={req_pool}, 服务={service or '(空)'}, 分支={branch}, 阶段={phase}")
        print(f"   筛选条件: {filter_expr}")
        
        # 获取 app_token 和 table_id
        app_token = get_bitable_app_token()
        table_id = get_bitable_table_id()
        if not app_token:
            # 可能是授权刚完成，重试一次
            app_token = get_bitable_app_token()
            if not app_token:
                print("⚠️ 无法获取 app_token，跳过查询")
                return None
        
        # 使用 CLI 查询
        success, records, error_msg = larkkit_deps.run_larkkit_bitable_list(
            url=app_token,
            table_id=table_id,
            filter_expr=filter_expr,
            page_size=10,
        )
        
        if not success:
            print(f"   ⚠️ 查询失败: {error_msg}")
            return None
        
        print(f"   查询返回记录数量: {len(records)}")
        
        if records and len(records) > 0:
            # 如果是 Hub 模式，还需要过滤掉有服务的记录
            if not service:
                for record in records:
                    fields = record.get("fields", {})
                    record_service = _extract_single_select_value(fields.get("服务"))
                    print(f"   检查记录: record_id={record.get('record_id', '')[:8]}..., 服务={record_service or '(空)'}")
                    # 服务字段为空或不存在的才是 Hub 记录
                    if not record_service:
                        print(f"   ✅ 匹配到 Hub 记录")
                        return record.get("record_id")
                print(f"   ⚠️ 未找到服务为空的记录")
            else:
                print(f"   ✅ 匹配到 Service 记录")
                return records[0].get("record_id")
        else:
            print(f"   📝 查询结果为空")
    except ImportError:
        print("⚠️ larkkit 未安装，请运行 vaf deps upgrade")
    except Exception as e:
        import traceback
        print(f"⚠️ 查询记录失败: {e}")
        traceback.print_exc()
    
    return None


def report_to_bitable(data: Dict[str, Any], record_id: Optional[str] = None) -> bool:
    """上报数据到飞书多维表格（使用 CLI 方式）"""
    try:
        larkkit_deps = _get_larkkit_deps()
        
        # 获取 app_token 和 table_id
        app_token = get_bitable_app_token()
        table_id = get_bitable_table_id()
        if not app_token:
            print("⚠️ 埋点上报失败：无法获取 app_token")
            return False
        
        if record_id:
            # 更新现有记录（使用 CLI）
            success, error_msg = larkkit_deps.run_larkkit_bitable_update(
                url=app_token,
                table_id=table_id,
                record_id=record_id,
                fields=data,
            )
            if success:
                print(f"✅ 埋点更新成功: {record_id[:8]}...")
            else:
                print(f"⚠️ 埋点更新失败: {error_msg}")
                return False
        else:
            # 新增记录（使用 CLI）
            success, new_id, error_msg = larkkit_deps.run_larkkit_bitable_create(
                url=app_token,
                table_id=table_id,
                fields=data,
            )
            if success:
                print(f"✅ 埋点新增成功: {new_id[:8] if len(new_id) > 8 else new_id}...")
            else:
                print(f"⚠️ 埋点新增失败: {error_msg}")
                return False
        return True
    except ImportError:
        print("⚠️ 埋点上报失败：larkkit 未安装，请运行 vaf deps upgrade")
    except Exception as e:
        error_msg = str(e)
        if "token" in error_msg.lower() or "auth" in error_msg.lower():
            print("⚠️ 埋点上报失败：飞书授权已过期，请运行 vaf lark auth")
        elif "field" in error_msg.lower():
            print(f"⚠️ 埋点上报失败：字段不存在 - {error_msg}")
        else:
            print(f"⚠️ 埋点上报失败：{error_msg}")
    
    return False


