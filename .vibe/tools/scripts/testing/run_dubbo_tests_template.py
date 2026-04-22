#!/usr/bin/env python3
"""
Dubbo 批量测试执行器
由 S05 生成，S07 执行。读取 dubbo_test_cases.json，逐条调用 mcp_client.py，输出结果。
"""
import json
import re
import uuid
import sys
import os
import subprocess
from datetime import datetime


def generate_trace_id(case_id):
    """生成 TraceID: it_dubbo_{case_id}_{timestamp}_{uuid前8位}"""
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    uid = str(uuid.uuid4())[:8]
    return f"it_dubbo_{case_id}_{ts}_{uid}"


def run_single_test(case, mcp_client_path, work_dir, defaults=None):
    """执行单条 Dubbo 测试，返回结果字典"""
    trace_id = generate_trace_id(case["id"])

    params = dict(case["request"])
    # 注入 TraceID 到 attachment
    attachment = {"traceId": trace_id}
    params["attachment"] = json.dumps(attachment)
    # 从 defaults 填充可选参数，避免硬编码
    if defaults:
        for k, v in defaults.items():
            params.setdefault(k, v)
    params.setdefault("addr", "")
    params.setdefault("dubboTag", "")

    start = datetime.now()
    try:
        # 强制子进程 UTF-8 输出，避免 Windows GBK 编码导致中文丢失
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        result = subprocess.run(
            ["python", mcp_client_path, json.dumps(params)],
            capture_output=True, timeout=30, cwd=work_dir, env=env
        )
        duration_ms = int((datetime.now() - start).total_seconds() * 1000)
        stdout_str = result.stdout.decode('utf-8', errors='replace') if result.stdout else ""
        stderr_str = result.stderr.decode('utf-8', errors='replace') if result.stderr else ""
        try:
            if result.returncode == 0 and stdout_str.strip():
                response = json.loads(stdout_str)
            else:
                response = {"error": {"code": result.returncode, "message": stderr_str or stdout_str or "Empty response"}}
        except (json.JSONDecodeError, TypeError):
            response = {"error": {"code": -2, "message": "JSON parse failed", "raw_output": stdout_str[:500]}}
    except subprocess.TimeoutExpired:
        duration_ms = 30000
        response = {"error": {"code": -3, "message": "Timeout"}}
    except Exception as e:
        duration_ms = int((datetime.now() - start).total_seconds() * 1000)
        response = {"error": {"code": -1, "message": str(e)}}

    passed = evaluate_result(response, case.get("expected", {}))

    return {
        "case_id": case["id"],
        "level": case["level"],
        "scenario": case["scenario"],
        "trace_id": trace_id,
        "passed": passed,
        "duration_ms": duration_ms,
        "response": response
    }


# Dubbo 级别错误模式（出现在 MCP result.content[0].text 中，表示服务端调用失败）
DUBBO_ERROR_PATTERNS = [
    "No provider available",
    "ClassNotFoundException",
    "RemotingException",
    "RpcException",
    "Failed to invoke",
]


def extract_business_response(response):
    """从 MCP 嵌套响应中提取 Dubbo 业务响应文本

    MCP 响应结构: { "result": { "content": [{ "text": "..." }] } }
    Dubbo 泛化调用的实际结果嵌套在 text 字段中。
    """
    try:
        content = response.get("result", {}).get("content", [])
        if content and isinstance(content, list) and len(content) > 0:
            first = content[0]
            text = first.get("text", "") if isinstance(first, dict) else ""
            if text:
                return text
    except (KeyError, IndexError, TypeError):
        pass
    return None


def extract_business_code(response):
    """从 MCP 嵌套响应文本中提取业务 code 值

    业务响应格式: res={"code":0,...} 或 res={\"code\":9000,...}
    code:0 表示业务成功，非 0 表示业务错误。
    """
    biz_text = extract_business_response(response)
    if biz_text:
        # MCP 文本中 code 字段可能带转义引号: \"code\":9000
        code_match = re.search(r'code[^:]*:\s*(\d+)', biz_text)
        if code_match:
            return int(code_match.group(1))
    return None


