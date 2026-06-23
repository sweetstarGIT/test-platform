"""Extract user-facing failure details from automation logs and results."""
import html
import re
from typing import Dict, List


_MODULE_LINE = re.compile(r'(.+?)\s*->\s*(\S+)\s*:\s*(success|failed|skipped)\b', re.IGNORECASE)
_STEP_FAILED = re.compile(r'\[([a-zA-Z0-9_]+)\]\s*步骤失败[^:：]*[:：]\s*(.+)')
_MODULE_RESULT = re.compile(r'模块结果\s*[:：]\s*(failed|skipped|success)\s*[-－—]\s*(.+)', re.IGNORECASE)


def _clean_log_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r'^\[\d{2}:\d{2}:\d{2}\]\s*', '', text).strip()
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _clean_tab_name(value: str) -> str:
    text = _clean_log_text(value)
    text = re.sub(r'^[✅✓✗✔✕❌\s]+', '', text).strip()
    return text


def _merge_reason(old: str, new: str, prefer_new: bool = False) -> str:
    old = (old or "").strip()
    new = (new or "").strip()
    if not new:
        return old
    if prefer_new:
        return new
    if not old:
        return new
    if new in old:
        return old
    if old in new:
        return new
    return f"{old}；{new}"


def extract_failure_details_from_logs(logs: List[str], module_results: Dict[str, dict] | None = None) -> Dict[str, str]:
    """Return failure details keyed by both tab name and module name when possible."""
    details: Dict[str, str] = {}
    module_to_tab: Dict[str, str] = {}
    last_failed_tab = ""
    last_failed_module = ""

    for tab_name, result in (module_results or {}).items():
        module_name = (result or {}).get("module", "")
        if module_name:
            module_to_tab[module_name] = tab_name

    for raw_line in logs or []:
        line = _clean_log_text(raw_line)
        if not line:
            continue

        module_match = _MODULE_LINE.search(line)
        if module_match and "->" in line and ":" in line:
            tab_name = _clean_tab_name(module_match.group(1))
            module_name = module_match.group(2).strip()
            status = module_match.group(3).strip().lower()
            if tab_name and module_name:
                module_to_tab[module_name] = tab_name
                if status in ("failed", "skipped"):
                    last_failed_tab = tab_name
                    last_failed_module = module_name

        step_match = _STEP_FAILED.search(line)
        if step_match:
            module_name = step_match.group(1).strip()
            reason = step_match.group(2).strip()
            tab_name = module_to_tab.get(module_name, "")
            details[module_name] = _merge_reason(details.get(module_name, ""), reason)
            if tab_name:
                details[tab_name] = _merge_reason(details.get(tab_name, ""), reason)
            continue

        module_result_match = _MODULE_RESULT.search(line)
        if module_result_match:
            status = module_result_match.group(1).strip().lower()
            reason = module_result_match.group(2).strip()
            if status in ("failed", "skipped") and reason:
                targets = [last_failed_tab, last_failed_module]
                for module_name, tab_name in module_to_tab.items():
                    if tab_name and tab_name in reason:
                        targets.extend([tab_name, module_name])
                for target in {t for t in targets if t}:
                    details[target] = _merge_reason(details.get(target, ""), reason, prefer_new=True)

    for module_name, tab_name in module_to_tab.items():
        if module_name in details and tab_name and tab_name not in details:
            details[tab_name] = details[module_name]

    return details


def apply_failure_details_to_modules(module_results: Dict[str, dict], logs: List[str]) -> Dict[str, str]:
    """Mutate module_results with extracted message values and return extracted details."""
    details = extract_failure_details_from_logs(logs, module_results)
    for tab_name, result in (module_results or {}).items():
        module_name = (result or {}).get("module", "")
        status = (result or {}).get("status", "")
        if status not in ("failed", "skipped"):
            continue
        message = details.get(tab_name) or details.get(module_name) or ""
        current_message = (result or {}).get("message", "")
        generic_messages = {"", module_name, "部分功能模块测试失败", "部分功能模块被跳过", "测试执行异常"}
        if message and current_message in generic_messages:
            result["message"] = message
    return details


def summarize_failure_reason(test_result: dict | None, logs: List[str] | None = None, fallback: str = "") -> str:
    """Build a compact failure reason for task.error and callback testLog."""
    test_result = test_result or {}
    module_results = test_result.get("module_results") or {}
    details = extract_failure_details_from_logs(logs or [], module_results)

    reasons = []
    for tab_name, result in module_results.items():
        status = (result or {}).get("status", "")
        if status not in ("failed", "skipped"):
            continue
        module_name = (result or {}).get("module", "")
        message = (result or {}).get("message") or details.get(tab_name) or details.get(module_name)
        if not message:
            message = module_name or "模块测试失败"
        reasons.append(f"{tab_name}: {message}")

    if not reasons and details:
        reasons.extend(details.values())

    generic_errors = {
        "部分功能模块测试失败",
        "部分功能模块被跳过",
        "测试执行异常",
    }
    for step in test_result.get("steps") or []:
        error = str(step.get("error") or "").strip()
        if step.get("status") == "failed" and error:
            if reasons and (
                error in generic_errors
                or re.match(r"有\s*\d+\s*个包测试失败", error)
                or re.match(r"退出码\s*[:：]", error)
            ):
                continue
            reasons.append(error)

    compact = []
    for reason in reasons:
        reason = _clean_log_text(reason)
        if reason and reason not in compact:
            compact.append(reason)

    if compact:
        return "；".join(compact)[:1000]
    return (fallback or "自动测试失败")[:1000]
