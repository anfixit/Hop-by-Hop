"""Markdown урока -> узлы Telegraph (https://telegra.ph/api#Node) и клиент API.

Telegraph понимает ограниченный набор тегов: заголовки только h3/h4, таблиц нет.
Поэтому h1/h2 превращаются в h3, h3+ в h4, а таблицы - в список карточек.
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
        # alt нужен только до сборки figure/figcaption, в Telegraph он не уходит
        keep = {k: v for k, v in attrs if k in ("href", "src", "alt") and v}
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
                self._children().append(_table_as_list(self.table))
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


def _table_as_list(rows: list[list[str]]) -> dict:
    """Таблица -> список карточек: моноширинные таблицы не читаются на телефоне.

    Каждая строка становится пунктом: первая ячейка жирным, остальные - "Заголовок: значение".
    """
    rows = [r for r in rows if r]
    header, body = (rows[0], rows[1:]) if len(rows) > 1 else ([], rows)
    items = []
    for row in body:
        children: list = [{"tag": "strong", "children": [row[0]]}] if row and row[0] else []
        for i, cell in enumerate(row[1:], start=1):
            if not cell:
                continue
            label = header[i] if i < len(header) and header[i] else ""
            children += [{"tag": "br"}, f"{label}: {cell}" if label else cell]
        items.append({"tag": "li", "children": children})
    return {"tag": "ul", "children": items}


def _figures(nodes: list, resolve_src) -> list:
    """Абзац из одной картинки -> figure с подписью из alt; src проходит через resolve_src."""
    result = []
    for node in nodes:
        if isinstance(node, dict) and node.get("tag") == "img":
            attrs = node.setdefault("attrs", {})
            attrs["src"] = resolve_src(attrs.get("src", ""))
            attrs.pop("alt", None)
        if isinstance(node, dict) and node.get("children"):
            kids = [c for c in node["children"] if not (isinstance(c, str) and not c.strip())]
            if node["tag"] == "p" and len(kids) == 1 and isinstance(kids[0], dict) and kids[0]["tag"] == "img":
                alt = kids[0].get("attrs", {}).get("alt", "")
                figure = {"tag": "figure", "children": _figures(kids, resolve_src)}
                if alt:
                    figure["children"].append({"tag": "figcaption", "children": [alt]})
                result.append(figure)
                continue
            node["children"] = _figures(node["children"], resolve_src)
        result.append(node)
    return result


def markdown_to_nodes(text: str, resolve_src=lambda src: src) -> list:
    """resolve_src превращает относительный путь картинки в абсолютный URL."""
    html = markdown.markdown(text, extensions=["fenced_code", "tables", "sane_lists"])
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return _figures(builder.root, resolve_src)


def image_sources(text: str) -> list[str]:
    """Все src картинок урока (для проверки, что файлы существуют)."""
    found = []

    def walk(nodes):
        for n in nodes:
            if isinstance(n, dict):
                if n.get("tag") == "img":
                    found.append(n["attrs"]["src"])
                walk(n.get("children", []))
    walk(markdown_to_nodes(text))
    return found


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
