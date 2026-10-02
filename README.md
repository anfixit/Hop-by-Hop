# Hop-by-Hop

Telegram course bot on computer networks: from bits and Ethernet to TLS 1.3 and modern proxy protocols. Follow a packet hop by hop through Linux, netfilter and cryptography, with a security-first focus.

Курс по компьютерным сетям в Telegram: путь пакета от кабеля до сервера, с акцентом на информационную безопасность. Программа курса: [docs/program.md](docs/program.md).

## Как устроено

- Уроки лежат в `content/lessons/NNN/`: `lesson.md` (текст, публикуется в Telegraph) и `lesson.yaml` (тест и вопросы на понимание).
- Глава открывается после сдачи теста предыдущей. Открытые ответы разбирает Claude API. Уроки и тесты бесплатны; разборы ИИ платные: пробный запас выдаётся один раз, дальше пакеты за звёзды Telegram или через рублёвую кассу - YooKassa или Platega (`bot/handlers/payments.py`, `bot/yookassa.py`, `bot/platega.py`).
- Бот: Python 3.12, aiogram 3, SQLAlchemy (SQLite локально).

## Запуск локально

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env        # и заполнить значения
.venv\Scripts\python -m pytest
.venv\Scripts\python -m bot
```

Публикация урока в Telegraph:

```bash
.venv\Scripts\python -m tools.publish_telegraph init        # один раз
.venv\Scripts\python -m tools.publish_telegraph publish 1
```

## Команды администратора

- `/stats` - пользователи, прохождение, расходы на ИИ, продажи пакетов
- `/topup 20` - отметить пополнение баланса Claude API на $20
- `/grant <user_id> <разборов>` - начислить разборы вручную
- `/reload` - перечитать уроки без перезапуска
