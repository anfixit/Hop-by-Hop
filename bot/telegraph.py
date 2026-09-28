"""Markdown урока -> узлы Telegraph (https://telegra.ph/api#Node) и клиент API.

Telegraph понимает ограниченный набор тегов: заголовки только h3/h4, таблиц нет.
Поэтому h1/h2 превращаются в h3, h3+ в h4, а таблицы - в моноширинный текст.
"""
import json
import urllib.parse
import urllib.request
from html.parser import HTMLParser

import markdown

API = "https://api.telegra.ph"
ALLOWED = {"a", "aside", "b", "blockquote", "br", "code", "em", "figcaption", "figure", "h3", "h4", "hr", "i",
           "iframe", "img", "li", "ol", "p", "pre", "s", "strong", "u", "ul", "video"}
RENAME = {"h1": "h3", "h2": "h3", "h5": "h4", "h6": "h4", "del": "s"}
VOID = {"br", "hr", "img"}
MAX_CONTENT_BYTES = 64 * 1024


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root: list = []
        self.stack: list[dict] = []
        self.table: list[list[str]] | None = None
        self.cell: list[str] | None = None

    def _children(self) -> list:
        return self.stack[-1]["children"] if self.stack else self.root

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.table = []
            return
        if self.table is not None:
            if tag == "tr":
                self.table.append([])
            elif tag in ("td", "th"):
                self.cell = []
            return
        tag = RENAME.get(tag, tag)
        if tag not in ALLOWED:
            return  # тег разворачивается: дети попадут к родителю
        node: dict = {"tag": tag}
        keep = {k: v for k, v in attrs if k in ("href", "src") and v}
        if keep:
            node["attrs"] = keep
        self._children().append(node)
        if tag not in VOID:
            node["children"] = []
            self.stack.append(node)

    def handle_endtag(self, tag):
        if self.table is not None:
            if tag in ("td", "th") and self.cell is not None:
                self.table[-1].append("".join(self.cell).strip())
                self.cell = None
            elif tag == "table":
                self._children().append({"tag": "pre", "children": [_render_table(self.table)]})
                self.table = None
            return
        tag = RENAME.get(tag, tag)
        if tag in ALLOWED and tag not in VOID:
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i]["tag"] == tag:
                    del self.stack[i:]
                    break

    def handle_data(self, data):
        if self.table is not None:
            if self.cell is not None:
                self.cell.append(data)
            return
        if not data.strip() and not self.stack:
            return  # переводы строк между блоками
        self._children().append(data)


def _render_table(rows: list[list[str]]) -> str:
    rows = [r for r in rows if r]
    if not rows:
        return ""
    widths = [max(len(r[i]) if i < len(r) else 0 for r in rows) for i in range(max(map(len, rows)))]
    lines = [" | ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]
    lines.insert(1, "-+-".join("-" * w for w in widths))
    return "\n".join(lines)


def markdown_to_nodes(text: str) -> list:
    html = markdown.markdown(text, extensions=["fenced_code", "tables", "sane_lists"])
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return builder.root


def split_title(text: str) -> tuple[str, str]:
    """Первая строка вида '# Заголовок' становится заголовком страницы."""
    first, _, rest = text.lstrip("\ufeff \n").partition("\n")
    if first.startswith("# "):
        return first[2:].strip(), rest
    raise ValueError("lesson.md должен начинаться со строки '# Заголовок'")


def call(method: str, proxy_url: str | None = None, **params) -> dict:
    data = urllib.parse.urlencode(
        {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in params.items()}
    ).encode()
    handlers = [urllib.request.ProxyHandler({"https": proxy_url})] if proxy_url else []
    opener = urllib.request.build_opener(*handlers)
    with opener.open(urllib.request.Request(f"{API}/{method}", data=data), timeout=30) as resp:
        body = json.loads(resp.read())
    if not body.get("ok"):
        raise RuntimeError(f"Telegraph {method}: {body.get('error')}")
    return body["result"]
