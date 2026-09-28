"""Собирает assets/logo.svg и рендерит PNG для аватара бота.

Точки траектории считаются здесь, потому что рендер MuPDF не поддерживает
stroke-dasharray и градиенты.
"""
from pathlib import Path

import pymupdf

ASSETS = Path(__file__).resolve().parents[1] / "assets"

BG = "#0E1830"
ACCENT = "#2DD4BF"
DIM = "#3B4A6B"
NODE_Y = 640
NODES = [232, 417, 602, 787]
ARC_LIFT = 230  # насколько контрольная точка выше узлов
DOTS_PER_HOP = 7


def quad(p0, c, p1, t):
    return tuple((1 - t) ** 2 * a + 2 * (1 - t) * t * b + t ** 2 * d for a, b, d in zip(p0, c, p1))


def hop_dots(x0, x1, t_max=1.0):
    p0, p1 = (x0, NODE_Y), (x1, NODE_Y)
    c = ((x0 + x1) / 2, NODE_Y - ARC_LIFT)
    n = DOTS_PER_HOP
    ts = [0.2 + 0.6 * i / (n - 1) for i in range(n)]
    return [quad(p0, c, p1, t) for t in ts if t <= t_max]


def build_svg() -> str:
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">',
        f'<rect width="1024" height="1024" fill="{BG}"/>',
    ]
    # пройденные прыжки - тусклые, текущий - яркий до пакета
    for x0, x1 in zip(NODES, NODES[1:-1]):
        parts += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="{DIM}"/>' for x, y in hop_dots(x0, x1)]
    parts += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="10" fill="{ACCENT}"/>'
              for x, y in hop_dots(NODES[-2], NODES[-1], t_max=0.45)]

    for i, x in enumerate(NODES):
        color = ACCENT if i == len(NODES) - 1 else DIM
        parts.append(f'<circle cx="{x}" cy="{NODE_Y}" r="36" fill="{BG}" stroke="{color}" stroke-width="15"/>')

    # пакет в полёте над последним прыжком: конверт с замком
    px, py = quad((NODES[-2], NODE_Y), ((NODES[-2] + NODES[-1]) / 2, NODE_Y - ARC_LIFT), (NODES[-1], NODE_Y), 0.55)
    parts.append(f'''<g transform="translate({px:.1f} {py - 95:.1f}) rotate(-8) scale(1.3)">
  <rect x="-80" y="-58" width="160" height="116" rx="18" fill="{ACCENT}"/>
  <path d="M-64 -42 L0 8 L64 -42" fill="none" stroke="{BG}" stroke-width="12" stroke-linejoin="round" stroke-linecap="round"/>
  <g transform="translate(48 28)">
    <path d="M-13 -6 v-10 a13 13 0 0 1 26 0 v10" fill="none" stroke="{BG}" stroke-width="8"/>
    <rect x="-21" y="-8" width="42" height="34" rx="6" fill="{BG}"/>
    <circle cx="0" cy="8" r="5" fill="{ACCENT}"/>
  </g>
</g>''')
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    svg_path = ASSETS / "logo.svg"
    svg_path.write_text(build_svg(), encoding="utf-8")
    page = pymupdf.open(svg_path)[0]
    for size in (640, 1024):
        zoom = size / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        pix.save(ASSETS / f"logo-{size}.png")
        print(f"logo-{size}.png: {pix.width}x{pix.height}")


if __name__ == "__main__":
    main()
