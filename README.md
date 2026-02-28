# telegram-legal-doc-assistant

> Telegram-бот для анализа первичных документов по голосовой задаче. Отправьте голосовое сообщение и файл (PDF/фото) — получите текстовый отчёт, голосовое резюме и чек-лист.

**Стек:** Python 3.10+, aiogram 3, OpenAI (Whisper, GPT-4 Vision, GPT-4), gTTS, ReportLab, pdf2image.

---

## Возможности

- Связка **голос + документ** в одну сессию (порядок любой)
- Транскрибация голоса (Whisper) и распознавание текста документа (GPT-4 Vision)
- Юридический разбор: тип документа, реквизиты, риски, рекомендации
- Выдача: Markdown-отчёт, MP3-резюме, чек-лист (PDF или PNG)

## Требования

- Python 3.10+
- **Poppler** (для PDF→изображение): [Windows](https://github.com/osber/poppler-windows/releases) или `winget install Poppler`

## Установка

```bash
git clone https://github.com/YOUR_USERNAME/telegram-legal-doc-assistant.git
cd telegram-legal-doc-assistant

python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS

pip install -r requirements.txt
copy .env.example .env
```

В `.env` укажите `BOT_TOKEN` (от [@BotFather](https://t.me/BotFather)) и `OPENAI_API_KEY`.

## Запуск

```bash
python bot.py
```

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
| `SESSION_TIMEOUT_MINUTES` | Таймаут ожидания второго сообщения |

## Лицензия

MIT
