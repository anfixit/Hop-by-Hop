"""Публикация уроков в Telegraph.

  python -m tools.publish_telegraph init         создать аккаунт и записать TELEGRAPH_TOKEN в .env
  python -m tools.publish_telegraph publish 1    опубликовать или обновить урок 1
  python -m tools.publish_telegraph preview 1    показать узлы Telegraph без публикации

Страница урока создаётся один раз; повторная публикация редактирует её по тому же адресу.
Адреса хранятся в content/telegraph.json (он в git).
"""
import json
import sys

from bot import telegraph
from bot.config import REPO_ROOT, Settings
from bot.content import LESSONS_DIR, TELEGRAPH_INDEX

AUTHOR = "Hop-by-Hop"
ENV_FILE = REPO_ROOT / ".env"


def init() -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    if any(l.startswith("TELEGRAPH_TOKEN=") and l.strip() != "TELEGRAPH_TOKEN=" for l in lines):
        print("TELEGRAPH_TOKEN уже задан в .env, ничего не делаю.")
        return
    proxy = Settings().proxy_url
    account = telegraph.call("createAccount", proxy, short_name="HopByHop", author_name=AUTHOR)
    token_line = f"TELEGRAPH_TOKEN={account['access_token']}"
    lines = [token_line if l.startswith("TELEGRAPH_TOKEN=") else l for l in lines]
    if token_line not in lines:
        lines.append(token_line)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Аккаунт Telegraph создан, токен записан в .env (в консоль не выводится).")


def _lesson_nodes(lesson_id: int) -> tuple[str, list]:
    text = (LESSONS_DIR / f"{lesson_id:03d}" / "lesson.md").read_text(encoding="utf-8")
    title, body = telegraph.split_title(text)
    nodes = telegraph.markdown_to_nodes(body)
    size = len(json.dumps(nodes, ensure_ascii=False).encode())
    if size > telegraph.MAX_CONTENT_BYTES:
        raise SystemExit(f"Урок {lesson_id}: {size} байт, лимит Telegraph 64 КБ. Раздели урок на части.")
    return title, nodes


def publish(lesson_id: int) -> None:
    settings = Settings()
    if settings.telegraph_token is None:
        raise SystemExit("Нет TELEGRAPH_TOKEN. Сначала: python -m tools.publish_telegraph init")
    title, nodes = _lesson_nodes(lesson_id)
    index = json.loads(TELEGRAPH_INDEX.read_text(encoding="utf-8")) if TELEGRAPH_INDEX.exists() else {}
    params = dict(access_token=settings.telegraph_token.get_secret_value(), title=title, author_name=AUTHOR,
                  content=nodes, return_content="false")
    entry = index.get(str(lesson_id))
    if entry:
        page = telegraph.call(f"editPage/{entry['path']}", settings.proxy_url, **params)
    else:
        page = telegraph.call("createPage", settings.proxy_url, **params)
    index[str(lesson_id)] = {"path": page["path"], "url": page["url"]}
    TELEGRAPH_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Урок {lesson_id}: {page['url']}")


def main(argv: list[str]) -> None:
    match argv:
        case ["init"]:
            init()
        case ["publish", lesson_id]:
            publish(int(lesson_id))
        case ["preview", lesson_id]:
            title, nodes = _lesson_nodes(int(lesson_id))
            print(title)
            print(json.dumps(nodes, ensure_ascii=False, indent=1))
        case _:
            print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
