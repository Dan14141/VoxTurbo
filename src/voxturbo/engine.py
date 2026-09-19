"""A local-only, lazy Parakeet adapter. No Qt or network calls."""

from __future__ import annotations

import importlib
import os
import threading
from typing import Any

import numpy as np

from voxturbo.audio import MAX_RECORDING_SECONDS, SUPPORTED_SAMPLE_RATES, AudioClip
from voxturbo.model_store import ModelStore


class EngineError(RuntimeError):
    """A recoverable ASR input/runtime problem."""


class ParakeetEngine:
    def __init__(self, store: ModelStore) -> None:
        self.store = store
        self._model: Any | None = None
        self._lock = threading.RLock()

    def load(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            self.store.verify()
            try:
                ort = importlib.import_module("onnxruntime")
                asr = importlib.import_module("onnx_asr")
                options = ort.SessionOptions()
                options.intra_op_num_threads = min(4, os.cpu_count() or 1)
                options.inter_op_num_threads = 1
                # A generic model TYPE has no Hub repository mapping. Even if the
                # validated directory disappears, the loader cannot download weights.
                self._model = asr.load_model(
                    "nemo-conformer-tdt",
                    self.store.root,
                    quantization="int8",
                    providers=["CPUExecutionProvider"],
                    sess_options=options,
                )
            except Exception as exc:
                raise EngineError(f"Не удалось загрузить локальную модель распознавания: {exc}") from exc

    def transcribe(self, clip: AudioClip) -> str:
        samples = clip.samples
        if not isinstance(samples, np.ndarray) or samples.dtype != np.float32 or samples.ndim != 1:
            raise EngineError("Для распознавания требуется одномерный аудиобуфер float32.")
        if clip.sample_rate not in SUPPORTED_SAMPLE_RATES:
            raise EngineError(f"Частота {clip.sample_rate} Гц не поддерживается моделью.")
        if len(samples) > MAX_RECORDING_SECONDS * clip.sample_rate:
            raise EngineError(f"Диктовка превышает предел {MAX_RECORDING_SECONDS} секунд.")
        if not np.all(np.isfinite(samples)):
            raise EngineError("Аудиозапись содержит повреждённые значения. Повторите запись.")
        if len(samples) and float(np.max(np.abs(samples))) > 1.00001:
            raise EngineError("Некорректная громкость аудиобуфера: требуется PCM в диапазоне от -1 до 1.")
        # This conservative guard rejects digital silence, not ordinary quiet speech.
        if clip.duration < 0.15 or float(np.sqrt(np.mean(np.square(samples)))) < 0.0001:
            return ""
        with self._lock:
            self.load()
            try:
                result = self._model.recognize(np.ascontiguousarray(samples), sample_rate=clip.sample_rate)
                if not isinstance(result, str):
                    raise EngineError("Модель вернула неподдерживаемый формат результата.")
                return result.strip()
            except EngineError:
                raise
            except Exception as exc:
                raise EngineError(f"Не удалось распознать запись: {exc}") from exc
