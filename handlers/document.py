"""
Обработка голосовых сообщений и документов. Связывание в сессию и запуск пайплайна.
"""
import asyncio
import logging
import time
from contextlib import suppress
from pathlib import Path

from aiogram import Bot, F, Router
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
from services import limits
from services.ai_errors import AIFailure, AIServiceError
from services.checklist_generator import generate_checklist
from services.deterministic_checks import CheckStatus, DeterministicCheck, run_deterministic_checks
from services.document_extraction import METHOD_VISION, DocumentExtractionResult, extract_document, format_coverage
from services.grounding import ground_issues
from services.openai_client import AIServices
from services.openai_service import OpenAIService
from services.report import compose_report, compose_tts_script, format_automatic_checks
from services.schemas import ChecklistItem, ReportResult
from services.tts_service import TTSService
from services.validation import (
    Rejection,
    ValidatedDocument,
    exceeds_upload_limit,
    validate_document,
    validate_voice,
)
from services.workspace import SessionWorkspace
from utils.logging_config import log_failure

router = Router()
logger = logging.getLogger(__name__)

# Отсев очевидно лишнего ДО скачивания по тому, что заявил Telegram. Принимает файл не он: тип по-настоящему
# определяет services.validation по содержимому.
ALLOWED_DOC_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
MIME_TO_EXT = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

REPORT_CHAR_LIMIT = 4000  # с запасом до лимита Telegram в 4096; строка охвата и дисклеймер в него входят
# Файл чек-листа живёт отдельно от отчёта и может уйти дальше сам по себе, поэтому пометка о том, что это оценка
# модели (и результат кода), — в подписи, а в самом файле — под заголовком.
CHECKLIST_CAPTION = "📋 Чек-лист: автоматические проверки и предварительная оценка модели (не юридическая консультация)"

STATUS_PROCESSING = "⏳ Обрабатываю…"
STATUS_ARTIFACTS = "🎧 Отчёт отправлен. Готовлю голосовое резюме и чек-лист…"
# Необязательные файлы: их сбой не отменяет отчёт. Одно короткое сообщение на файл, без текста ошибки провайдера.
VOICE_FAILED_MSG = "Не удалось создать голосовое резюме; текстовый отчёт доступен выше."
CHECKLIST_FAILED_MSG = "Не удалось создать PDF-чеклист; основной отчёт уже сформирован."

ERROR_MSG = "Произошла ошибка при обработке. Попробуйте позже."
DOWNLOAD_ERROR_MSG = "Не удалось получить файл из Telegram. Отправьте его ещё раз."
SESSION_EXPIRED_MSG = "⌛ Предыдущая незавершённая сессия истекла, её файлы удалены. Начинаю новую."
WAIT_DOC_MSG = "✅ Голос получен. Отправь документ (PDF или изображение)."
WAIT_VOICE_MSG = "✅ Документ получен. Отправь голосовое сообщение с задачей."

# Исходы вызова ИИ (services.ai_errors). Ни одно сообщение не говорит о качестве документа: о нём сообщает только
# детерминированное извлечение (REJECTION_MESSAGES). Текста ошибки провайдера и модели здесь нет.
AI_FAILURE_MESSAGES: dict[AIFailure, str] = {
    AIFailure.CONFIG: "Сервис ИИ сейчас недоступен из-за ошибки настройки бота. Сообщите об этом администратору.",
    AIFailure.UNAVAILABLE: "Сервис ИИ временно недоступен или перегружен. Попробуйте позже.",
    AIFailure.TIMEOUT: "Сервис ИИ не ответил вовремя. Попробуйте ещё раз.",
    AIFailure.REFUSED: (
        "Модель отказалась обработать этот документ или запрос. "
        "Попробуйте другой документ или иначе сформулируйте задачу."
    ),
    AIFailure.TRUNCATED: "Ответ модели оборвался и получился неполным. Попробуйте ещё раз или отправьте документ покороче.",
    AIFailure.INVALID_OUTPUT: "Модель вернула ответ в непредусмотренном формате. Попробуйте ещё раз.",
}

