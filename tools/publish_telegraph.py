"""Публикация уроков в Telegraph.

  python -m tools.publish_telegraph init         создать аккаунт и записать TELEGRAPH_TOKEN в .env
  python -m tools.publish_telegraph publish 1    опубликовать или обновить урок 1
  python -m tools.publish_telegraph preview 1    показать узлы Telegraph без публикации
  python -m tools.publish_telegraph legal        опубликовать или обновить оферту, политику и правила

Страница урока создаётся один раз; повторная публикация редактирует её по тому же адресу.
Адреса хранятся в content/telegraph.json (он в git).

Картинки берутся из репозитория на GitHub по ссылке на конкретный коммит, поэтому перед
публикацией коммит с картинками должен быть уже отправлен (git push).
"""
import json
import shutil
import subprocess
import sys

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from bot import telegraph
from bot.config import REPO_ROOT
from bot.content import LESSONS_DIR, TELEGRAPH_INDEX

AUTHOR = "Hop-by-Hop"
ENV_FILE = REPO_ROOT / ".env"
RAW_BASE = "https://raw.githubusercontent.com/anfixit/Hop-by-Hop"
LEGAL_DIR = REPO_ROOT.parent / "legal"  # исходники документов лежат вне репозитория
LEGAL_DOCS = ("offer", "privacy", "rules")


class PublishSettings(BaseSettings):
    """Только то, что нужно для публикации: боту и его токену здесь делать нечего."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")
    telegraph_token: SecretStr | None = None
    proxy_url: str | None = None


GIT = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"


def _git(*args: str) -> str:
    return subprocess.run([GIT, *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _pushed_commit() -> str:
    """SHA текущего коммита, если он уже есть на GitHub в main, иначе ошибка."""
    sha = _git("rev-parse", "HEAD")
    _git("fetch", "-q", "origin")
    if "origin/main" not in _git("branch", "-r", "--contains", sha).split():
        raise SystemExit("Текущий коммит ещё не на GitHub: сначала git push, иначе картинки не откроются.")
    return sha


def init() -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    if any(l.startswith("TELEGRAPH_TOKEN=") and l.strip() != "TELEGRAPH_TOKEN=" for l in lines):
        print("TELEGRAPH_TOKEN уже задан в .env, ничего не делаю.")
        return
    account = telegraph.call("createAccount", PublishSettings().proxy_url, short_name="HopByHop", author_name=AUTHOR)
    token_line = f"TELEGRAPH_TOKEN={account['access_token']}"
    lines = [token_line if l.startswith("TELEGRAPH_TOKEN=") else l for l in lines]
    if token_line not in lines:
        lines.append(token_line)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Аккаунт Telegraph создан, токен записан в .env (в консоль не выводится).")


def _lesson_nodes(lesson_id: int, commit: str | None) -> tuple[str, list]:
    folder = LESSONS_DIR / f"{lesson_id:03d}"
    text = (folder / "lesson.md").read_text(encoding="utf-8-sig")
    title, body = telegraph.split_title(text)

    def resolve(src: str) -> str:
        if src.startswith(("http://", "https://")):
            return src
        if not (folder / src).exists():
            raise SystemExit(f"Урок {lesson_id}: нет файла картинки {src}")
        rel = (folder / src).relative_to(REPO_ROOT).as_posix()
        return f"{RAW_BASE}/{commit or 'main'}/{rel}"

    nodes = telegraph.markdown_to_nodes(body, resolve)
    size = len(json.dumps(nodes, ensure_ascii=False).encode())
    if size > telegraph.MAX_CONTENT_BYTES:
        raise SystemExit(f"Урок {lesson_id}: {size} байт, лимит Telegraph 64 КБ. Раздели урок на части.")
    return title, nodes


def publish(lesson_id: int) -> None:
    settings = PublishSettings()
    if settings.telegraph_token is None:
        raise SystemExit("Нет TELEGRAPH_TOKEN. Сначала: python -m tools.publish_telegraph init")
    title, nodes = _lesson_nodes(lesson_id, _pushed_commit())
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


def publish_legal() -> None:
    """Документы для /terms. Строка "{url}" в тексте заменяется адресом самой страницы."""
    settings = PublishSettings()
    if settings.telegraph_token is None:
        raise SystemExit("Нет TELEGRAPH_TOKEN. Сначала: python -m tools.publish_telegraph init")
    index = json.loads(TELEGRAPH_INDEX.read_text(encoding="utf-8")) if TELEGRAPH_INDEX.exists() else {}
    for name in LEGAL_DOCS:
        title, body = telegraph.split_title((LEGAL_DIR / f"{name}.md").read_text(encoding="utf-8-sig"))
        params = dict(access_token=settings.telegraph_token.get_secret_value(), title=title, author_name=AUTHOR,
                      return_content="false")
        if name not in index:  # адрес страницы появляется только после её создания
            page = telegraph.call("createPage", settings.proxy_url, content=[{"tag": "p", "children": [title]}], **params)
            index[name] = {"path": page["path"], "url": page["url"]}
        nodes = telegraph.markdown_to_nodes(body.replace("{url}", index[name]["url"].removeprefix("https://")))
        telegraph.call(f"editPage/{index[name]['path']}", settings.proxy_url, content=nodes, **params)
        print(f"{name}: {index[name]['url']}")
    TELEGRAPH_INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str]) -> None:
    match argv:
        case ["init"]:
            init()
        case ["legal"]:
            publish_legal()
        case ["publish", lesson_id]:
            publish(int(lesson_id))
        case ["preview", lesson_id]:
            title, nodes = _lesson_nodes(int(lesson_id), commit=None)
            print(title)
            print(json.dumps(nodes, ensure_ascii=False, indent=1))
        case _:
            print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
