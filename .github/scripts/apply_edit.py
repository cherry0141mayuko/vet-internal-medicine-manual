#!/usr/bin/env python3
"""承認されたIssueの内容をindex.htmlに反映するスクリプト。

安全のため、Issueに書かれた編集後HTMLは反映前に必ずサニタイズする
(script/style/iframe等の除去、イベントハンドラ属性の除去、javascript: 等の
危険なURLスキームの除去)。許可した要素・属性以外はすべて取り除く。

GitHub Actions(approve-edit.yml)から呼ばれる。
"""
import os
import re
import json
import sys
import datetime

import bleach

# --- サニタイズ設定 -----------------------------------------------------
# マニュアル本文(build_site.pyが生成するHTML)で実際に使われるタグ・属性のみ許可する。
ALLOWED_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "strong", "em", "b", "i", "u", "code", "pre", "blockquote",
    "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td",
    "a", "img", "figure", "figcaption",
    "div", "span",
    # ダイアグラム用の SVG 要素
    "svg", "defs", "linearGradient", "stop", "marker", "path", "rect", "ellipse",
    "line", "text", "tspan", "g", "circle", "polygon", "polyline",
]

ALLOWED_ATTRS = {
    "*": ["class", "id", "style"],
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "svg": ["viewbox", "viewBox", "role", "aria-label", "xmlns", "width", "height", "style"],
    "defs": [],
    "lineargradient": ["id", "x1", "y1", "x2", "y2"],
    "linearGradient": ["id", "x1", "y1", "x2", "y2"],
    "stop": ["offset", "stop-color"],
    "marker": ["id", "markerwidth", "markerheight", "markerWidth", "markerHeight",
               "refx", "refy", "refX", "refY", "orient"],
    "path": ["d", "fill", "stroke", "stroke-width", "stroke-linecap", "marker-end"],
    "rect": ["x", "y", "width", "height", "rx", "ry", "fill", "stroke", "stroke-width"],
    "ellipse": ["cx", "cy", "rx", "ry", "fill", "stroke", "stroke-width"],
    "circle": ["cx", "cy", "r", "fill", "stroke", "stroke-width"],
    "line": ["x1", "y1", "x2", "y2", "stroke", "stroke-width", "stroke-linecap", "marker-end"],
    "polygon": ["points", "fill", "stroke", "stroke-width"],
    "polyline": ["points", "fill", "stroke", "stroke-width"],
    "text": ["x", "y", "font-size", "font-weight", "fill", "text-anchor", "dominant-baseline"],
    "tspan": ["x", "y"],
    "g": ["stroke", "stroke-width", "stroke-linecap", "fill"],
    "table": ["class"],
}

ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def sanitize_html(raw_html: str) -> str:
    # <script>/<style> はタグごと中身も含めて先に除去(bleachのstrip=Trueはタグのみ除去し
    # 内部テキストは残すため、scriptの中身が地の文として残るのを防ぐ)。
    raw_html = re.sub(r'<script\b[^>]*>.*?</script>', '', raw_html, flags=re.IGNORECASE | re.DOTALL)
    raw_html = re.sub(r'<style\b[^>]*>.*?</style>', '', raw_html, flags=re.IGNORECASE | re.DOTALL)

    cleaned = bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    # img の src は http(s) または data:image/* のみ許可(data:text/html 等は不可)。
    def _img_src_guard(m):
        src = m.group(1)
        if src.startswith("data:image/") or re.match(r'^https?://', src) or not re.match(r'^[a-zA-Z]+:', src):
            return m.group(0)
        return ""  # 危険なスキームは丸ごと除去(壊れた img として無害化)
    cleaned = re.sub(r'<img\b[^>]*\bsrc="([^"]*)"[^>]*>', _img_src_guard, cleaned)

    return cleaned


def main() -> int:
    body = os.environ.get("ISSUE_BODY") or ""

    m_id = re.search(r'^manual_id:\s*(\S+)\s*$', body, re.MULTILINE)
    if not m_id:
        print("manual_id が見つかりません")
        return 1
    manual_id = m_id.group(1)
    if not re.match(r'^[a-z0-9\-]+$', manual_id):
        print(f"manual_id の形式が不正です: {manual_id!r}")
        return 1

    m_html = re.search(r'```html\r?\n(.*?)\r?\n```', body, re.DOTALL)
    if not m_html:
        print("編集後のHTML(```html ... ```)が見つかりません")
        return 1
    raw_html = m_html.group(1)
    new_html = sanitize_html(raw_html)

    with open("index.html", encoding="utf-8") as f:
        content = f.read()

    m_data = re.search(r'window\.MANUALS = (\[.*?\]);\n', content, re.DOTALL)
    if not m_data:
        print("window.MANUALS が index.html 内に見つかりません")
        return 1

    manuals = json.loads(m_data.group(1))
    found = False
    for man in manuals:
        if man.get("id") == manual_id:
            man["html"] = new_html
            text_only = re.sub(r'<[^>]+>', ' ', new_html)
            text_only = re.sub(r'\s+', ' ', text_only).strip()
            tags = " ".join(str(t) for t in man.get("tags", []))
            man["text"] = f"{man.get('title', '')} {tags} {text_only}"
            man["updated"] = datetime.date.today().isoformat()
            found = True
            break

    if not found:
        print(f"manual_id '{manual_id}' が見つかりません")
        return 1

    new_manuals_json = json.dumps(manuals, ensure_ascii=False, default=str)
    new_content = content[:m_data.start(1)] + new_manuals_json + content[m_data.end(1):]

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"更新しました: {manual_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