# Постоянные отказы: дело в самом файле, поэтому нет «попробуйте позже», а есть что с ним сделать.
# Временные сбои OpenAI/Telegram идут отдельным путём (ERROR_MSG, DOWNLOAD_ERROR_MSG).
REJECTION_MESSAGES: dict[Rejection, str] = {
    Rejection.UNSUPPORTED_TYPE: "Не удалось распознать файл как PDF или изображение. Поддерживаются PDF, JPG, PNG и WEBP.",
    Rejection.EMPTY: "Файл пустой. Отправьте другой файл.",
    Rejection.TOO_LARGE: (
        f"Файл слишком большой: максимум {limits.MAX_UPLOAD_BYTES // (1024 * 1024)} МБ. "
        "Отправьте файл меньшего размера."
    ),
    Rejection.CORRUPT_PDF: "Не удалось прочитать PDF: файл повреждён или в нём нет страниц. Отправьте другой файл.",
    Rejection.ENCRYPTED_PDF: "PDF защищён паролем, такие файлы не поддерживаются. Снимите защиту и отправьте файл снова.",
    Rejection.TOO_MANY_PAGES: (
        f"В PDF слишком много страниц: максимум {limits.MAX_PDF_PAGES}. "
        "Отправьте нужные страницы отдельным файлом."
    ),
    Rejection.PAGE_SIZE: (
        "Размер страниц в PDF выходит за допустимые пределы. "
        "Сохраните документ в обычном формате (например, A4) и отправьте снова."
    ),
    Rejection.UNREADABLE_IMAGE: "Не удалось открыть изображение: файл повреждён или слишком мал. Отправьте другое изображение.",
    Rejection.IMAGE_SIZE: (
        f"Изображение слишком большое по разрешению: максимум {limits.MAX_IMAGE_PIXELS // 1_000_000} Мп. "
        "Уменьшите его и отправьте снова."
    ),
    Rejection.RENDER_FAILED: (
        "Не удалось подготовить страницы PDF к анализу. "
        "Отправьте документ другим файлом, например изображениями страниц."
    ),
    Rejection.RENDER_TIMEOUT: (
        "Обработка PDF заняла слишком много времени и была остановлена. "
        "Отправьте файл попроще: с меньшим числом страниц или изображениями страниц."
    ),
    Rejection.NO_TEXT: (
        "В документе не удалось найти читаемый текст. "
        "Отправьте более чёткий скан или фото либо PDF с текстовым слоем."
    ),
}


async def run_pipeline(
    bot: Bot,
    user_id: int,
    voice_path: Path,
    doc_path: Path,
    openai_service: OpenAIService,
    tts_service: TTSService,
) -> None:
    """
    Полный пайплайн: проверка файлов → извлечение документа → автоматические проверки реквизитов → транскрибация →
    анализ → пост-обработка → ОТЧЁТ → голосовое резюме → PDF-чек-лист. Проверка и извлечение идут до Whisper, поэтому
    за транскрибацию задачи к непригодному документу платить не приходится; сама проверка бесплатна и локальна.
    Автоматические проверки — тоже бесплатный локальный код по тексту документа: модель их не видит, и они не
    влияют на её анализ.

    Основной результат — текстовый отчёт: он отправляется первым, и после этого пайплайн уже не может «упасть» (см.
    _deliver_artifacts). Голос и чек-лист необязательны: сбой любого из них даёт одно короткое сообщение и не
    касается ни отчёта, ни другого файла.
    """
    try:
        status_msg = await bot.send_message(user_id, STATUS_PROCESSING)

        # 1. Проверка файлов (до любых платных вызовов; повторяет проверку при загрузке: run_pipeline самодостаточен)
        document = await asyncio.to_thread(_validate_inputs, doc_path, voice_path)
        if isinstance(document, Rejection):
            await _send_rejection(bot, user_id, status_msg, document)
            return

        # 2. Извлечение текста документа: текстовый слой, Vision только для страниц без него
        extraction = await extract_document(document, openai_service.extract_text_from_image)
        if isinstance(extraction, Rejection):
            await _send_rejection(bot, user_id, status_msg, extraction)
            return
        logger.info(
            "Document text: %d chars, pages %d of %d",
            len(extraction.extracted_text), len(extraction.analysed_pages), extraction.total_pages,
        )

        # 3. Автоматические проверки реквизитов: код по тексту документа, не по ответам модели. Не блокируют анализ.
        automatic_section, checks = _run_automatic_checks(extraction)

        # 4. Транскрибация голоса
        transcript = await openai_service.transcribe_voice(voice_path)
        logger.info("Transcribed: %d chars", len(transcript))

        # 5. Анализ: типизированный результат модели. Сбой формата, отказ и обрыв — это AIServiceError, а не «плохой документ».
        analysis = await openai_service.analyze_document(transcript, extraction.analysis_text)
        # Цитаты проверяются по тексту документа (без служебной пометки об охвате, которую добавляет код).
        findings = ground_issues(analysis.issues, extraction.extracted_text)
        logger.info(
            "Analysis: %d issues, %d with a verified quote",
            len(findings), sum(1 for finding in findings if finding.verified_evidence),
        )

        # 6. Пост-обработка: отчёт, текст озвучки и пункты чек-листа приходят структурой, а не разбираются из текста.
        report = await openai_service.generate_report(task=transcript, analysis=analysis)

        # 7. Отчёт — основной результат. Раздел автоматических проверок, заголовок «оценка модели», цитаты, строка охвата
        # и дисклеймер формируются кодом и добавляются после сокращения текста модели, поэтому не могут потеряться.
        # Текст уходит как есть, без режима разметки: ни одна строка модели или документа не может его сломать.
        report_text = compose_report(
            report.text_report, findings, format_coverage(extraction), limit=REPORT_CHAR_LIMIT,
            automatic=automatic_section,
        )
        await bot.send_message(user_id, report_text)

        # 8. Голос и чек-лист: после отчёта, каждый сам по себе и без права сорвать пайплайн.
        await _deliver_artifacts(
            bot, user_id, status_msg, tts_service, report,
            checks=checks, ocr_used=METHOD_VISION in extraction.extraction_methods,
        )

    except AIServiceError as e:
        logger.error("pipeline failed: AI service: %s", e)  # исход и класс исходной ошибки, без текста провайдера/модели
        await bot.send_message(user_id, AI_FAILURE_MESSAGES[e.kind])
    except Exception as e:
        log_failure(logger, "pipeline", e)
        await bot.send_message(user_id, ERROR_MSG)


