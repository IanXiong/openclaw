#!/usr/bin/env python3
"""
MCP Client for Dubbo Test
调用 miapi-mcp 服务进行 Dubbo 接口测试
"""

import json
import os
import sys
import subprocess
from typing import Dict, Any


class MCPClient:
    """MCP 客户端"""

    def __init__(self, url: str, token: str):
        """
        初始化 MCP 客户端

        Args:
            url: MCP 服务 URL
            token: API Token
        """
        self.url = url
        self.token = token
        self.session_id = None

    def initialize_session(self) -> bool:
        """
        初始化 MCP 会话

        Returns:
            是否成功初始化
        """
        request_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "dubbo-test-client",
                    "version": "1.0.0"
                }
            }
        }

        try:
            # 使用 curl 发送请求
            curl_cmd = [
                'curl', '-s', '-D', '-', '-X', 'POST',
                self.url,
                '-H', f'X-API-Token: {self.token}',
                '-H', 'Content-Type: application/json',
                '-H', 'Accept: text/event-stream, application/json',
                '-d', json.dumps(request_payload)
            ]

            result = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace')

            # 解析响应头以获取 session ID
            response_text = result.stdout
            for line in response_text.split('\n'):
                if line.lower().startswith('mcp-session-id:'):
                    self.session_id = line.split(':', 1)[1].strip()
                    break

            return True

        except Exception as e:
            print(f"MCP session init failed: {e}", file=sys.stderr)
            return False

    def call_dubbo_test(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        调用 dubboTest 工具

        Args:
            params: 测试参数
                - interfaceName: Dubbo 服务全限定名
                - methodName: Dubbo 方法名
                - group: 服务分组
                - version: 服务版本
                - paramType: 参数类型（JSON 数组字符串）
                - parameter: 参数值（JSON 数组字符串）
                - userName: 用户名
                - addr: 指定 IP:Port
                - dubboTag: Dubbo Tag 路由标签
                - attachment: RpcContext 上下文

        Returns:
            MCP 响应结果
        """
        # 初始化会话
        if not self.session_id:
            self.initialize_session()

        # 确保 paramType 和 parameter 是字符串格式
        if 'paramType' in params and not isinstance(params['paramType'], str):
            params['paramType'] = json.dumps(params['paramType'])
        if 'parameter' in params and not isinstance(params['parameter'], str):
            params['parameter'] = json.dumps(params['parameter'])

        # 构造 MCP 请求
        request_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "dubboTest",
                "arguments": params
            }
        }

        try:
            # 调试：打印请求内容
            if '--debug' in sys.argv:
                print(f"DEBUG: Request payload: {json.dumps(request_payload, indent=2)}", file=sys.stderr)

            # 使用 curl 发送请求
            curl_cmd = [
                'curl', '-s', '-X', 'POST',
                self.url,
                '-H', f'X-API-Token: {self.token}',
                '-H', 'Content-Type: application/json',
                '-H', 'Accept: text/event-stream, application/json',
            ]
            # 如果有 session ID，添加到 header
            if self.session_id:
                curl_cmd.extend(['-H', f'mcp-session-id: {self.session_id}'])
            curl_cmd.extend(['-d', json.dumps(request_payload)])

            result = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace')

            if result.returncode != 0:
                return {
                    "error": {
                        "code": result.returncode,
                        "message": f"curl 命令执行失败: {result.stderr}"
                    }
                }

            # 解析 SSE 响应
            try:
                output = result.stdout
                # SSE 格式: data: {...}
                for line in output.split('\n'):
                    if line.startswith('data: '):
                        data_str = line[6:]  # 移除 "data: " 前缀
                        response = json.loads(data_str)
                        return response

                # 如果没有找到 data 行，尝试直接解析
                response = json.loads(output)
                return response

            except json.JSONDecodeError:
                return {
                    "error": {
                        "code": -2,
                        "message": f"JSON 解析失败",
                        "raw_output": result.stdout
                    }
                }

        except subprocess.TimeoutExpired:
            return {
                "error": {
                    "code": -3,
                    "message": "请求超时"
                }
            }
        except Exception as e:
            return {
                "error": {
                    "code": -1,
                    "message": f"执行失败: {str(e)}"
                }
            }


def main():
    """主函数"""
    # 从命令行参数读取测试参数
    if len(sys.argv) < 2:
        print(json.dumps({
            "error": "缺少参数，请提供 JSON 格式的测试参数"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)

    try:
        # 解析参数
        params = json.loads(sys.argv[1])

        # 从 mcp.json 读取配置（基于脚本所在目录定位，避免 CWD 依赖）
        script_dir = os.path.dirname(os.path.abspath(__file__))
        mcp_json_path = os.path.join(script_dir, 'mcp.json')
        with open(mcp_json_path, 'r', encoding='utf-8', errors='replace') as f:
            config = json.load(f)

        try:
            mcp_config = config['mcpServers']['miapi-mcp']
            url = mcp_config['url']
            token = mcp_config['headers']['X-API-Token']
        except KeyError as e:
            print(json.dumps({
                "error": f"mcp.json 配置缺失字段: {e}"
            }, ensure_ascii=False, indent=2))
            sys.exit(1)

        # 创建客户端并调用
        client = MCPClient(url, token)
        result = client.call_dubbo_test(params)

        # 输出结果
        print(json.dumps(result, ensure_ascii=False, indent=2))

    except json.JSONDecodeError as e:
        print(json.dumps({
            "error": f"参数解析失败: {str(e)}"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)
    except FileNotFoundError:
        print(json.dumps({
            "error": "未找到 mcp.json 配置文件"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({
            "error": f"执行失败: {str(e)}"
        }, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
