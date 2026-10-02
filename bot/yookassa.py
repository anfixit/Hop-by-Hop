"""Клиент YooKassa (API v3): создать платёж, узнать его статус, вернуть деньги.

Документация: https://yookassa.ru/developers/api. Авторизация - HTTP Basic (shopId и секретный ключ),
каждый POST несёт заголовок Idempotence-Key. Как и с Platega, статус платежа мы опрашиваем сами.

Чек в запросе не передаём: продавец на НПД, а сервисы для самозанятых (чеки при платежах и возвратах)
ЮKassa закрыла 29.12.2025. Доход регистрируется в "Мой налог" отдельно.
"""
import base64
import logging
import uuid

import aiohttp

from bot.platega import CANCELED, CONFIRMED, PENDING, Transaction

log = logging.getLogger(__name__)

BASE_URL = "https://api.yookassa.ru/v3"
TIMEOUT = aiohttp.ClientTimeout(total=20)

# Статусы YooKassa в общих статусах кассы; waiting_for_capture не бывает, платежи списываются сразу (capture)
_STATUS = {"succeeded": CONFIRMED, "canceled": CANCELED}


class YooKassaError(RuntimeError):
    """Касса недоступна или ответила ошибкой; текст не показываем пользователю."""


class YooKassa:
    def __init__(self, shop_id: str, secret_key: str, proxy_url: str | None = None):
        token = base64.b64encode(f"{shop_id}:{secret_key}".encode()).decode()
        self._auth = {"Authorization": f"Basic {token}"}
        self._proxy = proxy_url

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        headers = {**self._auth, "Idempotence-Key": str(uuid.uuid4())} if method == "POST" else self._auth
        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
                async with http.request(method, BASE_URL + path, headers=headers,
                                        proxy=self._proxy, **kwargs) as response:
                    body = await response.text()
                    if response.status >= 400:
                        # тело ошибки содержит только код и описание, но в журнал всё равно идёт только начало
                        log.error("YooKassa %s %s -> %s: %s", method, path, response.status, body[:300])
                        raise YooKassaError(f"HTTP {response.status}")
                    return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise YooKassaError(str(exc) or type(exc).__name__) from exc

    async def create(self, amount_rub: int, description: str, return_url: str, payload: str) -> Transaction:
        body = {
            "amount": {"value": f"{amount_rub}.00", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description[:128],
            "metadata": {"payload": payload},
        }
        data = await self._request("POST", "/payments", json=body)
        url = (data.get("confirmation") or {}).get("confirmation_url")
        if not data.get("id") or not url:
            log.error("YooKassa: неожиданный ответ на создание платежа, ключи %s", sorted(data))
            raise YooKassaError("unexpected response")
        return Transaction(data["id"], url, _STATUS.get(data.get("status"), PENDING))

    async def _payment(self, payment_id: str) -> dict:
        return await self._request("GET", f"/payments/{payment_id}")

    async def status(self, payment_id: str) -> tuple[str, float | None]:
        """Статус платежа и его сумма в рублях (по ней проверяем, что оплачено столько, сколько выставлено)."""
        data = await self._payment(payment_id)
        amount = data.get("amount") or {}
        value = float(amount["value"]) if amount.get("currency") == "RUB" and "value" in amount else None
        return _STATUS.get(data.get("status"), PENDING), value

    async def cancel_supported(self, payment_id: str) -> tuple[bool, str]:
        """Можно ли вернуть платёж целиком; вторым значением - причина отказа, если нельзя."""
        data = await self._payment(payment_id)
        if data.get("status") == "succeeded" and data.get("refundable"):
            return True, ""
        return False, f"статус {data.get('status')}, возврат недоступен"

    async def cancel(self, payment_id: str) -> tuple[bool, str]:
        """Вернуть платёж плательщику целиком. accepted=False - касса отклонила возврат."""
        data = await self._payment(payment_id)
        refund = await self._request("POST", "/refunds", json={"payment_id": payment_id, "amount": data["amount"]})
        if refund.get("status") in ("succeeded", "pending"):
            return True, ""
        return False, (refund.get("cancellation_details") or {}).get("reason", "")
