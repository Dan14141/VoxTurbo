"""Bounded microphone capture; audio is kept only in memory."""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

MIN_RECORDING_SECONDS = 5
DEFAULT_RECORDING_SECONDS = 30
# Жёсткий потолок: значение выше него не принимает ни настройка, ни запись.
MAX_RECORDING_SECONDS = 60
PREFERRED_SAMPLE_RATE = 16_000
SUPPORTED_SAMPLE_RATES = (8_000, 11_025, 16_000, 22_050, 24_000, 32_000, 44_100, 48_000)


class AudioError(RuntimeError):
    """A recoverable microphone or audio-input problem."""


@dataclass(frozen=True)
class AudioClip:
    samples: npt.NDArray[np.float32]
    sample_rate: int

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate > 0 else 0.0


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    host_api: str


def _get_backend() -> Any:
    return importlib.import_module("sounddevice")


def list_input_devices() -> list[InputDevice]:
    """Return current PortAudio input indexes and stable human-readable names."""
    try:
        backend = _get_backend()
        apis = backend.query_hostapis()
        return [
            InputDevice(index, device["name"], apis[device["hostapi"]]["name"])
            for index, device in enumerate(backend.query_devices())
            if device["max_input_channels"] > 0
        ]
    except Exception as exc:
        raise AudioError(f"Не удалось получить список микрофонов: {exc}") from exc


class AudioRecorder:
    """Capture mono PCM with a hard memory limit; callers stop after limit_reached."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: Any | None = None
        self._chunks: list[npt.NDArray[np.float32]] = []
        self._sample_rate = PREFERRED_SAMPLE_RATE
        self._frames = 0
        self._level = 0.0
        self._limit_reached = False
        self._limit_seconds = DEFAULT_RECORDING_SECONDS
        self._capture_error: str | None = None

    @property
    def elapsed(self) -> float:
        with self._lock:
            return self._frames / self._sample_rate

    @property
    def level(self) -> float:
        with self._lock:
            return self._level

    @property
    def limit_reached(self) -> bool:
        with self._lock:
            return self._limit_reached

    def start(self, device: int | None = None, limit_seconds: int = DEFAULT_RECORDING_SECONDS) -> None:
        if self._stream is not None:
            raise AudioError("Запись уже идёт.")
        if (
            type(limit_seconds) is not int
            or not MIN_RECORDING_SECONDS <= limit_seconds <= MAX_RECORDING_SECONDS
        ):
            raise AudioError(f"Длина диктовки: от {MIN_RECORDING_SECONDS} до {MAX_RECORDING_SECONDS} секунд.")
        stream = None
        try:
            backend = _get_backend()
            info = backend.query_devices(device, "input")
            if info["max_input_channels"] < 1:
                raise AudioError("Выбранное устройство не поддерживает запись.")
            sample_rate = PREFERRED_SAMPLE_RATE
            try:
                backend.check_input_settings(
                    device=device, channels=1, dtype="float32", samplerate=sample_rate
                )
            except Exception:
                sample_rate = round(info["default_samplerate"])
                if sample_rate not in SUPPORTED_SAMPLE_RATES:
                    raise AudioError(
                        "Микрофон не поддерживает 16 кГц, а его стандартная частота "
                        f"{sample_rate} Гц не поддерживается. Выберите другой микрофон."
                    ) from None
                backend.check_input_settings(
                    device=device, channels=1, dtype="float32", samplerate=sample_rate
                )
            with self._lock:
                self._chunks.clear()
                self._sample_rate = sample_rate
                self._frames = 0
                self._level = 0.0
                self._limit_reached = False
                self._limit_seconds = limit_seconds
                self._capture_error = None
            stream = backend.InputStream(
                device=device,
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                blocksize=0,
                callback=self._callback,
            )
            self._stream = stream
            stream.start()
        except Exception as exc:
            self._stream = None
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
            if isinstance(exc, AudioError):
                raise
            raise AudioError(f"Не удалось открыть микрофон. Проверьте доступ и устройство: {exc}") from exc

    def _callback(self, indata: Any, frames: int, _time: Any, status: Any) -> None:
        # No disk, Qt, inference or stream stop here. PortAudio owns the input buffer.
        with self._lock:
            if status:
                self._capture_error = "Во время записи потеряны аудиоданные. Повторите диктовку."
            remaining = self._limit_seconds * self._sample_rate - self._frames
            count = min(frames, max(0, remaining))
            if count:
                chunk = np.array(indata[:count, 0], dtype=np.float32, copy=True)
                self._chunks.append(chunk)
                self._frames += len(chunk)
                rms = float(np.sqrt(np.mean(np.square(chunk))))
                self._level = min(1.0, rms) if np.isfinite(rms) else 0.0
            if self._frames >= self._limit_seconds * self._sample_rate:
                self._limit_reached = True

    def stop(self) -> AudioClip:
        stream, self._stream = self._stream, None
        if stream is None:
            raise AudioError("Сейчас запись не идёт.")
        failure: Exception | None = None
        try:
            stream.stop()
        except Exception as exc:
            failure = exc
        finally:
            try:
                stream.close()
            except Exception as exc:
                failure = failure or exc
        with self._lock:
            samples = np.concatenate(self._chunks) if self._chunks else np.empty(0, dtype=np.float32)
            self._chunks.clear()
            self._level = 0.0
            sample_rate = self._sample_rate
            capture_error = self._capture_error
        if failure is not None:
            raise AudioError(f"Не удалось завершить запись: {failure}") from failure
        if capture_error:
            raise AudioError(capture_error)
        return AudioClip(samples, sample_rate)

    def cancel(self) -> None:
        stream, self._stream = self._stream, None
        failure: Exception | None = None
        if stream is not None:
            try:
                stream.abort()
            except Exception as exc:
                failure = exc
            finally:
                try:
                    stream.close()
                except Exception as exc:
                    failure = failure or exc
        with self._lock:
            self._chunks.clear()
            self._frames = 0
            self._level = 0.0
            self._limit_reached = False
            self._capture_error = None
        if failure is not None:
            raise AudioError(f"Не удалось закрыть микрофон: {failure}") from failure
