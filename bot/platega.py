"""Клиент кассы Platega: создать платёж и узнать его статус.

Документация: https://docs.platega.io. Авторизация - заголовки X-MerchantId и X-Secret.
Бот работает без входящего веб-сервера, поэтому статус платежа мы опрашиваем сами,
а не ждём callback.
"""
import logging
import uuid
from dataclasses import dataclass

import aiohttp

log = logging.getLogger(__name__)

BASE_URL = "https://app.platega.io"
TIMEOUT = aiohttp.ClientTimeout(total=20)

PENDING, CONFIRMED, CANCELED, CHARGEBACKED = "PENDING", "CONFIRMED", "CANCELED", "CHARGEBACKED"


class PlategaError(RuntimeError):
    """Касса недоступна или ответила ошибкой; текст не показываем пользователю."""


@dataclass(frozen=True)
class Transaction:
    id: str
    url: str
    status: str


class Platega:
    def __init__(self, merchant_id: str, secret: str, proxy_url: str | None = None, payment_method: int | None = None):
        self._headers = {"X-MerchantId": merchant_id, "X-Secret": secret, "Content-Type": "application/json"}
        self._proxy = proxy_url
        self._payment_method = payment_method

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
                async with http.request(method, BASE_URL + path, headers=self._headers, proxy=self._proxy,
                                        **kwargs) as response:
                    body = await response.text()
                    if response.status >= 400:
                        # тело ответа может содержать детали платежа, в журнал идёт только начало
                        log.error("Platega %s %s -> %s: %s", method, path, response.status, body[:300])
                        raise PlategaError(f"HTTP {response.status}")
                    return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise PlategaError(str(exc) or type(exc).__name__) from exc

    async def create(self, amount_rub: int, description: str, return_url: str, payload: str) -> Transaction:
        body = {
            "id": str(uuid.uuid4()),
            "paymentDetails": {"amount": amount_rub, "currency": "RUB"},
            "description": description,
            "return": return_url,
            "failedUrl": return_url,
            "payload": payload,
        }
        if self._payment_method is not None:
            body["paymentMethod"] = self._payment_method
        data = await self._request("POST", "/v2/transaction/process", json=body)
        # v2 отдаёт ссылку в поле url, первая версия API - в redirect
        tx_id, url = data.get("transactionId"), data.get("url") or data.get("redirect")
        if not tx_id or not url:
            log.error("Platega: неожиданный ответ на создание платежа, ключи %s", sorted(data))
            raise PlategaError("unexpected response")
        return Transaction(tx_id, url, data.get("status", PENDING))

    async def status(self, transaction_id: str) -> tuple[str, float | None]:
        """Статус платежа и его сумма в рублях (по ней проверяем, что оплачено столько, сколько выставлено)."""
        data = await self._request("GET", f"/transaction/{transaction_id}")
        details = data.get("paymentDetails") or {}
        amount = details.get("amount") if details.get("currency") == "RUB" else None
        return data.get("status", PENDING), amount
