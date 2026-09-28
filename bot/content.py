"""Загрузка и проверка уроков из content/lessons/NNN/.

Каждый урок - папка с двумя файлами:
  lesson.yaml - метаданные, тест и открытые вопросы
  lesson.md   - текст урока (публикуется в Telegraph)
Ссылки на опубликованные страницы Telegraph хранятся в content/telegraph.json.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from bot.config import REPO_ROOT

CONTENT_DIR = REPO_ROOT / "content"
LESSONS_DIR = CONTENT_DIR / "lessons"
TELEGRAPH_INDEX = CONTENT_DIR / "telegraph.json"

MAX_OPTIONS = 6
STATUSES = {"draft", "published"}


class ContentError(ValueError):
    pass


@dataclass(frozen=True)
class QuizQuestion:
    question: str
    options: tuple[str, ...]
    answer: int
    explain: str


@dataclass(frozen=True)
class OpenQuestion:
    question: str
    reference: str
    key_points: tuple[str, ...]


@dataclass(frozen=True)
class Lesson:
    id: int
    title: str
    summary: str
    status: str
    quiz: tuple[QuizQuestion, ...]
    open_questions: tuple[OpenQuestion, ...]
    text_path: Path
    telegraph_url: str | None = None

    @property
    def published(self) -> bool:
        return self.status == "published"


@dataclass
class Course:
    lessons: dict[int, Lesson] = field(default_factory=dict)

    def ordered(self, include_drafts: bool) -> list[Lesson]:
        return [l for _, l in sorted(self.lessons.items()) if include_drafts or l.published]

    def get(self, lesson_id: int, include_drafts: bool) -> Lesson | None:
        lesson = self.lessons.get(lesson_id)
        if lesson and (include_drafts or lesson.published):
            return lesson
        return None

    def previous(self, lesson_id: int, include_drafts: bool) -> Lesson | None:
        earlier = [l for l in self.ordered(include_drafts) if l.id < lesson_id]
        return earlier[-1] if earlier else None


def _require(data: dict, key: str, where: str):
    if key not in data or data[key] in (None, "", []):
        raise ContentError(f"{where}: нет поля {key!r}")
    return data[key]


def _load_quiz(items: list, where: str) -> tuple[QuizQuestion, ...]:
    quiz = []
    for i, item in enumerate(items, start=1):
        at = f"{where}, тест #{i}"
        raw = _require(item, "options", at)
        # "Текст: пояснение" без кавычек YAML читает как словарь, и на кнопке окажется {'Текст': ...}
        for o in raw:
            if not isinstance(o, (str, int, float)):
                raise ContentError(f"{at}: вариант {o!r} не строка - возьми его в кавычки")
        options = tuple(str(o) for o in raw)
        answer = int(_require(item, "answer", at))
        if not 2 <= len(options) <= MAX_OPTIONS:
            raise ContentError(f"{at}: вариантов должно быть от 2 до {MAX_OPTIONS}")
        if not 0 <= answer < len(options):
            raise ContentError(f"{at}: answer={answer} вне диапазона вариантов")
        if len(set(options)) != len(options):
            raise ContentError(f"{at}: варианты повторяются")
        quiz.append(QuizQuestion(str(_require(item, "q", at)), options, answer, str(_require(item, "explain", at))))
    return tuple(quiz)


def _load_open(items: list, where: str) -> tuple[OpenQuestion, ...]:
    result = []
    for i, item in enumerate(items, start=1):
        at = f"{where}, вопрос #{i}"
        result.append(OpenQuestion(
            question=str(_require(item, "q", at)),
            reference=str(_require(item, "reference", at)).strip(),
            key_points=tuple(str(p) for p in _require(item, "key_points", at)),
        ))
    return tuple(result)


def load_lesson(folder: Path, telegraph: dict[str, dict]) -> Lesson:
    where = folder.name
    meta = yaml.safe_load((folder / "lesson.yaml").read_text(encoding="utf-8-sig")) or {}
    text_path = folder / "lesson.md"
    if not text_path.exists():
        raise ContentError(f"{where}: нет lesson.md")
    status = _require(meta, "status", where)
    if status not in STATUSES:
        raise ContentError(f"{where}: status должен быть одним из {sorted(STATUSES)}")
    lesson_id = int(_require(meta, "id", where))
    if folder.name != f"{lesson_id:03d}":
        raise ContentError(f"{where}: папка должна называться {lesson_id:03d}")
    return Lesson(
        id=lesson_id,
        title=str(_require(meta, "title", where)),
        summary=str(_require(meta, "summary", where)).strip(),
        status=status,
        quiz=_load_quiz(_require(meta, "quiz", where), where),
        open_questions=_load_open(meta.get("open_questions") or [], where),
        text_path=text_path,
        telegraph_url=telegraph.get(str(lesson_id), {}).get("url"),
    )


def load_course(lessons_dir: Path = LESSONS_DIR, telegraph_index: Path = TELEGRAPH_INDEX) -> Course:
    telegraph = json.loads(telegraph_index.read_text(encoding="utf-8")) if telegraph_index.exists() else {}
    course = Course()
    for folder in sorted(p for p in lessons_dir.iterdir() if p.is_dir()):
        lesson = load_lesson(folder, telegraph)
        if lesson.id in course.lessons:
            raise ContentError(f"{folder.name}: повторяющийся id {lesson.id}")
        course.lessons[lesson.id] = lesson
    return course
