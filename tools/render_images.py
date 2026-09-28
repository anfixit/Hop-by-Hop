"""Рендерит иллюстрации уроков: content/lessons/NNN/img/*.svg -> *.png рядом.

PNG коммитятся в git: Telegraph берёт картинки по ссылке на raw.githubusercontent.com.
Ограничения рендера MuPDF: без градиентов и stroke-dasharray; кириллица работает.

  python -m tools.render_images          все уроки
  python -m tools.render_images 1        только урок 1
"""
import sys
import xml.etree.ElementTree as ET

import pymupdf

from bot.content import LESSONS_DIR

WIDTH = 1200  # px; Telegraph показывает картинки по ширине колонки
SVG_NS = "http://www.w3.org/2000/svg"
# MuPDF не наследует эти атрибуты текста от <g>, поэтому раскладываем их по <text> сами
INHERITED = ("font-family", "font-size", "font-weight", "text-anchor", "fill")
DEFAULT_FONT = "sans-serif"


def _inline_text_attrs(node: ET.Element, inherited: dict[str, str]) -> None:
    own = {k: v for k, v in node.attrib.items() if k in INHERITED}
    scope = inherited | own
    if node.tag == f"{{{SVG_NS}}}text":
        for key, value in scope.items():
            node.attrib.setdefault(key, value)
        node.attrib.setdefault("font-family", DEFAULT_FONT)
    for child in node:
        _inline_text_attrs(child, scope)


def prepared_svg(path) -> bytes:
    ET.register_namespace("", SVG_NS)
    root = ET.parse(path).getroot()
    _inline_text_attrs(root, {})
    return ET.tostring(root, encoding="utf-8")


def render(lesson_id: int | None = None) -> list[str]:
    folders = [LESSONS_DIR / f"{lesson_id:03d}"] if lesson_id else sorted(LESSONS_DIR.iterdir())
    done = []
    for svg in sorted(p for f in folders for p in (f / "img").glob("*.svg")):
        page = pymupdf.open(stream=prepared_svg(svg), filetype="svg")[0]
        zoom = WIDTH / page.rect.width
        page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).save(svg.with_suffix(".png"))
        done.append(str(svg.with_suffix(".png").relative_to(LESSONS_DIR.parent.parent)))
    return done


if __name__ == "__main__":
    for path in render(int(sys.argv[1]) if len(sys.argv) > 1 else None):
        print(path)
