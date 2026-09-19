import subprocess
import sys
import wave

import pytest

from voxturbo.__main__ import transcribe_file

# Дочерний процесс держит single-instance lock, пока родитель проверяет второй запуск.
LOCK_HOLDER = """
import sys
from PySide6.QtCore import QLockFile

lock = QLockFile(sys.argv[1])
lock.setStaleLockTime(0)
if not lock.tryLock(0):
    print("failed", flush=True)
    raise SystemExit(1)
print("locked", flush=True)
sys.stdin.readline()
"""


def test_cli_rejects_long_wav_before_reading(tmp_path, monkeypatch):
    class OversizedWave:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def getsampwidth(self):
            return 2

        def getnchannels(self):
            return 1

        def getframerate(self):
            return 16000

        def getnframes(self):
            return 16000 * 3600

        def readframes(self, count):
            pytest.fail("Oversized audio must not be read or allocated")

    monkeypatch.setattr(wave, "open", lambda *args: OversizedWave())
    with pytest.raises(ValueError, match="60"):
        transcribe_file(tmp_path / "long.wav", tmp_path / "model")


def test_second_instance_reports_and_exits_without_ui(tmp_path, monkeypatch):
    """Второй экземпляр сообщает о запущенном приложении и не поднимает окно.

    QApplication создаётся настоящий: app_icon() строит QPixmap и без него
    зависает. Отдельный процесс держит lock, пока проверяется второй запуск.
    """
    import PySide6.QtWidgets as qt_widgets

    from voxturbo import __main__ as entrypoint

    holder = subprocess.Popen(
        [sys.executable, "-c", LOCK_HOLDER, str(tmp_path / "instance.lock")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "locked"

        created = []

        class FakeMessageBox:
            @staticmethod
            def information(parent, title, text):
                created.append(("message", title, text))

        monkeypatch.setattr(qt_widgets, "QMessageBox", FakeMessageBox)
        monkeypatch.setattr("voxturbo.ui.MainWindow", lambda *args: created.append(("window",)))
        monkeypatch.setattr(
            "voxturbo.controller.AppController", lambda *args: created.append(("controller",))
        )

        assert entrypoint.main(["--data-dir", str(tmp_path)]) == 0
        assert [entry[0] for entry in created] == ["message"]
        assert "уже запущен" in created[0][2]
    finally:
        holder.stdin.close()
        holder.terminate()
        holder.wait(timeout=15)