def _run_automatic_checks(extraction: DocumentExtractionResult) -> tuple[str, list[DeterministicCheck]]:
    """
    Раздел отчёта с автоматическими проверками реквизитов (готовый текст) и сами результаты, из которых строится
    раздел чек-листа. Вход — только текст документа; результаты модель не видит. Это необязательный слой: его
    внутренний сбой не лишает пользователя оценки модели, но об этом сообщается в отчёте. В лог идут только
    количества, не значения реквизитов.
    """
    try:
        checks = run_deterministic_checks(extraction.extracted_text, analysed_pages=extraction.analysed_pages)
    except Exception as e:
        log_failure(logger, "automatic_checks", e)
        return format_automatic_checks(None), []
    logger.info(
        "Automatic checks: %d, failed %d", len(checks), sum(1 for check in checks if check.status is CheckStatus.FAIL)
    )
    return format_automatic_checks(checks, ocr_used=METHOD_VISION in extraction.extraction_methods), checks


async def _deliver_artifacts(
    bot: Bot, user_id: int, status_msg, tts_service: TTSService, report: ReportResult,
    *, checks: list[DeterministicCheck], ocr_used: bool,
) -> None:
    """
    Необязательные файлы после отчёта. Не бросает исключений: отчёт к этому моменту уже доставлен, и ничто здесь не
    должно превратиться в «Произошла ошибка» поверх него. Каждый файл — отдельно: сбой голоса не мешает чек-листу.
    """
    await _set_status(status_msg, STATUS_ARTIFACTS)
    await _send_voice_summary(bot, user_id, tts_service, report.tts_script, checks)
    await _send_checklist(bot, user_id, report.checklist_items, checks, ocr_used)
    await _delete_status(status_msg)


async def _send_voice_summary(
    bot: Bot, user_id: int, tts_service: TTSService, model_script: str, checks: list[DeterministicCheck]
) -> None:
    try:
        # вслух — одна фраза о том, что автоматические проверки что-то выявили, а не сами проверки
        failed = any(check.status is CheckStatus.FAIL for check in checks)
        audio = await tts_service.text_to_speech(compose_tts_script(model_script, automatic_failed=failed))
        await bot.send_voice(user_id, voice=BufferedInputFile(audio, filename="resume.mp3"))
    except Exception as e:  # синтез, сеть, Telegram: для пользователя это одно и то же — голоса нет
        log_failure(logger, "voice", e)
        await _notify(bot, user_id, VOICE_FAILED_MSG)


async def _send_checklist(
    bot: Bot, user_id: int, items: list[ChecklistItem], checks: list[DeterministicCheck], ocr_used: bool
) -> None:
    try:
        # reportlab блокирует поток, поэтому PDF строится в отдельном потоке
        pdf = await asyncio.to_thread(generate_checklist, items, checks, ocr_used=ocr_used)
        await bot.send_document(user_id, document=BufferedInputFile(pdf, filename="checklist.pdf"), caption=CHECKLIST_CAPTION)
    except Exception as e:
        log_failure(logger, "checklist", e)
        await _notify(bot, user_id, CHECKLIST_FAILED_MSG)


async def _notify(bot: Bot, user_id: int, text: str) -> None:
    """Короткое сообщение о сбое необязательного файла; сам его сбой не должен ничего ломать."""
    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        log_failure(logger, "notice", e)


