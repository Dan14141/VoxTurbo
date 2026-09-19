"""Application and explicit diagnostic command entry points."""

from __future__ import annotations

import argparse
import ctypes
import json
import platform
import sys
import time
from pathlib import Path

from voxturbo import __version__
from voxturbo.config import data_dir, load_settings


def write_report(path: Path | None, payload: dict):
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if sys.stdout is not None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def health() -> dict:
    import numpy
    import onnx_asr
    import onnxruntime
    import PySide6
    import sounddevice

    from voxturbo.platform.windows import INPUT

    if sys.platform != "win32" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("Для этой версии требуется Windows 10 x64.")
    if sys.getwindowsversion().build < 19045:
        raise RuntimeError("Для этой сборки требуется Windows 10 22H2 (19045) или новее.")
    if ctypes.sizeof(INPUT) != 40:
        raise RuntimeError("Неверная структура Win32 INPUT.")
    resources = Path(onnx_asr.__file__).parent / "preprocessors" / "data"
    if not list(resources.glob("resample_*.onnx")):
        raise RuntimeError("В сборке отсутствуют данные ресемплера ONNX.")
    sounddevice.get_portaudio_version()
    return {
        "ok": True,
        "version": __version__,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "qt": PySide6.__version__,
        "numpy": numpy.__version__,
        "onnxruntime": onnxruntime.__version__,
        "cpu_provider": "CPUExecutionProvider" in onnxruntime.get_available_providers(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "input_struct_bytes": ctypes.sizeof(INPUT),
    }


def transcribe_file(path: Path, model_dir: Path) -> dict:
    import wave

    import numpy as np

    from voxturbo.audio import MAX_RECORDING_SECONDS, SUPPORTED_SAMPLE_RATES, AudioClip
    from voxturbo.engine import ParakeetEngine
    from voxturbo.model_store import ModelStore

    with wave.open(str(path), "rb") as source:
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise ValueError("Для проверки выберите mono PCM16 WAV.")
        rate = source.getframerate()
        if rate not in SUPPORTED_SAMPLE_RATES or source.getnframes() > rate * MAX_RECORDING_SECONDS:
            raise ValueError(
                f"Выберите WAV с поддержанной частотой и длительностью до {MAX_RECORDING_SECONDS} секунд."
            )
        samples = (
            np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768
        )
    start = time.perf_counter()
    text = ParakeetEngine(ModelStore(model_dir)).transcribe(AudioClip(samples, rate))
    return {
        "ok": True,
        "text": text,
        "audio_seconds": len(samples) / rate,
        "elapsed_seconds": round(time.perf_counter() - start, 3),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="VoxTurbo — локальная диктовка для Windows 10")
    parser.add_argument("--self-test", action="store_true", help="Проверка библиотек без записи и вставки")
    parser.add_argument("--smoke-gui", action="store_true", help="Показать окно на 2 секунды и завершить")
    parser.add_argument("--report", type=Path, help="JSON-отчёт проверки")
    parser.add_argument("--data-dir", type=Path, help="Отдельный каталог данных для проверки")
    parser.add_argument("--model-dir", type=Path, help="Локальная модель для CLI-проверки")
    parser.add_argument("--transcribe", type=Path, help="Распознать выбранный mono PCM16 WAV (до 25 с)")
    args = parser.parse_args(argv)
    root = (args.data_dir or data_dir()).resolve()
    try:
        if args.self_test:
            write_report(args.report, health())
            return 0
        if args.transcribe:
            report = transcribe_file(args.transcribe, args.model_dir or root / "models" / "parakeet-v3-int8")
            write_report(args.report, report)
            return 0

        from PySide6.QtCore import QLockFile, QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox

        from voxturbo.controller import AppController
        from voxturbo.ui import STYLE, MainWindow, app_icon

        app = QApplication(sys.argv[:1])
        app.setApplicationName("VoxTurbo")
        app.setOrganizationName("VoxTurbo")
        app.setQuitOnLastWindowClosed(False)
        app.setStyle("Fusion")
        app.setStyleSheet(STYLE)
        app.setWindowIcon(app_icon())
        root.mkdir(parents=True, exist_ok=True)
        instance_lock = QLockFile(str(root / "instance.lock"))
        instance_lock.setStaleLockTime(0)
        if not instance_lock.tryLock(0):
            QMessageBox.information(
                None, "VoxTurbo", "VoxTurbo уже запущен. Откройте его значок в системном трее."
            )
            return 0
        settings, warning = load_settings(root / "settings.json")
        window = MainWindow(settings)
        available = app.primaryScreen().availableGeometry()
        window.resize(
            min(window.width(), available.width() - 60), min(window.height(), available.height() - 60)
        )
        controller = AppController(app, window, settings, root)
        if warning:
            controller.show_message(warning)
        window.show()
        smoke_failed = [False]
        if args.smoke_gui:

            def finish_smoke():
                try:
                    report = health()
                    report.update(
                        {
                            "gui_visible": window.isVisible(),
                            "state": controller.state,
                            "model_ready": controller.store.is_ready(),
                        }
                    )
                    if args.report:
                        args.report.parent.mkdir(parents=True, exist_ok=True)
                        screenshot = args.report.with_suffix(".png")
                        if not window.grab().save(str(screenshot)):
                            raise OSError("Не удалось сохранить снимок окна.")
                        report["screenshot"] = str(screenshot.resolve())
                    write_report(args.report, report)
                except Exception as exc:
                    smoke_failed[0] = True
                    try:
                        write_report(args.report, {"ok": False, "error": str(exc)})
                    except OSError:
                        pass
                finally:
                    controller.request_quit()

            QTimer.singleShot(2000, finish_smoke)
        result = app.exec()
        instance_lock.unlock()
        return 1 if smoke_failed[0] else result
    except Exception as exc:
        write_report(args.report, {"ok": False, "error": str(exc), "type": type(exc).__name__})
        if not (args.self_test or args.smoke_gui or args.transcribe):
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance() or QApplication(sys.argv[:1])
            QMessageBox.critical(None, "VoxTurbo", f"Не удалось запустить приложение: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
