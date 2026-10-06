"""
Обработка голосовых сообщений и документов. Связывание в сессию и запуск пайплайна.
"""
import logging
import time
from contextlib import suppress
from pathlib import Path

from aiogram import Bot, F, Router

import config
from aiogram.types import BufferedInputFile, Message
from aiogram.fsm.context import FSMContext

from states.user_states import (
    PENDING_FILE_KEY,
    STARTED_AT_KEY,
    WORKSPACE_KEY,
    UserSessionState,
    discard_session,
    session_expired,
)
from services.openai_service import OpenAIService
from services.tts_service import TTSService
from services.pdf_converter import pdf_first_page_to_image, image_to_bytes, extract_text_from_pdf
from services.checklist_generator import generate_checklist
from services.workspace import SessionWorkspace
from utils.helpers import parse_confidence
from utils.logging_config import log_failure

router = Router()
logger = logging.getLogger(__name__)

# Расширения для документов
ALLOWED_DOC_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_MIME_IMAGE = {"image/jpeg", "image/png", "image/webp"}

ERROR_MSG = "Произошла ошибка при обработке. Попробуйте позже."
DOWNLOAD_ERROR_MSG = "Не удалось получить файл из Telegram. Отправьте его ещё раз."
SESSION_EXPIRED_MSG = "⌛ Предыдущая незавершённая сессия истекла, её файлы удалены. Начинаю новую."
WAIT_DOC_MSG = "✅ Голос получен. Отправь документ (PDF или изображение)."
WAIT_VOICE_MSG = "✅ Документ получен. Отправь голосовое сообщение с задачей."
LOW_CONFIDENCE_MSG = (
    "⚠️ Качество распознавания документа низкое. Отправьте документ заново в лучшем разрешении "
    "(чёткая фотография или скан) или попробуйте PDF с текстовым слоем."
)


async def run_pipeline(
    bot: Bot,
    user_id: int,
    voice_path: Path,
    doc_path: Path,
    openai_service: OpenAIService,
    tts_service: TTSService,
    checklist_format: str,
) -> None:
    """
    Полный пайплайн: транскрибация → OCR → анализ → пост-обработка → TTS → чек-лист → отправка.
    """
    try:
        status_msg = await bot.send_message(user_id, "⏳ Обрабатываю…")
        # 1. Транскрибация голоса
        transcript = await openai_service.transcribe_voice(voice_path)
        logger.info("Transcribed: %d chars", len(transcript))

        # 2. Извлечение текста документа
        doc_ext = doc_path.suffix.lower()
        if doc_ext == ".pdf":
            image_bytes = pdf_first_page_to_image(doc_path)
            mime = "image/jpeg"
        else:
            image_bytes, _ = image_to_bytes(doc_path)
            mime = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
            }.get(doc_ext, "image/jpeg")
        document_text = await openai_service.extract_text_from_image(image_bytes, mime)
        logger.info("Document text length: %d", len(document_text))

        # 3. Анализ
        analysis = await openai_service.analyze_document(transcript, document_text)
        confidence = parse_confidence(analysis.get("confidence"))

        # 3.1 Проверка confidence: при низком — fallback (PDF) или запрос перезагрузки
        if confidence < config.CONFIDENCE_THRESHOLD:
            fallback_text = ""
            if doc_ext == ".pdf":
                fallback_text = extract_text_from_pdf(doc_path)
            if fallback_text and len(fallback_text) > 100:
                document_text = fallback_text
                analysis = await openai_service.analyze_document(transcript, document_text)
                confidence = parse_confidence(analysis.get("confidence"))
                logger.info("Used fallback PDF OCR, new confidence: %s", confidence)
            if confidence < config.CONFIDENCE_THRESHOLD:
                await status_msg.delete()
                await bot.send_message(user_id, LOW_CONFIDENCE_MSG)
                return

        doc_type = analysis.get("document_type", "документ")
        user_task = analysis.get("user_task", transcript[:200])
        # Поддержка issues_found (список dict с description/priority или строк) и issues (старый формат)
        issues_raw = analysis.get("issues_found")
        if issues_raw is None:
            issues_raw = analysis.get("issues", [])
        issues = []
        for x in issues_raw:
            if isinstance(x, dict):
                desc = x.get("description", str(x))
                prio = x.get("priority", "")
                issues.append(f"[{prio}] {desc}" if prio else desc)
            else:
                issues.append(str(x))

        # 4. Пост-обработка
        sections = await openai_service.generate_response_sections(
            analysis_json=analysis,
            document_type=doc_type,
            user_task=user_task,
            issues_list=issues,
        )
        text_report = sections.get("text_report", "Отчёт не сформирован.")
        tts_script = sections.get("tts_script", "Анализ завершён.")
        checklist_text = sections.get("checklist", "□ Результаты анализа")

        # 5. TTS
        tts_bytes = await tts_service.text_to_speech(tts_script)

        # 6. Чек-лист
        checklist_bytes = generate_checklist(checklist_text, output_format=checklist_format)
        checklist_ext = "pdf" if checklist_format == "pdf" else "png"

        # 7. Отправка
        await status_msg.edit_text("📤 Отправляю результат…")
        # Текстовый отчёт (если Markdown вызывает ошибку — отправляем без форматирования)
        report_text = text_report[:4000]
        try:
            await bot.send_message(user_id, report_text, parse_mode="Markdown")
        except Exception:
            await bot.send_message(user_id, report_text, parse_mode=None)
        # Голосовое резюме
        voice_input = BufferedInputFile(tts_bytes, filename="resume.mp3")
        await bot.send_voice(user_id, voice=voice_input)
        # Файл чек-листа
        checklist_input = BufferedInputFile(checklist_bytes, filename=f"checklist.{checklist_ext}")
        await bot.send_document(user_id, document=checklist_input, caption="📋 Чек-лист")

        await status_msg.delete()

    except Exception as e:
        log_failure(logger, "pipeline", e)
        await bot.send_message(user_id, ERROR_MSG)


