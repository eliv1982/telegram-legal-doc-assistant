# telegram-legal-doc-assistant

> Telegram-бот для анализа первичных документов по голосовой задаче. Отправьте голосовое сообщение и файл (PDF/фото) — получите текстовый отчёт, голосовое резюме и чек-лист.

**Стек:** Python 3.12, aiogram 3, OpenAI (Whisper `whisper-1`, `gpt-4o` для распознавания и анализа документа, `gpt-4o-mini` для отчёта), gTTS, ReportLab, pdf2image.

> **Важно.** Это учебный/портфолио-проект, а не юридическая консультация и не замена юриста.
> Загруженные документы и голосовые сообщения передаются во внешние сервисы: Telegram и OpenAI;
> при `TTS_PROVIDER=gtts` текст голосового резюме отправляется в Google. Не загружайте конфиденциальные документы.
> Поведение и ограничения ещё дорабатываются: возможны ошибки анализа, а временные файлы пока могут оставаться на диске.

---

## Возможности

- Связка **голос + документ** в одну сессию (порядок любой)
- Транскрибация голоса (Whisper) и распознавание текста документа (vision-модель `gpt-4o`)
- Юридический разбор: тип документа, реквизиты, риски, рекомендации
- Выдача: Markdown-отчёт, MP3-резюме, чек-лист (PDF или PNG)

## Требования

- **Python 3.12** (в CI дополнительно проверяется 3.13)
- [**uv**](https://docs.astral.sh/uv/getting-started/installation/) — менеджер окружения и зависимостей (`winget install astral-sh.uv` или `pip install uv`)
- **Poppler** (для PDF→изображение): Windows — `winget install oschwartz10612.Poppler` или [сборка](https://github.com/oschwartz10612/poppler-windows/releases), Linux — `sudo apt install poppler-utils`, macOS — `brew install poppler`. Команда `pdftoppm` должна быть в `PATH`.

## Установка

```bash
git clone https://github.com/eliv1982/telegram-legal-doc-assistant.git
cd telegram-legal-doc-assistant

uv sync          # создаёт .venv (Python 3.12) и ставит зависимости ровно по uv.lock
copy .env.example .env   # Linux/macOS: cp .env.example .env
```

В `.env` укажите `BOT_TOKEN` (от [@BotFather](https://t.me/BotFather)) и `OPENAI_API_KEY`.

Зависимости описаны в `pyproject.toml`, точные версии зафиксированы в `uv.lock` (единственный источник правды; `requirements.txt` больше нет).

## Запуск

```bash
uv run python bot.py
```

## Тесты и линтер

```bash
uv run pytest          # офлайн: ни Telegram, ни OpenAI, ни Google не вызываются, ключи не нужны
uv run ruff check .
```

Тесты не читают `.env` и блокируют внешние сетевые соединения. Часть тестов — намеренные `xfail(strict=True)`: они фиксируют уже подтверждённые, но ещё не исправленные дефекты (жизненный цикл временных файлов, семантика confidence, гонка состояний FSM). Тест, который начал проходить, провалит прогон — значит, пометку `xfail` нужно снять вместе с исправлением.

## Использование

1. Отправьте боту **голосовое сообщение** с задачей (например: «Проверь этот договор на риски»).
2. Отправьте **документ** — PDF или изображение (JPG, PNG).
3. Порядок не важен — бот объединит голос и документ и вернёт результат.

## Переменные окружения

| Переменная | Описание |
|------------|----------|
| `BOT_TOKEN` | Токен бота (BotFather) |
| `OPENAI_API_KEY` | Ключ OpenAI API |
| `TTS_PROVIDER` | `gtts` или `openai` |
| `CHECKLIST_FORMAT` | `pdf` или `png` |
| `SESSION_TIMEOUT_MINUTES` | Зарезервировано: значение читается, но таймаут ожидания второго сообщения пока не реализован |

## Лицензия

MIT
