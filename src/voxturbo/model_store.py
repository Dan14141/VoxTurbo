"""Download the pinned model on explicit request, with size and SHA-256 checks."""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

MODEL_ID = "parakeet-v3-int8"
MODEL_REVISION = "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce"
MODEL_REPOSITORY = "istupakov/parakeet-tdt-0.6b-v3-onnx"
MODEL_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
_BLOCK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/{MODEL_REVISION}/{self.name}"


MODEL_FILES = (
    ModelFile("config.json", 97, "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466"),
    ModelFile("vocab.txt", 93939, "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d"),
    ModelFile(
        "encoder-model.int8.onnx",
        652183999,
        "6139d2fa7e1b086097b277c7149725edbab89cc7c7ae64b23c741be4055aff09",
    ),
    ModelFile(
        "decoder_joint-model.int8.onnx",
        18202004,
        "eea7483ee3d1a30375daedc8ed83e3960c91b098812127a0d99d1c8977667a70",
    ),
)
MODEL_SIZE_BYTES = sum(item.size for item in MODEL_FILES)


class ModelError(RuntimeError):
    """An incomplete, inaccessible, or invalid model installation."""


class ModelDownloadCancelled(ModelError):
    """An explicit cancellation; never a successful installation."""


class _HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if urlparse(newurl).scheme != "https":
            raise ModelError("Сервер предложил незащищённую переадресацию загрузки.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ModelStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._download_lock = threading.Lock()

    def is_ready(self) -> bool:
        """Fast presence/size check for UI; engine uses full verify before load."""
        try:
            return all(
                (path := self.root / item.name).is_file()
                and not path.is_symlink()
                and path.stat().st_size == item.size
                for item in MODEL_FILES
            )
        except OSError:
            return False

    @staticmethod
    def _validate_file(path: Path, item: ModelFile, cancel_event: threading.Event | None = None) -> None:
        if not path.is_file() or path.is_symlink():
            raise ModelError(f"Не найден файл модели: {item.name}. Загрузите модель.")
        if path.stat().st_size != item.size:
            raise ModelError(f"Неверный размер файла модели {item.name}. Повторите загрузку.")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while block := source.read(_BLOCK_SIZE):
                if cancel_event is not None and cancel_event.is_set():
                    raise ModelDownloadCancelled("Загрузка модели отменена.")
                digest.update(block)
        if digest.hexdigest() != item.sha256:
            raise ModelError(f"Проверка SHA-256 не пройдена: {item.name}. Повторите загрузку.")

    def verify(self) -> None:
        """Verify all model bytes; call only in a worker, not the UI thread."""
        try:
            for item in MODEL_FILES:
                self._validate_file(self.root / item.name, item)
        except OSError as exc:
            raise ModelError(f"Не удалось прочитать модель: {exc}") from exc

    def download(self, progress_callback: Callable[[int, int], None], cancel_event: threading.Event) -> None:
        """Download missing/broken files. Valid files survive cancellation or failure."""
        if not self._download_lock.acquire(blocking=False):
            raise ModelError("Загрузка модели уже идёт.")
        try:
            self._download(progress_callback, cancel_event)
        except OSError as exc:
            raise ModelError(f"Не удалось загрузить модель: {exc}") from exc
        finally:
            self._download_lock.release()

    def _download(self, progress: Callable[[int, int], None], cancel: threading.Event) -> None:
        if cancel.is_set():
            raise ModelDownloadCancelled("Загрузка модели отменена.")
        self.root.mkdir(parents=True, exist_ok=True)
        valid: set[str] = set()
        for item in MODEL_FILES:
            try:
                self._validate_file(self.root / item.name, item, cancel)
                valid.add(item.name)
            except ModelDownloadCancelled:
                raise
            except ModelError:
                pass
        completed = sum(item.size for item in MODEL_FILES if item.name in valid)
        progress(completed, MODEL_SIZE_BYTES)
        needed = MODEL_SIZE_BYTES - completed
        if needed and shutil.disk_usage(self.root).free < needed + 32 * 1024 * 1024:
            raise ModelError(
                "Недостаточно свободного места для модели. Освободите место и повторите загрузку."
            )
        opener = urllib.request.build_opener(_HTTPSRedirectHandler())
        for item in MODEL_FILES:
            if cancel.is_set():
                raise ModelDownloadCancelled("Загрузка модели отменена.")
            if item.name in valid:
                continue
            destination = self.root / item.name
            temporary = self.root / f"{item.name}.part"
            if destination.is_symlink() or temporary.is_symlink():
                raise ModelError(f"Недопустимая символическая ссылка в каталоге модели: {item.name}.")
            try:
                for attempt in range(3):
                    try:
                        # Remove only our partial directory entry before exclusive
                        # creation; never truncate a pre-existing hard link.
                        temporary.unlink(missing_ok=True)
                        request = urllib.request.Request(
                            item.url, headers={"User-Agent": "VoxTurbo/0.1 model-downloader"}
                        )
                        digest = hashlib.sha256()
                        received = 0
                        with opener.open(request, timeout=30) as response, temporary.open("xb") as target:
                            if urlparse(response.geturl()).scheme != "https":
                                raise ModelError("Загрузка модели требует HTTPS.")
                            while True:
                                if cancel.is_set():
                                    raise ModelDownloadCancelled("Загрузка модели отменена.")
                                block = response.read(_BLOCK_SIZE)
                                if not block:
                                    break
                                received += len(block)
                                if received > item.size:
                                    raise ModelError(f"Сервер вернул неверный размер файла {item.name}.")
                                target.write(block)
                                digest.update(block)
                                progress(completed + received, MODEL_SIZE_BYTES)
                            target.flush()
                            os.fsync(target.fileno())
                        if received != item.size or digest.hexdigest() != item.sha256:
                            raise ModelError(
                                f"Файл {item.name} не прошёл проверку размера/SHA-256. Повторите загрузку."
                            )
                        if cancel.is_set():
                            raise ModelDownloadCancelled("Загрузка модели отменена.")
                        os.replace(temporary, destination)
                        completed += item.size
                        progress(completed, MODEL_SIZE_BYTES)
                        break
                    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                        if attempt == 2:
                            raise ModelError(
                                f"Нет связи с сервером моделей. Проверьте подключение: {exc}"
                            ) from exc
                        # Event.wait is interruptible, unlike an unconditional retry sleep.
                        if cancel.wait(0.5 * (attempt + 1)):
                            raise ModelDownloadCancelled("Загрузка модели отменена.") from exc
            finally:
                temporary.unlink(missing_ok=True)