async def _reply(message: Message, text: str) -> None:
    """Ответ пользователю, который никогда не бросает исключение: отправка не должна мешать очистке."""
    try:
        await message.answer(text)
    except Exception as e:
        log_failure(logger, "send_message", e)


async def _download(bot: Bot, file_id: str, dest: Path) -> bool:
    """Скачивает файл Telegram в dest. При ошибке логирует только класс исключения и убирает частичный файл."""
    try:
        tg_file = await bot.get_file(file_id)
        await bot.download_file(tg_file.file_path, dest)
    except Exception as e:
        log_failure(logger, "download", e)
        with suppress(OSError):
            dest.unlink(missing_ok=True)
        return False
    return True


async def _start_or_replace_session(
    message: Message, state: FSMContext, bot: Bot, data: dict,
    file_id: str, is_voice: bool, doc_ext: str,
) -> None:
    """Первый файл сессии или замена ожидающего файла того же типа: всегда в новую рабочую директорию."""
    workspace = SessionWorkspace.create()
    incoming = workspace.voice_path if is_voice else workspace.document_path(doc_ext)
    previous = data.get(WORKSPACE_KEY)
    adopted = False  # True, когда рабочей директорией владеет FSM
    try:
        if not await _download(bot, file_id, incoming):
            await _reply(message, DOWNLOAD_ERROR_MSG)
            return
        await state.set_state(
            UserSessionState.waiting_for_document if is_voice else UserSessionState.waiting_for_voice
        )
        await state.set_data({
            WORKSPACE_KEY: str(workspace.path),
            PENDING_FILE_KEY: str(incoming),
            STARTED_AT_KEY: time.time(),
        })
        adopted = True
    finally:
        if not adopted:
            workspace.cleanup()
    if previous:
        SessionWorkspace(Path(previous)).cleanup()
    await _reply(message, WAIT_DOC_MSG if is_voice else WAIT_VOICE_MSG)


async def _complete_session(
    message: Message, state: FSMContext, bot: Bot, data: dict,
    file_id: str, is_voice: bool, doc_ext: str,
) -> None:
    """Пришла вторая половина: скачать, запустить пайплайн. Рабочая директория удаляется в finally."""
    workspace_dir = data.get(WORKSPACE_KEY)
    pending_file = data.get(PENDING_FILE_KEY)
    if not (workspace_dir and pending_file and Path(pending_file).is_file()):
        await discard_session(state)
        await _reply(message, ERROR_MSG)
        return

    workspace = SessionWorkspace(Path(workspace_dir))
    incoming = workspace.voice_path if is_voice else workspace.document_path(doc_ext)
    if not await _download(bot, file_id, incoming):
        # Ожидающая половина остаётся в сессии: достаточно отправить файл ещё раз.
        await _reply(message, DOWNLOAD_ERROR_MSG)
        return

    voice_path, doc_path = (incoming, Path(pending_file)) if is_voice else (Path(pending_file), incoming)
    try:
        await state.clear()
        await run_pipeline(
            bot,
            message.from_user.id if message.from_user else 0,
            voice_path,
            doc_path,
            OpenAIService(api_key=config.OPENAI_API_KEY),
            TTSService(openai_api_key=config.OPENAI_API_KEY),
            config.CHECKLIST_FORMAT,
        )
    except Exception as e:
        # run_pipeline сам сообщает об ошибках; сюда попадаем, если не удалась и эта отправка.
        log_failure(logger, "session", e)
        await _reply(message, ERROR_MSG)
    finally:
        workspace.cleanup()


async def _receive_input(
    message: Message, state: FSMContext, bot: Bot, file_id: str, is_voice: bool, doc_ext: str = "",
) -> None:
    """Общая логика для голоса и документа/фото: связывает их в сессию в любом порядке."""
    data = await state.get_data()
    current = await state.get_state()

    if current and session_expired(data):
        await discard_session(state)
        await _reply(message, SESSION_EXPIRED_MSG)
        data, current = {}, None

    # Голос завершает сессию, начатую документом, и наоборот.
    completes_with = UserSessionState.waiting_for_voice if is_voice else UserSessionState.waiting_for_document
    handle = _complete_session if current == completes_with.state else _start_or_replace_session
    await handle(message, state, bot, data, file_id, is_voice, doc_ext)


@router.message(F.voice)
async def handle_voice(message: Message, state: FSMContext, bot: Bot) -> None:
    """Обработка голосового сообщения."""
    await _receive_input(message, state, bot, message.voice.file_id, is_voice=True)


@router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    """Обработка фото (последнее изображение в медиа-группе)."""
    if not message.photo:
        return
    await _receive_input(message, state, bot, message.photo[-1].file_id, is_voice=False, doc_ext=".jpg")


@router.message(F.document)
async def handle_document(message: Message, state: FSMContext, bot: Bot) -> None:
    """Обработка документа (PDF, изображение)."""
    if not message.document:
        return
    doc = message.document
    ext = Path(doc.file_name or "").suffix.lower()
    if ext not in ALLOWED_DOC_EXTENSIONS:
        await _reply(message, "Отправьте PDF или изображение (JPG, PNG).")
        return
    await _receive_input(message, state, bot, doc.file_id, is_voice=False, doc_ext=ext)
