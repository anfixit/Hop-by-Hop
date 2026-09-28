FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot bot
COPY content content

# Бот работает от непривилегированного пользователя; писать может только в /data (база)
RUN useradd --system --uid 10001 --no-create-home bot \
    && mkdir /data && chown bot /data
USER bot
VOLUME /data
ENV DATABASE_URL=sqlite+aiosqlite:////data/bot.sqlite3

CMD ["python", "-m", "bot"]
