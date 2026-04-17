"""报告生成服务 - 支持单任务报告和批量汇总报告
设计风格：Data-Dense Dashboard（基于 UI/UX Pro Max 设计系统）
配色：#1E40AF / #3B82F6 / #F59E0B / #F8FAFC
"""
import os
import re
from datetime import datetime
from typing import List, Dict
from app.config import REPORT_DIR, TESTCASE_PROJECT_DIR

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

    return results


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
.kpi-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
.kpi{background:rgba(30,30,50,0.55);border-radius:12px;padding:20px;text-align:center;border:1px solid rgba(255,255,255,0.08);transition:all .2s}
.kpi:hover{background:rgba(255,255,255,0.08);border-color:rgba(255,255,255,0.14);transform:translateY(-1px)}
.kpi .num{font-size:36px;font-weight:700;line-height:1}
.kpi .lbl{font-size:11px;color:#cbd5e1;text-transform:uppercase;letter-spacing:.5px;margin-top:6px;font-weight:500}
.kpi.total .num{color:#818cf8}
.kpi.pass .num{color:#34d399}
.kpi.fail .num{color:#f87171}
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
/* 页脚 */
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
    if testcase_report_path and os.path.exists(testcase_report_path):
        try:
            with open(testcase_report_path, "r", encoding="utf-8") as f:
                testcase_html = f.read()
            detailed_results = _extract_detailed_results_from_html(testcase_html)
            print(f"[Report] 从 testcase 报告提取了 {len(detailed_results)} 条详细结果")
        except Exception as e:
            print(f"[Report] 读取 testcase 报告失败: {e}")

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
        rows = ""
        for tab_name, mr in module_results.items():
            ms = mr.get("status", "unknown")
            # 优先使用从 testcase 报告提取的详细描述
            detail_msg = detailed_results.get(tab_name, mr.get("message", ""))
            # 如果没有详细描述但有 module 名，显示 module 名
            if not detail_msg:
                detail_msg = mr.get("module", "")
            rows += f'<tr><td style="font-weight:600">{tab_name}</td><td>{_status_tag(ms)}</td><td style="color:#cbd5e1;font-size:12px">{detail_msg}</td></tr>'
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
        <div class="kpi"><div class="num" style="color:#F59E0B">{rate}%</div><div class="lbl">通过率</div>
            <div class="progress-bar"><div class="progress-fill {'green' if rate>=80 else 'red'}" style="width:{rate}%"></div></div>
        </div>
    </div>
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
        error_html = f'<td style="color:#f87171;font-size:12px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{r["error"][:100]}</td>' if r.get("error") else "<td></td>"
        summary_rows += f'''<tr>
            <td style="color:#94a3b8">#{r["task_id"]}</td>
            <td><div style="font-weight:600">{r["package_name"]}</div><div style="font-size:11px;color:#94a3b8">{r["filename"]}</div></td>
            <td>{_status_tag(s)}</td>
            <td style="color:#cbd5e1">{duration}</td>
            {error_html}
        </tr>'''

    # 各包详情 - 去掉"查看执行日志"功能
    detail_sections = ""
    for r in pkg_results:
        s = r["status"]

        # 尝试读取 testcase 报告获取详细结果
        detailed_features = {}
        testcase_report = os.path.join(TESTCASE_PROJECT_DIR, "reports", f"report_task_{r['task_id']}.html")
        if os.path.exists(testcase_report):
            try:
                with open(testcase_report, "r", encoding="utf-8") as f:
                    testcase_html = f.read()
                detailed_features = _extract_detailed_results_from_html(testcase_html)
            except Exception:
                pass

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

        detail_sections += f'''<div class="card">
            <div class="pkg-bar">
                {_status_tag(s)}
                <div><div class="name">{r["package_name"]}</div><div class="meta">{r["filename"]} &middot; 任务 #{r["task_id"]}</div></div>
            </div>
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
        <div class="kpi"><div class="num" style="color:#F59E0B">{rate}%</div><div class="lbl">通过率</div>
            <div class="progress-bar"><div class="progress-fill {'green' if rate>=80 else 'red'}" style="width:{rate}%"></div></div>
        </div>
    </div>
    <div class="card">
        <h2>测试汇总</h2>
        <table><thead><tr><th>任务</th><th>包</th><th>状态</th><th>耗时</th><th>错误</th></tr></thead>
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
