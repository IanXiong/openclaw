#!/usr/bin/env python3
"""VAF 配置检查模块

在 vaf update 后或用户执行 vaf config check 时，
从飞书多维表格读取当前版本所需配置，与用户本地配置对比，给出引导提示。

Usage:
    python vaf_config_check.py [--version 2.2.2]
"""

from __future__ import annotations
import sys

# Windows 编码兼容：确保 stdout/stderr 使用 UTF-8，避免 emoji 输出时 GBK 编码错误
if sys.platform == 'win32':
    for stream in ('stdout', 'stderr', 'stdin'):
        s = getattr(sys, stream)
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')

import re
from dataclasses import dataclass, field
from pathlib import Path
from configparser import ConfigParser
from typing import Optional

try:
    from packaging.version import Version
except ImportError:
    Version = None

# 飞书多维表格配置（通过 URL 解析，不硬编码 token）
_BITABLE_WIKI_URL = "https://mi.feishu.cn/wiki/DYFAwESe0iVBOokbbg7czxVqnxA?table=tblD6El8XmFQIliS"


def _parse_bitable_url(url: str) -> tuple:
    """从 URL 解析 wiki_token 和 table_id"""
    table_match = re.search(r'[?&]table=([^&]+)', url)
    table_id = table_match.group(1) if table_match else None
    wiki_match = re.search(r'/wiki/([^?/]+)', url)
    wiki_token = wiki_match.group(1) if wiki_match else None
    return wiki_token, table_id


@dataclass
class ConfigItem:
    """配置项"""
    key: str
    value: str
    description: str
    required: bool = True


@dataclass
class ConfigDiff:
    """配置差异"""
    key: str
    expected: str
    actual: str
    description: str


@dataclass
class ConfigDiffResult:
    """配置差异结果"""
    is_valid: bool = True
    # 必填配置问题
    required_missing: list = field(default_factory=list)  # 必填配置缺失
    required_wrong: list = field(default_factory=list)    # 必填配置值错误
    # 建议配置问题
    optional_missing: list = field(default_factory=list)  # 建议配置缺失
    optional_wrong: list = field(default_factory=list)    # 建议配置值错误
    # 统计
    required_count: int = 0
    optional_count: int = 0
    
    @property
    def has_required_issues(self) -> bool:
        """是否有必填配置问题"""
        return bool(self.required_missing or self.required_wrong)
    
    @property
    def has_optional_issues(self) -> bool:
        """是否有建议配置问题"""
        return bool(self.optional_missing or self.optional_wrong)