def has_dubbo_error(response):
    """检查 MCP 响应中是否包含 Dubbo 级别错误"""
    biz_text = extract_business_response(response)
    if biz_text:
        for pattern in DUBBO_ERROR_PATTERNS:
            if pattern in biz_text:
                return True, pattern
    return False, None


def evaluate_result(response, expected):
    """基于 expected 规则判定 PASS/FAIL

    判定逻辑（三层）：
    1. MCP 顶层 error → 网络/协议错误
    2. Dubbo 级别错误 → No provider / ClassNotFound 等基础设施错误
    3. 业务码 code:0=成功, code:非0=业务错误
    """
    has_error = "error" in response
    dubbo_err, dubbo_pattern = has_dubbo_error(response)
    biz_code = extract_business_code(response)
    biz_text = extract_business_response(response) or ""

    if expected.get("success") is False:
        # 期望失败的场景：MCP 错误 / Dubbo 错误 / 业务码非 0 都算 PASS
        if has_error:
            msg = str(response.get("error", ""))
            keyword = expected.get("error_message_contains", "")
            return keyword == "" or keyword in msg
        if dubbo_err:
            keyword = expected.get("error_message_contains", "")
            return keyword == "" or keyword in biz_text
        if biz_code is not None and biz_code != 0:
            keyword = expected.get("error_message_contains", "")
            return keyword == "" or keyword in biz_text
        return False

    # 期望成功：任何错误都是 FAIL
    if has_error:
        return False
    if dubbo_err:
        return False
    if biz_code is not None and biz_code != 0:
        return False

    # 检查 response_contains 关键字
    resp_str = json.dumps(response, ensure_ascii=False)
    search_text = resp_str + biz_text
    for keyword in expected.get("response_contains", []):
        if keyword not in search_text:
            return False
    return True


def find_project_root(start_dir):
    """从 start_dir 向上查找包含 .vibe/ 或 .git/ 的目录作为项目根"""
    current = os.path.abspath(start_dir)
    while True:
        if os.path.isdir(os.path.join(current, ".vibe")) or os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            raise RuntimeError(f"Cannot find project root from {start_dir} (.vibe/ or .git/ required)")
        current = parent


# --- FQCN pre-check ---------------------------------------------------

# Java built-in types, no need to validate against source
JAVA_BUILTIN_PREFIXES = (
    "java.", "javax.", "int", "long", "boolean", "double", "float",
    "short", "byte", "char", "void",
)


def fqcn_to_path(fqcn):
    """Convert FQCN to relative .java file path: com.foo.Bar -> com/foo/Bar.java"""
    return fqcn.replace(".", "/") + ".java"


def find_java_source_roots(project_root):
    """Auto-discover all src/main/java directories in the project"""
    roots = []
    for dirpath, dirnames, _ in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in ('target', 'build', 'node_modules')]
        if os.path.basename(dirpath) == "java" and dirpath.replace("\\", "/").endswith("src/main/java"):
            roots.append(dirpath)
    return roots


