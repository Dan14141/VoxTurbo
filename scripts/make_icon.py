"""Собирает installer/voxturbo.ico из фирменного знака приложения.

Знак рисует voxturbo.ui.draw_logo, поэтому иконка окна, трея и установщика
совпадают. Запуск: .venv\\Scripts\\python scripts/make_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QGuiApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from voxturbo.ui import logo_pixmap  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(size: int) -> bytes:
    payload = QByteArray()
    buffer = QBuffer(payload)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not logo_pixmap(size).save(buffer, "PNG"):
        raise RuntimeError(f"Не удалось закодировать PNG {size}x{size}")
    buffer.close()
    return bytes(payload)


def ico_bytes(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, payload = b"", b""
    for size, data in images:
        dimension = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)
    return header + entries + payload


def main() -> int:
    # QPixmap требует живой QGuiApplication: без сохранённой ссылки Qt падает.
    application = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    target = Path(__file__).resolve().parents[1] / "installer" / "voxturbo.ico"
    images = [(size, png_bytes(size)) for size in SIZES]
    target.write_bytes(ico_bytes(images))
    print(f"{target} — {target.stat().st_size} байт, размеры: {', '.join(str(s) for s in SIZES)}")
    del application
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
