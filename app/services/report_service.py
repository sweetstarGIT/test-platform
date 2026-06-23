"""报告生成服务 - 支持单任务报告和批量汇总报告
设计风格：Data-Dense Dashboard（基于 UI/UX Pro Max 设计系统）
配色：#1E40AF / #3B82F6 / #F59E0B / #F8FAFC
"""
import os
import re
import base64
import html as html_lib
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from app.config import REPORT_DIR, TESTCASE_PROJECT_DIR
from app.services.result_details import apply_failure_details_to_modules

def _extract_detailed_results_from_html(html_content: str) -> Dict[str, str]:
    """从 testcase 生成的 HTML 报告中提取详细功能测试结果

    解析格式:
    <div class="module-result success">
        <span>记账</span>
        <span>记账操作完成：支出100元（餐饮），收入200元</span>
    </div>
    """
    import re
    results = {}
    if not html_content:
        return results

    # 匹配 module-result div 中的两个 span
    # 第一个 span 是功能名，第二个 span 是详细描述
    pattern = re.compile(
        r'<div[^>]*class=["\']module-result[^"\']*["\'][^>]*>\s*'
        r'<span[^>]*>(.*?)</span>\s*'
        r'<span[^>]*>(.*?)</span>\s*'
        r'</div>',
        re.DOTALL | re.IGNORECASE
    )

    for match in pattern.finditer(html_content):
        tab_name = re.sub(r'<[^>]+>', '', match.group(1)).strip()
        detail = re.sub(r'<[^>]+>', '', match.group(2)).strip()
        if tab_name and detail:
            results[tab_name] = detail

    # 新版 UI-Automation 报告结构：
    # <div class="module-result ..."><div class="module-result-main"><span>推荐</span><span>详情</span></div>
    main_pattern = re.compile(
        r'<div[^>]*class=["\'][^"\']*\bmodule-result-main\b[^"\']*["\'][^>]*>\s*'
        r'<span[^>]*>(.*?)</span>\s*'
        r'<span[^>]*>(.*?)</span>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in main_pattern.finditer(html_content):
        tab_name = _strip_html(match.group(1))
        detail = _strip_html(match.group(2))
        if tab_name and detail:
            results[tab_name] = detail

    return results


def _module_blocks_from_html(html_content: str) -> List[str]:
    """Split a UI-Automation report into per-module HTML blocks."""
    module_starts = [
        match.start()
        for match in re.finditer(
            r'<div[^>]*class=["\'][^"\']*(?<![\w-])module-result(?![\w-])[^"\']*["\'][^>]*>',
            html_content,
            re.IGNORECASE,
        )
    ]
    blocks = []
    for index, start in enumerate(module_starts):
        end = module_starts[index + 1] if index + 1 < len(module_starts) else len(html_content)
        blocks.append(html_content[start:end])
    return blocks or [html_content]


def _extract_module_tab_name(block: str) -> str:
    tab_match = re.search(
        r'<div[^>]*class=["\']module-result-main["\'][^>]*>\s*<span[^>]*>(.*?)</span>',
        block,
        re.DOTALL | re.IGNORECASE,
    )
    return _strip_html(tab_match.group(1)) if tab_match else ""


def _iter_divs_by_class(html_content: str, class_name: str):
    pattern = re.compile(
        rf'<div\b[^>]*class=["\'][^"\']*\b{re.escape(class_name)}\b[^"\']*["\'][^>]*>',
        re.IGNORECASE,
    )
    for match in pattern.finditer(html_content):
        end = _find_matching_div_end(html_content, match.start())
        if end <= match.end():
            end = html_content.find("</div>", match.end())
            end = len(html_content) if end < 0 else end + len("</div>")
        inner_end = max(match.end(), end - len("</div>"))
        yield match.start(), end, html_content[match.end():inner_end]


def _find_matching_div_end(html_content: str, start: int) -> int:
    tag_pattern = re.compile(r'</?div\b[^>]*>', re.IGNORECASE)
    depth = 0
    for match in tag_pattern.finditer(html_content, start):
        tag = match.group(0).lower()
        if tag.startswith("</div"):
            depth -= 1
            if depth == 0:
                return match.end()
        else:
            depth += 1
    return -1


def _extract_ui_issues_from_html(html_content: str, testcase_report_path: str = "") -> List[Dict[str, object]]:
    """Extract UI visual issues and screenshots from the UI-Automation report."""
    if not html_content:
        return []

    report_dir = os.path.dirname(os.path.abspath(testcase_report_path)) if testcase_report_path else ""
    blocks = _module_blocks_from_html(html_content)

    issues: List[Dict[str, str]] = []
    for block in blocks:
        if "ai-issue-item" not in block and "问题截图" not in block:
            continue

        tab_name = _extract_module_tab_name(block)

        issue_messages = []
        for class_name, issue_html in re.findall(
            r'<div[^>]*class=["\']([^"\']*\bai-issue-item\b[^"\']*)["\'][^>]*>(.*?)</div>',
            block,
            re.DOTALL | re.IGNORECASE,
        ):
            raw_text = _strip_html(issue_html)
            if _is_ignored_ui_issue_text(raw_text):
                continue
            text = _clean_issue_message(raw_text)
            if not text or _is_ignored_ui_issue_text(text):
                continue
            issue_messages.append({
                "severity": _issue_severity_from_class(class_name),
                "text": text,
            })

        screenshot_src = ""
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', block, re.IGNORECASE)
        if img_match:
            screenshot_src = html_lib.unescape(img_match.group(1))

        screenshot_path = _resolve_testcase_asset_path(screenshot_src, report_dir)
        screenshot_data_uri = _image_to_data_uri(screenshot_path) if screenshot_path else ""

        if issue_messages or screenshot_data_uri:
            if not issue_messages:
                continue
            issues.append({
                "tab_name": tab_name,
                "messages": issue_messages,
                "screenshot_path": screenshot_path,
                "screenshot_data_uri": screenshot_data_uri,
            })

    return issues


def _strip_html(value: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', value or '')
    text = re.sub(r'\s+', ' ', text).strip()
    return html_lib.unescape(text)


def _issue_severity_from_class(class_name: str) -> str:
    classes = set((class_name or "").split())
    if "failed" in classes:
        return "failed"
    if "warning" in classes:
        return "warning"
    if "info" in classes:
        return "info"
    return "warning"


def _clean_issue_message(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r'^(问题|警告|提示)\s*', '', text)
    legacy_match = re.match(r'^(?:failed|warning|info)\s*/\s*[^/]+/\s*(.+)$', text, re.IGNORECASE)
    if legacy_match:
        return legacy_match.group(1).strip()
    legacy_match = re.match(r'^(?:failed|warning|info)\s*/\s*[^:：]+[:：]\s*(.+)$', text, re.IGNORECASE)
    if legacy_match:
        return legacy_match.group(1).strip()
    legacy_match = re.match(r'^[a-z0-9_]+\s*/\s*(.+)$', text, re.IGNORECASE)
    if legacy_match:
        return legacy_match.group(1).strip()
    legacy_match = re.match(r'^[a-z0-9_]+\s*[:：]\s*(.+)$', text, re.IGNORECASE)
    if legacy_match:
        return legacy_match.group(1).strip()
    return text


def _is_ignored_ui_issue_text(text: str) -> bool:
    if not text:
        return True
    ignored_fragments = (
        "popup_overlay",
        "快应用胶囊悬浮",
        "快应用悬浮控制条",
        "右上角存在快应用",
        "底部导航区域被内容卡片侵入",
        "bottom_nav_overlap",
        "button_occluded",
    )
    if any(fragment in text for fragment in ignored_fragments):
        return True
    safe_labels = ("继续追剧", "最近播放")
    crowded_types = ("button_text_wrapped_or_crowded", "text_too_close_to_container_edge")
    if any(issue_type in text for issue_type in crowded_types) and "????" in text:
        return True
    return any(label in text for label in safe_labels) and any(issue_type in text for issue_type in crowded_types)


def _extract_warning_count_from_html(html_content: str) -> int:
    """Extract warning count from a UI-Automation report without affecting pass rate."""
    if not html_content:
        return 0

    warning_items = re.findall(
        r'<div[^>]*class=["\'][^"\']*\bai-issue-item\b[^"\']*\bwarning\b[^"\']*["\'][^>]*>(.*?)</div>',
        html_content,
        re.DOTALL | re.IGNORECASE,
    )
    if warning_items:
        total = 0
        for item in warning_items:
            raw_text = _strip_html(item)
            cleaned = _clean_issue_message(raw_text)
            if not _is_ignored_ui_issue_text(raw_text) and not _is_ignored_ui_issue_text(cleaned):
                total += 1
        return total

    label_match = re.search(
        r'<div[^>]*class=["\']label["\'][^>]*>\s*警告数\s*</div>\s*'
        r'<div[^>]*class=["\'][^"\']*\bvalue\b[^"\']*\bwarning\b[^"\']*["\'][^>]*>\s*(\d+)\s*</div>',
        html_content,
        re.DOTALL | re.IGNORECASE,
    )
    if label_match:
        return int(label_match.group(1))

    summary_match = re.search(r'警告[^0-9]{0,8}(\d+)\s*个', html_content)
    return int(summary_match.group(1)) if summary_match else 0


def _count_ui_issue_warnings(ui_issues: List[Dict[str, object]]) -> int:
    total = 0
    for issue in ui_issues or []:
        for message in issue.get("messages") or []:
            if isinstance(message, dict) and message.get("severity") == "warning":
                total += 1
    return total


def _resolve_testcase_asset_path(src: str, report_dir: str) -> str:
    if not src:
        return ""
    src = html_lib.unescape(src).replace("/", os.sep)
    if re.match(r'^[a-zA-Z]:\\', src) and os.path.exists(src):
        return src
    if report_dir:
        candidate = os.path.abspath(os.path.join(report_dir, src))
        if os.path.exists(candidate):
            return candidate
    candidate = os.path.abspath(os.path.join(TESTCASE_PROJECT_DIR, "reports", src))
    if os.path.exists(candidate):
        return candidate
    return ""


def _image_to_data_uri(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    suffix = Path(path).suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    try:
        with open(path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except Exception:
        return ""


def _extract_test_screenshots_from_html(html_content: str, testcase_report_path: str = "") -> List[Dict[str, object]]:
    """Extract normal execution screenshots and playback probe screenshots."""
    if not html_content:
        return []

    report_dir = os.path.dirname(os.path.abspath(testcase_report_path)) if testcase_report_path else ""
    screenshots: List[Dict[str, object]] = []
    seen = set()

    for block in _module_blocks_from_html(html_content):
        tab_name = _extract_module_tab_name(block)

        for shot_match in re.finditer(
            r'<div[^>]*class=["\'][^"\']*\bai-screenshot\b[^"\']*["\'][^>]*>(.*?)</div>',
            block,
            re.DOTALL | re.IGNORECASE,
        ):
            shot_html = shot_match.group(1)
            label_match = re.search(r'<strong[^>]*>(.*?)</strong>', shot_html, re.DOTALL | re.IGNORECASE)
            label = _strip_html(label_match.group(1)).rstrip(":：") if label_match else "执行后截图"
            label_lower = label.lower()
            if "问题" in label or "issue" in label_lower or "闂" in label:
                continue

            img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', shot_html, re.IGNORECASE)
            if not img_match:
                continue
            src = html_lib.unescape(img_match.group(1))
            path = _resolve_testcase_asset_path(src, report_dir)
            data_uri = _image_to_data_uri(path) if path else ""
            key = ("shot", path or src)
            if not data_uri or key in seen:
                continue
            seen.add(key)
            screenshots.append({
                "type": "screenshot",
                "tab_name": tab_name,
                "label": label or "执行后截图",
                "screenshot_path": path,
                "screenshot_data_uri": data_uri,
            })

        for gallery_start, _gallery_end, gallery_html in _iter_divs_by_class(block, "probe-gallery"):
            previous_html = block[:gallery_start]
            pre_matches = re.findall(
                r'<div[^>]*class=["\'][^"\']*\bai-pre\b[^"\']*["\'][^>]*>(.*?)</div>',
                previous_html,
                re.DOTALL | re.IGNORECASE,
            )
            summary = _strip_html(pre_matches[-1]) if pre_matches else ""
            images = []
            for _image_start, _image_end, image_html in _iter_divs_by_class(gallery_html, "probe-image"):
                title_match = re.search(r'<strong[^>]*>(.*?)</strong>', image_html, re.DOTALL | re.IGNORECASE)
                title = _strip_html(title_match.group(1)) if title_match else "播放检测截图"
                img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', image_html, re.IGNORECASE)
                if not img_match:
                    continue
                src = html_lib.unescape(img_match.group(1))
                path = _resolve_testcase_asset_path(src, report_dir)
                data_uri = _image_to_data_uri(path) if path else ""
                key = ("probe", path or src)
                if not data_uri or key in seen:
                    continue
                seen.add(key)
                images.append({
                    "label": title,
                    "screenshot_path": path,
                    "screenshot_data_uri": data_uri,
                })
            if images:
                screenshots.append({
                    "type": "playback",
                    "tab_name": tab_name,
                    "label": "播放检测截图",
                    "summary": summary,
                    "images": images,
                })

    return screenshots


def _build_ui_issue_html(ui_issues: List[Dict[str, object]]) -> str:
    if not ui_issues:
        return ""
    sections = []
    for issue in ui_issues:
        tab_name = html_lib.escape(str(issue.get("tab_name") or "未知页面"))
        messages = issue.get("messages") or []
        rows = []
        for message in messages[:8]:
            if isinstance(message, dict):
                severity = str(message.get("severity") or "warning")
                severity_label = {"failed": "问题", "warning": "警告", "info": "提示"}.get(severity, "提示")
                text = str(message.get("text") or "")
            else:
                severity = "warning"
                severity_label = "警告"
                text = str(message)
            rows.append(
                f'<li><span class="ui-issue-badge {html_lib.escape(severity)}">'
                f'{html_lib.escape(severity_label)}</span>'
                f'<span>{html_lib.escape(text)}</span></li>'
            )
        rows_html = "".join(rows)
        image = ""
        if issue.get("screenshot_data_uri"):
            image = (
                '<div class="ui-issue-shot">'
                f'<img src="{issue["screenshot_data_uri"]}" alt="UI 问题截图">'
                '</div>'
            )
        sections.append(f'''
            <div class="ui-issue-card">
                <div class="ui-issue-title">TAB / 页面：{tab_name}</div>
                <ul>{rows_html}</ul>
                {image}
            </div>
        ''')
    return f'''<div class="card">
        <h2>UI 视觉检查</h2>
        <div class="ui-issue-grid">{"".join(sections)}</div>
    </div>'''


def _build_test_screenshot_html(screenshots: List[Dict[str, object]]) -> str:
    if not screenshots:
        return ""
    sections = []
    for item in screenshots:
        tab_name = html_lib.escape(str(item.get("tab_name") or "未知页面"))
        label = html_lib.escape(str(item.get("label") or "测试截图"))

        if item.get("type") == "playback":
            summary = ""
            if item.get("summary"):
                summary = f'<div class="test-shot-summary">{html_lib.escape(str(item.get("summary")))}</div>'
            image_items = []
            for image in item.get("images") or []:
                image_label = html_lib.escape(str(image.get("label") or "截图"))
                data_uri = str(image.get("screenshot_data_uri") or "")
                path = str(image.get("screenshot_path") or "")
                if not data_uri:
                    continue
                image_items.append(f'''
                    <div class="test-shot-img">
                        <div class="test-shot-img-label">{image_label}</div>
                        <img src="{data_uri}" alt="{image_label}">
                    </div>
                ''')
            if not image_items:
                continue
            sections.append(f'''
                <div class="test-shot-card">
                    <div class="test-shot-title">{tab_name} / {label}</div>
                    {summary}
                    <div class="test-shot-images">{"".join(image_items)}</div>
                </div>
            ''')
            continue

        data_uri = str(item.get("screenshot_data_uri") or "")
        if not data_uri:
            continue
        sections.append(f'''
            <div class="test-shot-card">
                <div class="test-shot-title">{tab_name} / {label}</div>
                <div class="test-shot-single">
                    <img src="{data_uri}" alt="{label}">
                </div>
            </div>
        ''')

    if not sections:
        return ""
    return f'''<div class="card">
        <h2>测试截图</h2>
        <div class="test-shot-grid">{"".join(sections)}</div>
    </div>'''


def _extract_detailed_results_from_logs(logs: List[str]) -> Dict[str, str]:
    """尝试从日志中提取详细结果（备用方案）"""
    import re
    results = {}
    if not logs:
        return results

    # 匹配包含详细描述的行，如:
    # [18:50:19] ✅ 记账 -> bookkeeping : success | 记账操作完成：支出100元
    # 或者独立的详细结果行
    detail_pattern = re.compile(
        r'(?:记账|明细|词库|我的|设置|首页)\s*[:-]\s*(.+?)(?:\||$)'
    )

    for line in logs:
        # 尝试提取 功能名: 详细描述 格式
        if '->' in line and ':' in line:
            # 提取功能名和可能的结果
            match = re.search(r'[✅✓]\s*(\S+?)\s*->', line)
            if match:
                tab_name = match.group(1)
                # 查找这行或后续几行是否有详细描述
                detail_match = detail_pattern.search(line)
                if detail_match:
                    results[tab_name] = detail_match.group(1).strip()

    return results


REPORT_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Inter','Fira Sans',system-ui,-apple-system,sans-serif;background:linear-gradient(135deg,#0f0c29 0%,#1a1a2e 50%,#24243e 100%);background-attachment:fixed;color:#e2e8f0;line-height:1.6;padding:24px;min-height:100vh}
.container{max-width:1000px;margin:0 auto}
/* 顶部横幅 */
.banner{background:linear-gradient(135deg,rgba(99,102,241,0.25) 0%,rgba(139,92,246,0.15) 100%);border-radius:16px;padding:32px 36px;color:#fff;margin-bottom:20px;position:relative;overflow:hidden;border:1px solid rgba(255,255,255,0.08);backdrop-filter:blur(8px)}
.banner::after{content:'';position:absolute;top:-50%;right:-10%;width:300px;height:300px;border-radius:50%;background:rgba(255,255,255,.04)}
.banner h1{font-size:22px;font-weight:700;margin-bottom:4px;letter-spacing:-0.3px}
.banner .sub{color:#a5b4fc;font-size:13px}
/* 信息网格 */
.info-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin:16px 0}
.info-chip{background:rgba(255,255,255,.08);border-radius:10px;padding:10px 16px;border:1px solid rgba(255,255,255,0.06)}
.info-chip .label{font-size:10px;text-transform:uppercase;letter-spacing:.8px;color:#a5b4fc;font-weight:500}
.info-chip .value{font-size:14px;font-weight:600;color:#fff;margin-top:2px;word-break:break-all}
/* KPI 卡片 */
.kpi-row{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px}
.kpi{background:rgba(30,30,50,0.55);border-radius:12px;padding:20px;text-align:center;border:1px solid rgba(255,255,255,0.08);transition:all .2s}
.kpi:hover{background:rgba(255,255,255,0.08);border-color:rgba(255,255,255,0.14);transform:translateY(-1px)}
.kpi .num{font-size:36px;font-weight:700;line-height:1}
.kpi .lbl{font-size:11px;color:#cbd5e1;text-transform:uppercase;letter-spacing:.5px;margin-top:6px;font-weight:500}
.kpi.total .num{color:#818cf8}
.kpi.pass .num{color:#34d399}
.kpi.fail .num{color:#f87171}
.kpi.warn .num{color:#f59e0b}
.kpi.skip .num{color:#94a3b8}
/* 卡片容器 */
.card{background:rgba(30,30,50,0.55);border-radius:12px;padding:24px;margin-bottom:16px;border:1px solid rgba(255,255,255,0.08)}
.card h2{font-size:15px;font-weight:700;color:#f8fafc;margin-bottom:14px;display:flex;align-items:center;gap:8px}
.card h2::before{content:'';width:4px;height:18px;background:linear-gradient(180deg,#6366f1,#a78bfa);border-radius:2px;flex-shrink:0}
/* 表格 */
table{width:100%;border-collapse:separate;border-spacing:0;font-size:13px}
thead th{background:rgba(0,0,0,0.22);padding:10px 14px;text-align:left;font-weight:600;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid rgba(255,255,255,0.08)}
tbody td{padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.06);vertical-align:middle;color:#e2e8f0}
tbody tr{transition:background .15s}
tbody tr:hover{background:rgba(255,255,255,0.04)}
/* 状态标签 */
.tag{display:inline-flex;align-items:center;gap:4px;padding:3px 12px;border-radius:20px;font-size:11px;font-weight:600;border:1px solid transparent}
.tag-success{background:rgba(52,211,153,0.14);color:#6ee7b7;border-color:rgba(52,211,153,0.3)}
.tag-failed{background:rgba(248,113,113,0.14);color:#fca5a5;border-color:rgba(248,113,113,0.3)}
.tag-done{background:rgba(52,211,153,0.14);color:#6ee7b7;border-color:rgba(52,211,153,0.3)}
.tag-partial{background:rgba(251,191,36,0.14);color:#fcd34d;border-color:rgba(251,191,36,0.3)}
.tag-skipped{background:rgba(255,255,255,0.06);color:#94a3b8;border-color:rgba(255,255,255,0.12)}
.tag-cancelled{background:rgba(255,255,255,0.06);color:#94a3b8;border-color:rgba(255,255,255,0.12)}
/* 进度条 */
.progress-bar{height:6px;background:rgba(255,255,255,0.08);border-radius:3px;overflow:hidden;margin-top:8px}
.progress-fill{height:100%;border-radius:3px;transition:width .6s ease}
.progress-fill.green{background:linear-gradient(90deg,#34d399,#6ee7b7)}
.progress-fill.red{background:linear-gradient(90deg,#f87171,#fca5a5)}
/* 日志 */
.log-box{background:#030408;color:#94E2D5;border-radius:10px;padding:16px 20px;font-family:'Fira Code',Consolas,monospace;font-size:12px;line-height:1.7;max-height:320px;overflow-y:auto;white-space:pre-wrap;margin-top:8px;border:1px solid rgba(255,255,255,0.06)}
/* 包头 */
.pkg-bar{display:flex;align-items:center;gap:12px;padding:14px 0;border-bottom:1px solid rgba(255,255,255,0.08)}
.pkg-bar .name{font-size:15px;font-weight:600;color:#f8fafc}
.pkg-bar .meta{font-size:11px;color:#94a3b8}
details summary{cursor:pointer;color:#818cf8;font-size:12px;font-weight:500;padding:6px 0}
details summary:hover{color:#a5b4fc}
/* UI 视觉问题 */
.ui-issue-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.ui-issue-card{background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.22);border-radius:10px;padding:14px}
.ui-issue-title{font-size:13px;font-weight:700;color:#fcd34d;margin-bottom:8px}
.ui-issue-card ul{padding-left:0;margin:0 0 10px 0;list-style:none}
.ui-issue-card li{display:flex;align-items:flex-start;gap:8px;font-size:12px;color:#f8fafc;margin:6px 0}
.ui-issue-badge{flex:0 0 auto;min-width:34px;padding:1px 6px;border-radius:10px;text-align:center;font-size:11px;font-weight:700}
.ui-issue-badge.failed{background:rgba(248,113,113,0.18);color:#fca5a5;border:1px solid rgba(248,113,113,0.32)}
.ui-issue-badge.warning{background:rgba(251,191,36,0.18);color:#fcd34d;border:1px solid rgba(251,191,36,0.32)}
.ui-issue-badge.info{background:rgba(125,211,252,0.16);color:#bae6fd;border:1px solid rgba(125,211,252,0.28)}
.ui-issue-shot{margin-top:10px;max-width:320px}
.ui-issue-shot img{display:block;width:100%;max-height:480px;object-fit:contain;border-radius:8px;border:1px solid rgba(255,255,255,0.12);background:#fff}
/* 页脚 */
.test-shot-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.test-shot-card{background:rgba(59,130,246,0.08);border:1px solid rgba(59,130,246,0.22);border-radius:10px;padding:14px}
.test-shot-title{font-size:13px;font-weight:700;color:#bfdbfe;margin-bottom:8px}
.test-shot-summary{background:rgba(0,0,0,0.2);border-radius:8px;padding:8px 10px;color:#cbd5e1;font-family:'Fira Code',Consolas,monospace;font-size:11px;white-space:pre-wrap;margin-bottom:10px}
.test-shot-single{max-width:360px}
.test-shot-single img,.test-shot-img img{display:block;width:100%;max-height:520px;object-fit:contain;border-radius:8px;border:1px solid rgba(255,255,255,0.12);background:#fff}
.test-shot-images{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.test-shot-img{background:rgba(255,255,255,0.06);border-radius:8px;padding:8px;border:1px solid rgba(255,255,255,0.08)}
.test-shot-img-label{font-size:12px;font-weight:600;color:#e2e8f0;margin-bottom:6px}
.footer{text-align:center;color:#64748b;font-size:11px;margin-top:20px;padding:12px 0}
/* 响应式 */
@media(max-width:768px){body{padding:16px}.kpi-row{grid-template-columns:repeat(2,1fr)}.info-grid{grid-template-columns:1fr}}
@media(max-width:480px){.kpi-row{grid-template-columns:1fr}}
"""


def _status_tag(status):
    """生成状态标签 HTML"""
    icons = {"success": "&#10003;", "done": "&#10003;", "failed": "&#10007;", "skipped": "&#8722;", "partial": "&#9679;", "cancelled": "&#8722;"}
    cls = {"success": "tag-success", "done": "tag-done", "failed": "tag-failed", "partial": "tag-partial"}.get(status, "tag-skipped")
    icon = icons.get(status, "&#8226;")
    return f'<span class="tag {cls}">{icon} {status}</span>'


def _pass_rate(passed, total):
    """计算通过率百分比"""
    if total == 0:
        return 0
    return round(passed / total * 100)


def generate_html_report(task_id: int, pkg, test_result: dict, device_serial: str = "", device_model: str = "", logs: list = None, testcase_report_path: str = None) -> str:
    """生成单任务 HTML 测试报告"""
    import re
    status = test_result.get("status", "unknown")
    steps = test_result.get("steps", [])
    module_results = test_result.get("module_results", {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 尝试从 testcase 生成的报告中提取详细结果
    detailed_results = {}
    ui_issues = []
    test_screenshots = []
    warning_count = 0
    if testcase_report_path and os.path.exists(testcase_report_path):
        try:
            with open(testcase_report_path, "r", encoding="utf-8") as f:
                testcase_html = f.read()
            detailed_results = _extract_detailed_results_from_html(testcase_html)
            ui_issues = _extract_ui_issues_from_html(testcase_html, testcase_report_path)
            test_screenshots = _extract_test_screenshots_from_html(testcase_html, testcase_report_path)
            warning_count = _extract_warning_count_from_html(testcase_html)
            print(f"[Report] 从 testcase 报告提取了 {len(detailed_results)} 条详细结果")
            if ui_issues:
                print(f"[Report] 从 testcase 报告提取了 {len(ui_issues)} 个 UI 问题截图块")
        except Exception as e:
            print(f"[Report] 读取 testcase 报告失败: {e}")
    warning_count = max(warning_count, _count_ui_issue_warnings(ui_issues))

    # 如果 module_results 为空，尝试从日志解析
    if not module_results and logs:
        # 匹配格式: [时间] 符号 功能名 -> 模块名 : 状态
        # 例如: [18:50:19] ✅ 记账 -> bookkeeping : success
        module_pattern = re.compile(r'\[\d{2}:\d{2}:\d{2}\]\s*[✅✓✗✔✕]?\s*(.+?)\s*->\s*(\S+)\s*:\s*(\w+)')
        for line in logs:
            match = module_pattern.search(line)
            if match:
                tab_name = match.group(1).strip()
                module_name = match.group(2).strip()
                status_val = match.group(3).strip()
                if tab_name and module_name and status_val in ('success', 'failed', 'skipped'):
                    module_results[tab_name] = {
                        "module": module_name,
                        "status": status_val,
                        "message": ""
                    }
        # 调试输出
        print(f"[Report] 日志行数: {len(logs)}, 解析到 {len(module_results)} 个功能模块: {list(module_results.keys())}")

    total = len(steps)
    passed = sum(1 for s in steps if s.get("status") == "success")
    failed = sum(1 for s in steps if s.get("status") == "failed")
    skipped = total - passed - failed
    rate = _pass_rate(passed, total)

    # 功能测试结果 - 使用详细描述
    module_html = ""
    if module_results:
        if logs:
            apply_failure_details_to_modules(module_results, logs)
        rows = ""
        for tab_name, mr in module_results.items():
            ms = mr.get("status", "unknown")
            # 失败场景优先展示平台日志提取的具体失败原因；通过场景使用 testcase 报告描述。
            detail_msg = mr.get("message", "") if ms in ("failed", "skipped") else detailed_results.get(tab_name, mr.get("message", ""))
            if not detail_msg:
                detail_msg = detailed_results.get(tab_name, "")
            # 如果没有详细描述但有 module 名，显示 module 名
            if not detail_msg:
                detail_msg = mr.get("module", "")
            rows += (
                f'<tr><td style="font-weight:600">{html_lib.escape(str(tab_name))}</td>'
                f'<td>{_status_tag(ms)}</td>'
                f'<td style="color:#cbd5e1;font-size:12px">{html_lib.escape(str(detail_msg))}</td></tr>'
            )
        module_html = f'''<div class="card">
            <h2>功能测试结果</h2>
            <table><thead><tr><th>功能 (TAB)</th><th>状态</th><th>结果详情</th></tr></thead>
            <tbody>{rows}</tbody></table>
        </div>'''

    # 执行步骤
    steps_html = ""
    if steps:
        rows = ""
        for i, step in enumerate(steps, 1):
            s = step.get("status", "unknown")
            detail = step.get("detail", step.get("error", ""))
            rows += f'<tr><td style="color:#94a3b8">{i}</td><td style="font-weight:500">{step["name"]}</td><td>{_status_tag(s)}</td><td style="color:#cbd5e1;font-size:12px">{detail}</td></tr>'
        steps_html = f'''<div class="card">
            <h2>执行步骤</h2>
            <table><thead><tr><th>#</th><th>步骤</th><th>结果</th><th>详情</th></tr></thead>
            <tbody>{rows}</tbody></table>
        </div>'''

    device_chip = f'<div class="info-chip"><div class="label">测试设备</div><div class="value">{device_model or device_serial}</div></div>' if device_serial else ""
    ui_issue_html = _build_ui_issue_html(ui_issues)
    test_screenshot_html = _build_test_screenshot_html(test_screenshots)

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>测试报告 - {pkg.package_name}</title>
<style>{REPORT_CSS}</style></head><body>
<div class="container">
    <div class="banner">
        <h1>自动化测试报告</h1>
        <div class="sub">生成时间: {now} &middot; 任务 #{task_id}</div>
        <div class="info-grid">
            <div class="info-chip"><div class="label">包名</div><div class="value">{pkg.package_name}</div></div>
            <div class="info-chip"><div class="label">文件</div><div class="value">{pkg.filename}</div></div>
            <div class="info-chip"><div class="label">类型</div><div class="value">{pkg.file_type.upper()}</div></div>
            {device_chip}
            <div class="info-chip"><div class="label">状态</div><div class="value">{status.upper()}</div></div>
        </div>
    </div>
    <div class="kpi-row">
        <div class="kpi total"><div class="num">{total}</div><div class="lbl">总步骤</div></div>
        <div class="kpi pass"><div class="num">{passed}</div><div class="lbl">通过</div></div>
        <div class="kpi fail"><div class="num">{failed}</div><div class="lbl">失败</div></div>
        <div class="kpi warn"><div class="num">{warning_count}</div><div class="lbl">警告</div></div>
        <div class="kpi"><div class="num" style="color:#F59E0B">{rate}%</div><div class="lbl">通过率</div>
            <div class="progress-bar"><div class="progress-fill {'green' if rate>=80 else 'red'}" style="width:{rate}%"></div></div>
        </div>
    </div>
    {ui_issue_html}
    {test_screenshot_html}
    {module_html}
    {steps_html}
    <div class="footer">自动化测试平台 &middot; 报告自动生成</div>
</div></body></html>"""

    filename = f"report_{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    filepath = os.path.join(REPORT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    return filepath


def generate_batch_report(batch_id: str, pkg_results: List[Dict]) -> str:
    """生成批量任务汇总报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_pkgs = len(pkg_results)
    done_pkgs = sum(1 for r in pkg_results if r["status"] == "done")
    failed_pkgs = sum(1 for r in pkg_results if r["status"] == "failed")
    other_pkgs = total_pkgs - done_pkgs - failed_pkgs
    rate = _pass_rate(done_pkgs, total_pkgs)
    package_warning_counts = {}
    total_warnings = 0
    for r in pkg_results:
        testcase_report = os.path.join(TESTCASE_PROJECT_DIR, "reports", f"report_task_{r['task_id']}.html")
        warning_count = int(r.get("warning_count") or 0)
        if os.path.exists(testcase_report):
            try:
                with open(testcase_report, "r", encoding="utf-8") as f:
                    warning_count = max(warning_count, _extract_warning_count_from_html(f.read()))
            except Exception:
                pass
        package_warning_counts[r["task_id"]] = warning_count
        total_warnings += warning_count

    # 汇总表
    summary_rows = ""
    for r in pkg_results:
        s = r["status"]
        duration = ""
        if r.get("started_at") and r.get("finished_at"):
            try:
                t1 = datetime.fromisoformat(r["started_at"])
                t2 = datetime.fromisoformat(r["finished_at"])
                dur = (t2 - t1).total_seconds()
                duration = f"{dur:.0f}s"
            except Exception:
                pass
        warning_count = package_warning_counts.get(r["task_id"], 0)
        error_html = f'<td style="color:#f87171;font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{r["error"][:100]}</td>' if r.get("error") else "<td></td>"
        summary_rows += f'''<tr>
            <td style="color:#94a3b8">#{r["task_id"]}</td>
            <td><div style="font-weight:600">{r["package_name"]}</div><div style="font-size:11px;color:#94a3b8">{r["filename"]}</div></td>
            <td>{_status_tag(s)}</td>
            <td style="color:#cbd5e1">{duration}</td>
            <td style="color:#fcd34d;font-weight:600">{warning_count}</td>
            {error_html}
        </tr>'''

    # 各包详情 - 去掉"查看执行日志"功能
    detail_sections = ""
    for r in pkg_results:
        s = r["status"]

        # 尝试读取 testcase 报告获取详细结果
        detailed_features = {}
        ui_issues = []
        test_screenshots = []
        warning_count = package_warning_counts.get(r["task_id"], 0)
        testcase_report = os.path.join(TESTCASE_PROJECT_DIR, "reports", f"report_task_{r['task_id']}.html")
        if os.path.exists(testcase_report):
            try:
                with open(testcase_report, "r", encoding="utf-8") as f:
                    testcase_html = f.read()
                detailed_features = _extract_detailed_results_from_html(testcase_html)
                ui_issues = _extract_ui_issues_from_html(testcase_html, testcase_report)
                test_screenshots = _extract_test_screenshots_from_html(testcase_html, testcase_report)
                warning_count = max(warning_count, _extract_warning_count_from_html(testcase_html))
            except Exception:
                pass
        warning_count = max(warning_count, _count_ui_issue_warnings(ui_issues))

        # 如果没有详细结果，从日志提取简单的功能行
        logs = r.get("logs", [])
        if not detailed_features and logs:
            feature_lines = [l for l in logs if any(k in l for k in ["PASS", "FAIL", "SKIP", "-> ", "success", "failed", "skipped"])]
            for line in feature_lines[-10:]:
                # 尝试解析 功能名 -> 模块名 : 状态
                match = re.search(r'[✅✓✗✔✕]?\s*(\S+?)\s*->\s*(\S+)', line)
                if match:
                    tab_name = match.group(1)
                    detailed_features[tab_name] = line.split("] ", 1)[-1] if "] " in line else line

        # 功能测试结果列表
        features_html = ""
        if detailed_features:
            items = ""
            for tab_name, detail in detailed_features.items():
                # 清理显示文本
                clean_detail = detail
                if "->" in clean_detail:
                    clean_detail = clean_detail.split("->", 1)[0].strip()
                    clean_detail = re.sub(r'^[✅✓✗✔✕\s]+', '', clean_detail)
                items += f'<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,0.08);font-size:12px"><span style="font-weight:500;color:#f8fafc">{tab_name}</span><span style="color:#cbd5e1">{detail}</span></div>'
            features_html = f'<div style="margin:12px 0;padding:12px;background:rgba(0,0,0,0.22);border-radius:8px;border:1px solid rgba(255,255,255,0.06)">{items}</div>'

        ui_issue_html = _build_ui_issue_html(ui_issues)
        test_screenshot_html = _build_test_screenshot_html(test_screenshots)

        detail_sections += f'''<div class="card">
            <div class="pkg-bar">
                {_status_tag(s)}
                <div><div class="name">{r["package_name"]}</div><div class="meta">{r["filename"]} &middot; 任务 #{r["task_id"]} &middot; 警告 {warning_count}</div></div>
            </div>
            {ui_issue_html}
            {test_screenshot_html}
            {features_html}
        </div>'''

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>批量测试报告</title>
<style>{REPORT_CSS}</style></head><body>
<div class="container">
    <div class="banner">
        <h1>批量自动化测试报告</h1>
        <div class="sub">生成时间: {now} &middot; 批次: {batch_id} &middot; 共 {total_pkgs} 个包</div>
    </div>
    <div class="kpi-row">
        <div class="kpi total"><div class="num">{total_pkgs}</div><div class="lbl">总包数</div></div>
        <div class="kpi pass"><div class="num">{done_pkgs}</div><div class="lbl">通过</div></div>
        <div class="kpi fail"><div class="num">{failed_pkgs}</div><div class="lbl">失败</div></div>
        <div class="kpi warn"><div class="num">{total_warnings}</div><div class="lbl">警告</div></div>
        <div class="kpi"><div class="num" style="color:#F59E0B">{rate}%</div><div class="lbl">通过率</div>
            <div class="progress-bar"><div class="progress-fill {'green' if rate>=80 else 'red'}" style="width:{rate}%"></div></div>
        </div>
    </div>
    <div class="card">
        <h2>测试汇总</h2>
        <table><thead><tr><th>任务</th><th>包</th><th>状态</th><th>耗时</th><th>警告</th><th>错误</th></tr></thead>
        <tbody>{summary_rows}</tbody></table>
    </div>
    {detail_sections}
    <div class="footer">自动化测试平台 &middot; 批量报告自动生成</div>
</div></body></html>"""

    filename = f"batch_{batch_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    filepath = os.path.join(REPORT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    return filepath
