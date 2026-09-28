"""Проверка всего контента курса: запускается перед каждой публикацией."""
from bot.content import load_course
from bot.telegraph import MAX_CONTENT_BYTES, image_sources, markdown_to_nodes, split_title

import json


def test_course_loads():
    course = load_course()
    assert course.lessons, "нет ни одного урока"


def test_lessons_render_for_telegraph():
    for lesson in load_course().lessons.values():
        title, body = split_title(lesson.text_path.read_text(encoding="utf-8"))
        assert title
        nodes = markdown_to_nodes(body)
        assert len(json.dumps(nodes, ensure_ascii=False).encode()) <= MAX_CONTENT_BYTES


def test_typography():
    """Правило проекта: без кавычек-ёлочек и длинного тире."""
    for lesson in load_course().lessons.values():
        folder = lesson.text_path.parent
        for path in folder.rglob("*"):
            if path.suffix not in {".md", ".yaml", ".svg"}:
                continue
            text = path.read_text(encoding="utf-8")
            for bad in ("«", "»", "—"):
                assert bad not in text, f"{path}: символ {bad!r}"


def test_lesson_images_exist():
    for lesson in load_course().lessons.values():
        folder = lesson.text_path.parent
        for src in image_sources(lesson.text_path.read_text(encoding="utf-8-sig")):
            if not src.startswith(("http://", "https://")):
                assert (folder / src).exists(), f"урок {lesson.id}: нет картинки {src}"