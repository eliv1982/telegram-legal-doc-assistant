"""
Обработка голосовых сообщений и документов. Связывание в сессию и запуск пайплайна.
"""
import logging
import tempfile
from pathlib import Path

from aiogram import Bot, F, Router

import config
from aiogram.types import BufferedInputFile, Message
from aiogram.fsm.context import FSMContext

from states.user_states import UserSessionState
from services.openai_service import OpenAIService
from services.tts_service import TTSService
from services.pdf_converter import pdf_first_page_to_image, image_to_bytes, extract_text_from_pdf
from services.checklist_generator import generate_checklist
from utils.helpers import parse_confidence

router = Router()
logger = logging.getLogger(__name__)

# Расширения для документов
ALLOWED_DOC_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_MIME_IMAGE = {"image/jpeg", "image/png", "image/webp"}

ERROR_MSG = "Произошла ошибка при обработке. Попробуйте позже."
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
    tts_provider: str,
    checklist_format: str,
) -> None:
    """
    Полный пайплайн: транскрибация → OCR → анализ → пост-обработка → TTS → чек-лист → отправка.
    """
    temp_files: list[Path] = []
    try:
        status_msg = await bot.send_message(user_id, "⏳ Обрабатываю…")
        # 1. Транскрибация голоса
        transcript = await openai_service.transcribe_voice(voice_path)
        logger.info("Transcribed: %s", transcript[:100])

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
        voice_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        voice_file.write(tts_bytes)
        voice_file.close()
        temp_files.append(Path(voice_file.name))

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
        logger.exception("Pipeline error: %s", e)
        await bot.send_message(user_id, ERROR_MSG)
    finally:
        for p in temp_files:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


@router.message(F.voice)
async def handle_voice(message: Message, state: FSMContext, bot: Bot) -> None:
    """Обработка голосового сообщения."""
    user_id = message.from_user.id if message.from_user else 0
    data = await state.get_data()
    current = await state.get_state()

    voice_file = await bot.get_file(message.voice.file_id)
    voice_path = Path(tempfile.gettempdir()) / f"voice_{user_id}_{message.voice.file_unique_id}.ogg"
    await bot.download_file(voice_file.file_path, voice_path)

    if current == UserSessionState.waiting_for_voice.state:
        # Уже ждём голос — документ был раньше
        doc_path = data.get("doc_path")
        if doc_path and Path(doc_path).exists():
            await state.clear()
            openai_svc = OpenAIService(api_key=config.OPENAI_API_KEY)
            tts_svc = TTSService(provider=config.TTS_PROVIDER, openai_api_key=config.OPENAI_API_KEY)
            await run_pipeline(bot, user_id, voice_path, Path(doc_path), openai_svc, tts_svc, config.TTS_PROVIDER, config.CHECKLIST_FORMAT)
            try:
                Path(doc_path).unlink(missing_ok=True)
            except OSError:
                pass
        else:
            await state.clear()
            await message.answer(ERROR_MSG)
    else:
        # Сохраняем голос и ждём документ
        await state.set_state(UserSessionState.waiting_for_document)
        await state.update_data(voice_path=str(voice_path))
        await message.answer(WAIT_DOC_MSG)


async def _process_document_file(
    message: Message, state: FSMContext, bot: Bot,
    doc_path: Path, user_id: int,
) -> None:
    """Общая логика обработки документа (из F.document или F.photo)."""
    data = await state.get_data()
    current = await state.get_state()

    if current == UserSessionState.waiting_for_document.state:
        voice_path_str = data.get("voice_path")
        if voice_path_str and Path(voice_path_str).exists():
            await state.clear()
            openai_svc = OpenAIService(api_key=config.OPENAI_API_KEY)
            tts_svc = TTSService(provider=config.TTS_PROVIDER, openai_api_key=config.OPENAI_API_KEY)
            await run_pipeline(bot, user_id, Path(voice_path_str), doc_path, openai_svc, tts_svc, config.TTS_PROVIDER, config.CHECKLIST_FORMAT)
            try:
                Path(voice_path_str).unlink(missing_ok=True)
            except OSError:
                pass
            try:
                doc_path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            await state.clear()
            await message.answer(ERROR_MSG)
    else:
        await state.set_state(UserSessionState.waiting_for_voice)
        await state.update_data(doc_path=str(doc_path))
        await message.answer(WAIT_VOICE_MSG)


@router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    """Обработка фото (последнее изображение в медиа-группе)."""
    if not message.photo:
        return
    user_id = message.from_user.id if message.from_user else 0
    largest = message.photo[-1]
    file = await bot.get_file(largest.file_id)
    ext = ".jpg"
    doc_path = Path(tempfile.gettempdir()) / f"doc_{user_id}_{largest.file_unique_id}{ext}"
    await bot.download_file(file.file_path, doc_path)
    await _process_document_file(message, state, bot, doc_path, user_id)


@router.message(F.document)
async def handle_document(message: Message, state: FSMContext, bot: Bot) -> None:
    """Обработка документа (PDF, изображение)."""
    if not message.document:
        return
    doc = message.document
    ext = Path(doc.file_name or "").suffix.lower()
    if ext not in ALLOWED_DOC_EXTENSIONS:
        await message.answer("Отправьте PDF или изображение (JPG, PNG).")
        return

    user_id = message.from_user.id if message.from_user else 0
    file = await bot.get_file(doc.file_id)
    doc_path = Path(tempfile.gettempdir()) / f"doc_{user_id}_{doc.file_unique_id}{ext}"
    await bot.download_file(file.file_path, doc_path)
    await _process_document_file(message, state, bot, doc_path, user_id)
