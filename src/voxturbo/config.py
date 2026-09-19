"""Validated settings. No transcription or clipboard content is persisted."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from voxturbo.audio import DEFAULT_RECORDING_SECONDS

LEGACY_DIR_NAME = "DeltaEcho"


def _migrate_legacy_data_dir(local: Path, target: Path) -> None:
    """Одноразовый перенос каталога данных прежнего имени.

    Приложение ранее называлось DeltaEcho и хранило скачанную модель рядом с
    настройками. Переименование каталога избавляет от повторной загрузки 639 МиБ.
    Переносится только когда целевого каталога ещё нет; при любой ошибке каталог
    остаётся на месте, и приложение просто работает с пустым новым.
    """
    legacy = local / LEGACY_DIR_NAME
    if target.exists() or not legacy.is_dir():
        return
    try:
        legacy.rename(target)
    except OSError:
        return


def data_dir() -> Path:
    override = os.environ.get("VOXTURBO_DATA_DIR")
    if override:
        return Path(override).resolve()
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    target = local / "VoxTurbo"
    _migrate_legacy_data_dir(local, target)
    return target


@dataclass(frozen=True)
class Settings:
    schema_version: int = 1
    hotkey: str = "F4"
    paste_shortcut: str = "Ctrl+V"
    restore_delay_ms: int = 700
    microphone: str | None = None
    show_widget: bool = True
    max_recording_seconds: int = DEFAULT_RECORDING_SECONDS

    def validated(self) -> Settings:
        from voxturbo.audio import MAX_RECORDING_SECONDS, MIN_RECORDING_SECONDS
        from voxturbo.platform.windows import parse_hotkey

        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Версия настроек не поддерживается.")
        if not isinstance(self.hotkey, str):
            raise ValueError("Неверная горячая клавиша.")
        parse_hotkey(self.hotkey)
        if self.paste_shortcut not in ("Ctrl+V", "Shift+Insert"):
            raise ValueError("Выберите Ctrl+V или Shift+Insert.")
        if type(self.restore_delay_ms) is not int or not 200 <= self.restore_delay_ms <= 3000:
            raise ValueError("Задержка восстановления: от 200 до 3000 мс.")
        if self.microphone is not None and not isinstance(self.microphone, str):
            raise ValueError("Неверный микрофон.")
        if type(self.show_widget) is not bool:
            raise ValueError("Неверное состояние виджета.")
        if (
            type(self.max_recording_seconds) is not int
            or not MIN_RECORDING_SECONDS <= self.max_recording_seconds <= MAX_RECORDING_SECONDS
        ):
            raise ValueError(f"Длина диктовки: от {MIN_RECORDING_SECONDS} до {MAX_RECORDING_SECONDS} секунд.")
        return self


def load_settings(path: Path) -> tuple[Settings, str | None]:
    if not path.exists():
        return Settings(), None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Настройки должны быть объектом JSON.")
        known = Settings.__dataclass_fields__
        settings = Settings(**{key: value for key, value in raw.items() if key in known}).validated()
        return settings, None
    except (OSError, ValueError, TypeError) as exc:
        # Leave the original untouched; a subsequent explicit save is allowed to replace it.
        return Settings(), f"Не удалось прочитать настройки. Используются стандартные. {exc}"


def save_settings(path: Path, settings: Settings) -> None:
    settings.validated()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(asdict(settings), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