def validate_fqcns(cases, project_root):
    """
    Pre-flight check: verify all interfaceName and paramType FQCNs match actual .java files.
    Returns (errors, warnings). Errors are blockers.
    """
    source_roots = find_java_source_roots(project_root)
    if not source_roots:
        return [], ["No Java source roots found, skipping FQCN validation"]

    fqcn_usage = {}  # fqcn -> [(case_id, field_name), ...]
    for case in cases:
        req = case.get("request", {})
        iface = req.get("interfaceName", "")
        if iface:
            fqcn_usage.setdefault(iface, []).append((case["id"], "interfaceName"))
        param_type_str = req.get("paramType", "[]")
        try:
            param_types = json.loads(param_type_str)
            for pt in param_types:
                if pt and isinstance(pt, str) and not any(pt.startswith(p) for p in JAVA_BUILTIN_PREFIXES):
                    fqcn_usage.setdefault(pt, []).append((case["id"], "paramType"))
        except (json.JSONDecodeError, TypeError):
            pass

    errors = []
    for fqcn, usages in fqcn_usage.items():
        rel_path = fqcn_to_path(fqcn)
        found = False
        for root in source_roots:
            full_path = os.path.join(root, rel_path)
            if os.path.isfile(full_path):
                found = True
                break
        if not found:
            case_ids = sorted(set(cid for cid, _ in usages))
            field = usages[0][1]
            simple_name = fqcn.rsplit(".", 1)[-1] + ".java"
            candidates = []
            for root in source_roots:
                for dp, _, fns in os.walk(root):
                    if simple_name in fns:
                        actual_path = os.path.join(dp, simple_name)
                        rel = os.path.relpath(actual_path, root).replace("\\", "/").replace("/", ".")
                        actual_fqcn = rel[:-5]
                        candidates.append(actual_fqcn)

            msg = f"  FQCN not found: {fqcn}\n"
            msg += f"    used by: {', '.join(case_ids)} ({field})\n"
            if candidates:
                msg += f"    suggested fix:\n"
                for c in candidates[:3]:
                    msg += f"      -> {c}\n"
            else:
                msg += f"    no match found for class {simple_name}\n"
            errors.append(msg)

    return errors, []


def main():
    cases_file = sys.argv[1] if len(sys.argv) > 1 else "dubbo_test_cases.json"

    with open(cases_file, encoding="utf-8", errors="replace") as f:
        suite = json.load(f)

    tool_config = suite.get("tool", {})
    mcp_client = tool_config.get("mcp_client", "tools/dubbo_test/mcp_client.py")
    work_dir = tool_config.get("work_dir", "tools/dubbo_test")
    defaults = tool_config.get("defaults", {})

    # 向上查找项目根目录，转换为绝对路径
    cases_dir = os.path.dirname(os.path.abspath(cases_file))
    project_root = find_project_root(cases_dir)
    mcp_client_path = os.path.join(project_root, mcp_client)
    work_dir_path = os.path.join(project_root, work_dir)

    # --- FQCN pre-check ---
    print("=" * 60)
    print("[PRE-CHECK] FQCN validation: interfaceName / paramType vs source code...")
    fqcn_errors, fqcn_warnings = validate_fqcns(suite.get("cases", []), project_root)
    for w in fqcn_warnings:
        print(f"  [WARN] {w}")
    if fqcn_errors:
        print(f"\n[FAIL] {len(fqcn_errors)} FQCN error(s) found:\n")
        for e in fqcn_errors:
            print(e)
        print("[BLOCKER] Fix FQCN before running tests.")
        print("  Wrong FQCN causes 'No provider available' or 'ClassNotFoundException',")
        print("  which looks like 'service not deployed' but is actually wrong interface name.")
        print("=" * 60)
        sys.exit(1)
    else:
        print("  [OK] All FQCNs match source code")
    print("=" * 60 + "\n")

    results = []
    for case in suite.get("cases", []):
        result = run_single_test(case, mcp_client_path, work_dir_path, defaults)
        results.append(result)
        status = "PASS" if result["passed"] else "FAIL"
        print(f"[{status}] {result['case_id']} | {result['scenario']} | TraceID: {result['trace_id']} | {result['duration_ms']}ms")

    # 汇总
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    pct = (passed / total * 100) if total > 0 else 0
    print(f"\n--- Result: {passed}/{total} passed ({pct:.0f}%) ---")

    # 写入结果文件
    output_file = os.path.join(os.path.dirname(cases_file), "dubbo_test_results.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"summary": {"total": total, "passed": passed}, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"Results written to: {output_file}")


if __name__ == "__main__":
    main()
