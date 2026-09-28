"""Извлекает оглавление и текст книг из ../Books в ../books_text (вне репозитория).

books_text/<slug>/toc.md       - оглавление с номерами страниц PDF
books_text/<slug>/pages/NNNN.txt - текст каждой страницы (для поиска и сверки)
"""
from pathlib import Path

import pymupdf

WORKSPACE = Path(__file__).resolve().parents[2]  # папка над репозиторием
SRC = WORKSPACE / "Books"
DST = WORKSPACE / "books_text"

SLUGS = {"olifer": "Olifer", "tanenbaum": "Tanenbaum", "ristic": "bulletproof-tls"}


def slug_for(pdf: Path) -> str:
    for slug, marker in SLUGS.items():
        if marker.lower() in pdf.name.lower():
            return slug
    return pdf.stem


def main() -> None:
    for pdf in sorted(SRC.rglob("*.pdf")):
        slug = slug_for(pdf)
        out = DST / slug
        (out / "pages").mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open(pdf)

        toc = doc.get_toc(simple=True)
        lines = [f"# {pdf.name}", f"pages: {doc.page_count}, toc entries: {len(toc)}", ""]
        lines += [f"{'  ' * (lvl - 1)}- {title.strip()} (p.{page})" for lvl, title, page in toc]
        (out / "toc.md").write_text("\n".join(lines), encoding="utf-8")

        empty = 0
        for i, page in enumerate(doc, start=1):
            text = page.get_text()
            if not text.strip():
                empty += 1
            (out / "pages" / f"{i:04d}.txt").write_text(text, encoding="utf-8")
        print(f"{slug}: {doc.page_count} pages, {len(toc)} toc entries, {empty} pages without text")


if __name__ == "__main__":
    main()