def _extract_text(value) -> str:
    """从飞书多维表格字段值中提取纯文本

    字段值可能是：
    - 纯字符串: "hello"
    - 富文本列表: [{'text': 'hello', 'type': 'text'}]
    - dict: {'text': 'hello', 'type': 'text'}
    - None
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        # [{'text': 'xxx', 'type': 'text'}, ...] -> 拼接所有 text
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    if isinstance(value, dict):
        return value.get("text", "") or value.get("value", "")
    return str(value)


class ConfigChecker:
    """VAF 配置检查器"""

    def __init__(self, target_version: str):
        self.target_version = target_version
        self.required_configs: dict[str, ConfigItem] = {}
        self.user_configs: dict[str, str] = {}
        self.config_path: Optional[Path] = None
    
    def fetch_required_configs(self) -> bool:
        """从飞书多维表格获取当前版本所需配置（使用 CLI 方式）"""
        self._last_error = None  # 保存最后的错误信息
        try:
            # 动态导入 larkkit_deps 模块
            import os
            infra_dir = os.path.dirname(__file__)
            if infra_dir not in sys.path:
                sys.path.insert(0, infra_dir)
            import larkkit_deps
            
            # 从 URL 解析 wiki_token 和 table_id
            wiki_token, table_id = _parse_bitable_url(_BITABLE_WIKI_URL)
            if not wiki_token or not table_id:
                self._last_error = "无法解析配置表 URL"
                return False
            
            # 通过 CLI 获取 Wiki 内嵌表格的真实 app_token
            wiki_url = f"https://mi.feishu.cn/wiki/{wiki_token}"
            success, app_token, _, error_msg = larkkit_deps.run_larkkit_bitable_resolve_token(wiki_url)
            if not success or not app_token:
                self._last_error = error_msg or "无法获取 app_token"
                return False
            
            # 通过 CLI 读取所有记录
            success, records, error_msg = larkkit_deps.run_larkkit_bitable_list(
                url=app_token,
                table_id=table_id,
                page_size=500,
            )
            if not success:
                self._last_error = error_msg or "读取配置表失败"
                return False
            
            # 过滤出当前版本生效的配置
            for record in records:
                fields = record.get("fields", {})
                config_key = _extract_text(fields.get("配置项", ""))
                config_value = _extract_text(fields.get("值", ""))
                start_version = _extract_text(fields.get("生效起始版本", ""))
                end_version = _extract_text(fields.get("生效结束版本", ""))
                description = _extract_text(fields.get("说明", ""))

                if self._is_version_in_range(start_version, end_version):
                    # 从飞书表格读取"是否必填"字段
                    is_required_field = _extract_text(fields.get("是否必填", ""))
                    is_required = is_required_field == "是" or "必填" in description
                    
                    self.required_configs[config_key] = ConfigItem(
                        key=config_key,
                        value=config_value,
                        description=description,
                        required=is_required
                    )
            
            return True
            
        except Exception as e:
            # 保存错误信息，供调用方使用
            error_msg = str(e)
            if "access_token" in error_msg.lower() or "token" in error_msg.lower():
                self._last_error = "用户令牌无效或已过期，请执行 larkkit auth 重新授权"
            elif "permission" in error_msg.lower() or "403" in error_msg:
                self._last_error = "无权限访问配置表"
            elif "network" in error_msg.lower() or "connection" in error_msg.lower():
                self._last_error = "网络连接失败"
            else:
                self._last_error = error_msg
            return False
    
    def _is_version_in_range(self, start: str, end: str) -> bool:
        """判断当前版本是否在生效范围内（左闭右闭）
        
        如果 end 为空，表示一直生效到最新版本
        """
        if not start:
            return False
        
        if Version is None:
            # packaging 未安装，使用字符串比较
            if not end:
                return start <= self.target_version
            return start <= self.target_version <= end
        
        try:
            v = Version(self.target_version)
            v_start = Version(start)
            
            # 如果没有结束版本，表示一直生效
            if not end:
                return v_start <= v
            
            v_end = Version(end)
            return v_start <= v <= v_end
        except Exception:
            return False
    
    def load_user_configs(self) -> bool:
        """读取用户本地配置文件"""
        # 配置文件优先级: ~/.vaf/config > ~/.config/vaf
        config_paths = [
            Path.home() / ".vaf" / "config",
            Path.home() / ".config" / "vaf",
        ]
        
        for path in config_paths:
            if path.exists():
                self.config_path = path
                break
        
        if not self.config_path:
            return False
        
        config = ConfigParser()
        # 支持编码回退（UTF-8 → GBK），兼容 Windows 记事本保存的配置文件
        encodings = ['utf-8', 'gbk', 'gb2312', 'utf-8-sig']
        read_success = False
        for encoding in encodings:
            try:
                config.read(self.config_path, encoding=encoding)
                read_success = True
                break
            except UnicodeDecodeError:
                continue
            except Exception:
                return False
        
        if not read_success:
            return False
        
        # 将配置转换为 section.key 格式
        for section in config.sections():
            for key, value in config.items(section):
                full_key = f"{section}.{key}"
                self.user_configs[full_key] = value
        
        return True
    
    def compare_configs(self) -> ConfigDiffResult:
        """对比配置差异"""
        result = ConfigDiffResult()
        
        for key, item in self.required_configs.items():
            # 统计必填/建议配置数量
            if item.required:
                result.required_count += 1
            else:
                result.optional_count += 1
            
            if key not in self.user_configs:
                # 配置项缺失
                if item.required:
                    result.required_missing.append(item)
                else:
                    result.optional_missing.append(item)
            elif self.user_configs[key] != item.value:
                # 配置值不匹配
                diff = ConfigDiff(
                    key=key,
                    expected=item.value,
                    actual=self.user_configs[key],
                    description=item.description
                )
                if item.required:
                    result.required_wrong.append(diff)
                else:
                    result.optional_wrong.append(diff)
        
        # 只有必填配置有问题才算失败
        result.is_valid = not result.has_required_issues
        
        return result
    
    def print_guidance(self, result: ConfigDiffResult) -> None:
        """打印用户友好的引导提示"""
        if result.is_valid:
            self._print_success(result)
        else:
            self._print_diff_guidance(result)
    
    def _print_success(self, result: ConfigDiffResult) -> None:
        """打印成功提示"""
        config_display = str(self.config_path) if self.config_path else "~/.vaf/config"
        line = "─" * 66
        
        # 计算通过的配置数
        req_ok = result.required_count - len(result.required_missing) - len(result.required_wrong)
        opt_ok = result.optional_count - len(result.optional_missing) - len(result.optional_wrong)
        
        # 判断是否有建议配置问题
        if result.has_optional_issues:
            # 必填通过，建议有问题
            print(f"""
{line}
  ⚠️  VAF 配置需要完善
{line}
  当前版本: v{self.target_version}
  配置文件: {config_display}
{line}""")
            # 打印建议配置问题
            self._print_optional_issues(result)
            print(f"""
{line}
  💡 建议配置可优化文档下载效果，但不影响核心功能
  📖 详细配置指南: https://mi.feishu.cn/wiki/TJV6wVRJIiQuXAkzEmMcMJAonmH
{line}""")
        else:
            # 全部通过
            total = result.required_count + result.optional_count
            print(f"""
{line}
  ✅ VAF 配置检查通过
{line}
  当前版本: v{self.target_version}
  配置文件: {config_display}
  已检查 {total} 项配置，全部符合要求
{line}""")
    
    def _print_diff_guidance(self, result: ConfigDiffResult) -> None:
        """打印差异引导（必填配置有问题）"""
        config_display = str(self.config_path) if self.config_path else "~/.vaf/config"
        line = "─" * 66
        inner_line = "─" * 64
        
        print(f"""
{line}
  ❌ VAF 配置检查失败
{line}
  当前版本: v{self.target_version}
  配置文件: {config_display}
{line}""")
        
        # 必填配置问题（分开显示不对和缺失）
        if result.required_wrong:
            print(f"\n  ❗必填配置不对 ({len(result.required_wrong)}项):\n")
            self._print_wrong_configs(result.required_wrong)
        if result.required_missing:
            print(f"\n  ⚠️ 必填配置缺失 ({len(result.required_missing)}项):\n")
            self._print_missing_configs(result.required_missing)
        
        # 建议配置问题（如果有）
        if result.has_optional_issues:
            self._print_optional_issues(result)
        
        print(f"""
{line}
  ❌ 必填配置有问题，VAF 核心功能无法使用
  📝 请打开配置文件修改以上配置项: {config_display}
  🔍 修改完成后执行 vaf config check 验证
  📖 详细配置指南: https://mi.feishu.cn/wiki/TJV6wVRJIiQuXAkzEmMcMJAonmH
{line}""")
    
    def _print_optional_issues(self, result: ConfigDiffResult) -> None:
        """打印建议配置问题"""
        if result.optional_wrong:
            print(f"\n  📌 建议配置不对 ({len(result.optional_wrong)}项):\n")
            self._print_wrong_configs(result.optional_wrong)
        if result.optional_missing:
            print(f"\n  ⚠️ 建议配置缺失 ({len(result.optional_missing)}项):\n")
            self._print_missing_configs(result.optional_missing)
    
    def _print_wrong_configs(self, wrong_configs: list) -> None:
        """打印值错误的配置"""
        sections = self._group_by_section(
            [(d.key, d.expected, d.actual) for d in wrong_configs]
        )
        for section, items in sections.items():
            print(f"  [{section}]")
            for key, expected, actual in items:
                short_key = key.split(".")[-1]
                print(f"  {short_key}: {actual} -> {expected}")
    
    def _print_missing_configs(self, missing_configs: list) -> None:
        """打印缺失的配置"""
        sections = self._group_by_section(
            [(c.key, c.value, None) for c in missing_configs]
        )
        for section, items in sections.items():
            print(f"  [{section}]")
            for key, value, _ in items:
                short_key = key.split(".")[-1]
                print(f"  {short_key} = {value}")
    
    def _group_by_section(self, items: list) -> dict:
        """按 section 分组"""
        sections = {}
        for item in items:
            key = item[0]
            section = key.split(".")[0]
            if section not in sections:
                sections[section] = []
            sections[section].append(item)
        return sections


def check_config_for_version(version: str) -> bool:
    """检查指定版本的配置
    
    Args:
        version: VAF 版本号
    
    Returns:
        True: 配置检查通过
        False: 配置需要更新或检查失败
    """
    checker = ConfigChecker(version)
    
    # 读取用户配置（先检查配置文件是否存在）
    if not checker.load_user_configs():
        line = "─" * 66
        print(f"""
{line}
  ❌ VAF 配置文件不存在
{line}
  当前版本: v{version}
  期望路径: ~/.vaf/config

  💡 请执行 vaf config init 创建配置文件
  📖 详细配置指南: https://mi.feishu.cn/wiki/TJV6wVRJIiQuXAkzEmMcMJAonmH
{line}""")
        return False
    
    # 获取版本所需配置（需要飞书凭证）
    if not checker.fetch_required_configs():
        line = "─" * 66
        error_detail = getattr(checker, '_last_error', None)
        
        # 根据错误类型显示不同提示
        if error_detail and "令牌" in error_detail:
            print(f"""
{line}
  ⚠️  飞书用户令牌无效，跳过配置检查
{line}
  原因: {error_detail}
  
  💡 请执行以下命令重新授权:
     larkkit auth

  📖 详细配置指南: https://mi.feishu.cn/wiki/TJV6wVRJIiQuXAkzEmMcMJAonmH
{line}""")
        else:
            print(f"""
{line}
  ⚠️  无法连接飞书，跳过配置检查
{line}
  {f"原因: {error_detail}" if error_detail else ""}
  请确保配置文件中包含以下必填项:
  
  [feishu]
  app_id = <请参考配置指南>
  app_secret = <请参考配置指南>

  📖 详细配置指南: https://mi.feishu.cn/wiki/TJV6wVRJIiQuXAkzEmMcMJAonmH
{line}""")
        return True  # 不阻断流程
    
    # 对比配置
    result = checker.compare_configs()
    
    # 打印引导
    checker.print_guidance(result)
    
    return result.is_valid


def get_local_version() -> str:
    """获取本地 VAF 版本"""
    version_file = Path(__file__).parent.parent.parent.parent / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()
    return "2.2.2"  # 默认版本


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="VAF 配置检查")
    parser.add_argument("--version", "-v", help="指定检查的版本")
    args = parser.parse_args()
    
    # 获取版本
    version = args.version if args.version else get_local_version()
    
    success = check_config_for_version(version)
    sys.exit(0 if success else 1)