async def _set_status(status_msg, text: str) -> None:
    with suppress(Exception):  # статус «Обрабатываю…» — украшение, его сбой не должен ничего ломать
        await status_msg.edit_text(text)


async def _delete_status(status_msg) -> None:
    with suppress(Exception):
        await status_msg.delete()


def _validate_inputs(doc_path: Path, voice_path: Path) -> ValidatedDocument | Rejection:
    """Бесплатные локальные проверки обеих половин сессии (блокирующая: вызывать через asyncio.to_thread)."""
    document = validate_document(doc_path)
    if isinstance(document, Rejection):
        return document
    voice_rejection = validate_voice(voice_path)
    return document if voice_rejection is None else voice_rejection


async def _send_rejection(bot: Bot, user_id: int, status_msg, reason: Rejection) -> None:
    logger.info("Input rejected: %s", reason.value)
    await bot.send_message(user_id, REJECTION_MESSAGES[reason])
    await _delete_status(status_msg)  # его сбой не должен заменять причину отказа


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


async def _accept_upload(message: Message, path: Path, is_voice: bool) -> bool:
    """
    Проверка сразу после скачивания: непригодный файл отклоняется немедленно, а не после ожидания второй половины.
    Платных вызовов здесь нет. При отказе пользователю сообщается причина, а сессия остаётся как была.
    """
    if is_voice:
        rejection = await asyncio.to_thread(validate_voice, path)
    else:
        outcome = await asyncio.to_thread(validate_document, path)
        rejection = outcome if isinstance(outcome, Rejection) else None
    if rejection is None:
        return True
    logger.info("Upload rejected: %s", rejection.value)
    await _reply(message, REJECTION_MESSAGES[rejection])
    return False


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
        if not await _accept_upload(message, incoming, is_voice):
            return  # отклонённый файл не заменяет уже ожидающий: прежняя сессия остаётся нетронутой
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
    file_id: str, is_voice: bool, doc_ext: str, ai: AIServices,
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
    if not await _accept_upload(message, incoming, is_voice):
        with suppress(OSError):
            incoming.unlink(missing_ok=True)  # ожидающая половина остаётся: достаточно прислать другой файл
        return

    voice_path, doc_path = (incoming, Path(pending_file)) if is_voice else (Path(pending_file), incoming)
    try:
        await state.clear()
        await run_pipeline(
            bot,
            message.from_user.id if message.from_user else 0,
            voice_path,
            doc_path,
            ai.openai,
            ai.tts,
        )
    except Exception as e:
        # run_pipeline сам сообщает об ошибках; сюда попадаем, если не удалась и эта отправка.
        log_failure(logger, "session", e)
        await _reply(message, ERROR_MSG)
    finally:
        workspace.cleanup()


async def _receive_input(
    message: Message, state: FSMContext, bot: Bot, ai: AIServices, file_id: str, is_voice: bool, doc_ext: str = "",
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
    if current == completes_with.state:
        await _complete_session(message, state, bot, data, file_id, is_voice, doc_ext, ai)
    else:
        await _start_or_replace_session(message, state, bot, data, file_id, is_voice, doc_ext)


@router.message(F.voice)
async def handle_voice(message: Message, state: FSMContext, bot: Bot, ai: AIServices) -> None:
    """Обработка голосового сообщения."""
    if exceeds_upload_limit(message.voice.file_size):
        await _reply(message, REJECTION_MESSAGES[Rejection.TOO_LARGE])
        return
    await _receive_input(message, state, bot, ai, message.voice.file_id, is_voice=True)


@router.message(F.photo)
async def handle_photo(message: Message, state: FSMContext, bot: Bot, ai: AIServices) -> None:
    """Обработка фото (последнее изображение в медиа-группе)."""
    if not message.photo:
        return
    await _receive_input(message, state, bot, ai, message.photo[-1].file_id, is_voice=False, doc_ext=".jpg")


@router.message(F.document)
async def handle_document(message: Message, state: FSMContext, bot: Bot, ai: AIServices) -> None:
    """Обработка документа (PDF, изображение)."""
    if not message.document:
        return
    doc = message.document
    ext = Path(doc.file_name or "").suffix.lower()
    if ext not in ALLOWED_DOC_EXTENSIONS:
        ext = MIME_TO_EXT.get((doc.mime_type or "").lower(), "")
    if not ext:
        await _reply(message, REJECTION_MESSAGES[Rejection.UNSUPPORTED_TYPE])
        return
    if exceeds_upload_limit(doc.file_size):  # заявленный размер известен до скачивания
        await _reply(message, REJECTION_MESSAGES[Rejection.TOO_LARGE])
        return
    await _receive_input(message, state, bot, ai, doc.file_id, is_voice=False, doc_ext=ext)
