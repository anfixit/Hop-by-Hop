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
    # вариант "DNS: пояснение" без кавычек YAML превращает в словарь
    with pytest.raises(ContentError):
        _load_quiz([{"q": "?", "options": [{"DNS": "x"}, "b"], "answer": 0, "explain": "x"}], "t")


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

def test_packs_are_parsed_from_env_string():
    from bot.config import Pack, Settings

    s = Settings(_env_file=None, bot_token="1:a", packs="50:99:75, 350:490:450")
    assert s.packs == (Pack(50, 99, 75), Pack(350, 490, 450))
    assert not s.platega_enabled
    assert Settings(_env_file=None, bot_token="1:a", platega_merchant_id="m", platega_secret="s").platega_enabled


def test_stars_invoice_must_match_a_pack_exactly():
    from bot.config import Settings
    from bot.handlers.payments import _stars_pack, reviews_word

    s = Settings(_env_file=None, bot_token="1:a", packs="50:99:75")
    assert _stars_pack(s, "pack:50", 75).reviews == 50
    assert _stars_pack(s, "pack:50", 1) is None      # оплатили не ту сумму
    assert _stars_pack(s, "pack:999", 75) is None    # пакета с таким числом разборов нет
    assert [reviews_word(n) for n in (1, 2, 5, 11, 21)] == ["1 разбор", "2 разбора", "5 разборов", "11 разборов", "21 разбор"]


async def test_platega_client_builds_request_and_reads_both_api_versions(monkeypatch):
    from bot.platega import Platega, PlategaError

    calls = []
    answers = iter([
        {"transactionId": "t1", "url": "https://pay.example/1", "status": "PENDING"},      # v2
        {"transactionId": "t2", "redirect": "https://pay.example/2"},                      # v1
        {"status": "PENDING"},                                                              # без ссылки
        {"id": "t1", "status": "CONFIRMED", "paymentDetails": {"amount": 99, "currency": "RUB"}},
    ])

    async def fake(self, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        return next(answers)

    monkeypatch.setattr(Platega, "_request", fake)
    client = Platega("merchant", "secret", payment_method=2)

    tx = await client.create(99, "пакет", "https://t.me/bot", "tg:5")
    assert (tx.id, tx.url) == ("t1", "https://pay.example/1")
    method, path, body = calls[0]
    assert (method, path) == ("POST", "/v2/transaction/process")
    assert body["paymentDetails"] == {"amount": 99, "currency": "RUB"} and body["paymentMethod"] == 2

    assert (await client.create(99, "пакет", "https://t.me/bot", "tg:5")).url == "https://pay.example/2"
    with pytest.raises(PlategaError):
        await client.create(99, "пакет", "https://t.me/bot", "tg:5")
    assert await client.status("t1") == ("CONFIRMED", 99)
    assert calls[-1][:2] == ("GET", "/transaction/t1")