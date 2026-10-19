"""工作台 UI 动态内容转义：JS 单元 + 真实浏览器（可用时）。"""

from __future__ import annotations

import re

import pytest

from workbench.ui import INDEX_HTML


def _render_functions() -> str:
    start = INDEX_HTML.index("function renderTaskRow")
    end = INDEX_HTML.index("async function loadTasks")
    render = INDEX_HTML[start:end]
    escape_start = INDEX_HTML.index("function escapeHtml")
    escape_end = INDEX_HTML.index("</script>", escape_start)
    return render + "\n" + INDEX_HTML[escape_start:escape_end]


MALICIOUS = (
    "{task_id:'<img src=x onerror=alert(1)>',status:'<script>alert(2)</script>',"
    "model_calls:1,result:{conclusion:'<script>alert(3)</script>'}}"
)


def test_render_task_row_escapes_model_output() -> None:
    quickjs = pytest.importorskip("quickjs")
    context = quickjs.Context()
    context.eval(_render_functions())
    html = context.eval(f"renderTaskRow({MALICIOUS})")
    assert isinstance(html, str)
    assert "<img" not in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html


def test_detail_render_escapes_citation_and_issue() -> None:
    # 详情渲染代码必须对引用与审核描述调用 escapeHtml。
    assert "escapeHtml(e.citation" in INDEX_HTML
    assert "escapeHtml(i.description" in INDEX_HTML
    assert "escapeHtml(t.task_id)" in INDEX_HTML


def test_ui_has_no_inline_dynamic_onclick() -> None:
    assert not re.search(r"onclick=\"(showTask|decide)\('", INDEX_HTML)


def test_malicious_html_is_text_in_real_browser() -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    # 只有浏览器/运行环境不可用时才 skip；后续断言失败必须让测试失败。
    try:
        manager = playwright.sync_playwright().start()
        browser = manager.chromium.launch(channel="msedge", headless=True)
    except Exception as exc:
        pytest.skip(f"browser unavailable: {exc}")
    try:
        page = browser.new_page()
        page.set_content(INDEX_HTML)
        page.evaluate(
            "document.getElementById('tasklist').innerHTML = '<table>' + renderTaskRow(" + MALICIOUS + ") + '</table>'"
        )
        assert page.locator("#tasklist img").count() == 0
        assert page.locator("#tasklist script").count() == 0
        assert page.locator("#tasklist").inner_text().count("<script>") >= 1
    finally:
        browser.close()
        manager.stop()
