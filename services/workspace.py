"""
Рабочая директория сессии: одна приватная временная папка на одно незавершённое
взаимодействие (голос + документ). Создаётся при первом входящем файле и удаляется целиком
при завершении, замене, /start, истечении сессии или ошибке.
"""
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Приложение создаёт и удаляет только директории с этим префиксом.
WORKSPACE_PREFIX = "tgdoc-session-"


@dataclass(frozen=True)
class SessionWorkspace:
    path: Path

    @classmethod
    def create(cls) -> "SessionWorkspace":
        """Новая директория со случайным именем (tempfile.mkdtemp), не зависящим от user/chat id."""
        return cls(Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX)))

    @property
    def voice_path(self) -> Path:
        return self.path / "voice.ogg"

    def document_path(self, ext: str) -> Path:
        """Расширение нужно пайплайну, чтобы отличить PDF от изображения."""
        return self.path / f"document{ext}"

    def cleanup(self) -> None:
        """Рекурсивно удаляет всю директорию. Идемпотентно и не бросает исключений."""
        if not self.path.name.startswith(WORKSPACE_PREFIX):
            logger.warning("Refusing to remove a directory that is not a session workspace")
            return
        try:
            shutil.rmtree(self.path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Workspace cleanup failed: %s", type(exc).__name__)


def purge_stale_workspaces(max_age_seconds: float) -> int:
    """
    Удаляет директории сессий старше max_age_seconds (остались от упавшего процесса).
    Трогает только директории с WORKSPACE_PREFIX во временной папке. Возвращает число удалённых.
    """
    cutoff = time.time() - max_age_seconds
    removed = 0
    try:
        candidates = [p for p in Path(tempfile.gettempdir()).glob(f"{WORKSPACE_PREFIX}*") if p.is_dir()]
    except OSError as exc:
        logger.warning("Stale workspace scan failed: %s", type(exc).__name__)
        return 0
    for path in candidates:
        try:
            if path.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        SessionWorkspace(path).cleanup()
        if not path.exists():
            removed += 1
    return removed
