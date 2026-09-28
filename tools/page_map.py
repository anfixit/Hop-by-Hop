"""Соответствие страниц PDF печатным номерам страниц книги.

Печатный номер берётся из колонтитула: отдельная строка-число в начале страницы
("26") или число в конце строки колонтитула ("5.7. Сетевой уровень интернета    505").

  python -m tools.page_map olifer          сводка смещений по диапазонам
  python -m tools.page_map olifer 801      печатный номер для PDF-страницы 801

Как библиотека: printed_page(book, pdf_page) -> int | None
"""
import re
import sys
from functools import cache
from pathlib import Path

BOOKS_TEXT = Path(__file__).resolve().parents[2] / "books_text"
# "26", "... сетей    505", "504    Глава 5 ..."
HEADER_NUMBER = re.compile(r"^\s*(\d{1,4})\s*$|\s{2,}(\d{1,4})\s*$|^\s*(\d{1,4})\s{2,}")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f]")


def _header_number(text: str) -> int | None:
    for line in text.splitlines()[:3]:
        m = HEADER_NUMBER.search(CONTROL.sub("", line))
        if m:
            return int(next(g for g in m.groups() if g))
    return None


@cache
def page_map(book: str) -> dict[int, int]:
    """PDF-страница -> печатный номер; пропуски заполняются по соседям с тем же смещением."""
    pages = sorted((BOOKS_TEXT / book / "pages").glob("*.txt"))
    raw = {int(p.stem): _header_number(p.read_text(encoding="utf-8")) for p in pages}
    offsets = {pdf: printed - pdf for pdf, printed in raw.items() if printed is not None}
    # колонтитул мог содержать случайное число: доверяем смещению, только если его
    # подтверждает хотя бы одна соседняя страница
    trusted = {pdf: off for pdf, off in offsets.items()
               if offsets.get(pdf - 1) == off or offsets.get(pdf + 1) == off}
    # Страница без колонтитула - обычно первая страница главы. Смещение меняется именно
    # на границах глав (в печатной книге перед главой бывает пустая страница, которой нет
    # в PDF), поэтому берём смещение следующей страницы с колонтитулом, а не предыдущей.
    result, nxt = {}, None
    for pdf in sorted(raw, reverse=True):
        if pdf in trusted:
            nxt = trusted[pdf]
        if nxt is not None:
            result[pdf] = pdf + nxt
    return result


def printed_page(book: str, pdf_page: int) -> int | None:
    return page_map(book).get(pdf_page)


def offset_ranges(book: str) -> list[tuple[int, int, int]]:
    """[(pdf_from, pdf_to, offset)] - участки с постоянным смещением."""
    ranges = []
    for pdf, printed in sorted(page_map(book).items()):
        off = printed - pdf
        if ranges and ranges[-1][2] == off and ranges[-1][1] == pdf - 1:
            ranges[-1] = (ranges[-1][0], pdf, off)
        else:
            ranges.append((pdf, pdf, off))
    return ranges


if __name__ == "__main__":
    match sys.argv[1:]:
        case [book]:
            for a, b, off in offset_ranges(book):
                print(f"PDF {a:4d}-{b:4d}: печатная = PDF {off:+d}")
        case [book, page]:
            print(printed_page(book, int(page)))
        case _:
            print(__doc__)
