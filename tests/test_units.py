from types import SimpleNamespace

import pytest

from bot.access import sign, verify
from bot.content import ContentError, _load_quiz
from bot.review import cost_of
from bot.telegraph import markdown_to_nodes

SECRET = b"test-secret"


def test_signed_callback_roundtrip():
    data = sign(SECRET, 42, "qa:1:0:0:2")
    assert verify(SECRET, 42, data) == "qa:1:0:0:2"


def test_signed_callback_rejects_tampering_and_other_users():
    data = sign(SECRET, 42, "qr:1:1")
    assert verify(SECRET, 42, data.replace("qr:1:1", "qr:1:9")) is None
    assert verify(SECRET, 43, data) is None
    assert verify(SECRET, 42, "qr:1:9") is None


def test_callback_fits_telegram_limit():
    assert len(sign(SECRET, 42, "qa:105:19:19:5").encode()) <= 64


def test_quiz_validation():
    with pytest.raises(ContentError):
        _load_quiz([{"q": "?", "options": ["a", "b"], "answer": 2, "explain": "x"}], "t")
    with pytest.raises(ContentError):
        _load_quiz([{"q": "?", "options": ["a", "a"], "answer": 0, "explain": "x"}], "t")
    assert _load_quiz([{"q": "?", "options": ["a", "b"], "answer": 0, "explain": "x"}], "t")[0].answer == 0


def test_cost_with_cache():
    usage = SimpleNamespace(input_tokens=1000, output_tokens=500, cache_creation_input_tokens=0,
                            cache_read_input_tokens=2000)
    tokens_in, tokens_out, cost = cost_of("claude-sonnet-5", usage)
    assert (tokens_in, tokens_out) == (3000, 500)
    assert cost == pytest.approx((1000 + 0.1 * 2000) * 2 / 1e6 + 500 * 10 / 1e6)


def test_telegraph_headings_and_tables():
    nodes = markdown_to_nodes("## Раздел\n\n| a | b |\n|---|---|\n| 1 | 22 |\n\ntext **bold**")
    assert nodes[0] == {"tag": "h3", "children": ["Раздел"]}
    assert nodes[1] == {"tag": "ul", "children": [{"tag": "li", "children": [{"tag": "strong", "children": ["1"]}, {"tag": "br"}, "b: 22"]}]}
    assert nodes[2] == {"tag": "p", "children": ["text ", {"tag": "strong", "children": ["bold"]}]}


def test_telegraph_code_block():
    nodes = markdown_to_nodes("```\n[ETH | IP]\n```")
    assert nodes == [{"tag": "pre", "children": [{"tag": "code", "children": ["[ETH | IP]\n"]}]}]


def test_telegraph_image_becomes_figure():
    nodes = markdown_to_nodes("text\n\n![Подпись](img/a.png)\n", lambda src: "https://x/" + src)
    assert nodes[1] == {"tag": "figure", "children": [
        {"tag": "img", "attrs": {"src": "https://x/img/a.png"}},
        {"tag": "figcaption", "children": ["Подпись"]},
    ]}