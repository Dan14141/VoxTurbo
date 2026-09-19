"""Win32 hotkeys, input and clipboard transactions for the interactive UI thread.

Clipboard handles are copied before mutation. Every automatic restore compares
sequence, owner and an unguessable transaction marker while OpenClipboard is held.
No function here pumps Qt events, sleeps, or silently retries input.
"""

from __future__ import annotations

import ctypes as C
import os
import re
import uuid
from contextlib import contextmanager
from ctypes import wintypes as W
from dataclasses import dataclass
from typing import Iterator

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 1, 2, 4, 8
MOD_NOREPEAT = 0x4000
CF_TEXT, CF_BITMAP, CF_METAFILEPICT, CF_PALETTE = 1, 2, 3, 9
CF_DIB, CF_UNICODETEXT, CF_ENHMETAFILE = 8, 13, 14
CF_HDROP, CF_LOCALE, CF_DIBV5 = 15, 16, 17
GMEM_MOVEABLE = 0x0002
MAX_CLIPBOARD_BYTES = 128 * 1024 * 1024
MAX_CLIPBOARD_FORMATS = 256
_MARKER_NAME = "VoxTurbo.ClipboardTransaction.v1"
_OLE_TRANSPORT_NAMES = {"DataObject", "Ole Private Data"}
_HGLOBAL_FORMATS = {1, 4, 5, 6, 7, 8, 11, 12, 13, 15, 16, 17, 0x81}
_BITMAP_FORMATS = {CF_BITMAP, 0x82}
_METAFILE_FORMATS = {CF_METAFILEPICT, 0x83}
_ENHMETAFILE_FORMATS = {CF_ENHMETAFILE, 0x8E}
_REGISTERED_MEMORY_FORMATS = {
    "HTML Format",
    "Rich Text Format",
    "Rich Text Format Without Objects",
    "PNG",
    "image/png",
    "JFIF",
    "GIF",
    "image/jpeg",
    "image/gif",
    "Preferred DropEffect",
    "Performed DropEffect",
    "Paste Succeeded",
    "InShellDragLoop",
    "Shell IDList Array",
    "Shell Object Offsets",
    "FileName",
    "FileNameW",
    "FileNameMap",
    "FileNameMapW",
    "DropDescription",
    "UniformResourceLocator",
    "UniformResourceLocatorW",
    "CanIncludeInClipboardHistory",
    "CanUploadToCloudClipboard",
    "ExcludeClipboardContentFromMonitorProcessing",
    "Chromium internal source RFH token",
    "Chromium internal source URL",
}
_HANDLE = C.c_void_p
_UINT_PTR = C.c_size_t
_LONG_PTR = C.c_ssize_t


class KEYBDINPUT(C.Structure):
    _fields_ = [
        ("wVk", W.WORD),
        ("wScan", W.WORD),
        ("dwFlags", W.DWORD),
        ("time", W.DWORD),
        ("dwExtraInfo", _UINT_PTR),
    ]


class MOUSEINPUT(C.Structure):
    _fields_ = [
        ("dx", W.LONG),
        ("dy", W.LONG),
        ("mouseData", W.DWORD),
        ("dwFlags", W.DWORD),
        ("time", W.DWORD),
        ("dwExtraInfo", _UINT_PTR),
    ]


class HARDWAREINPUT(C.Structure):
    _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]


class _INPUTUNION(C.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("event",)
    _fields_ = [("type", W.DWORD), ("event", _INPUTUNION)]


class MSG(C.Structure):
    _fields_ = [
        ("hwnd", _HANDLE),
        ("message", W.UINT),
        ("wParam", _UINT_PTR),
        ("lParam", _LONG_PTR),
        ("time", W.DWORD),
        ("pt", W.POINT),
        ("lPrivate", W.DWORD),
    ]


class GUITHREADINFO(C.Structure):
    _fields_ = [
        ("cbSize", W.DWORD),
        ("flags", W.DWORD),
        ("hwndActive", _HANDLE),
        ("hwndFocus", _HANDLE),
        ("hwndCapture", _HANDLE),
        ("hwndMenuOwner", _HANDLE),
        ("hwndMoveSize", _HANDLE),
        ("hwndCaret", _HANDLE),
        ("rcCaret", W.RECT),
    ]


class _BITMAP(C.Structure):
    _fields_ = [
        ("bmType", W.LONG),
        ("bmWidth", W.LONG),
        ("bmHeight", W.LONG),
        ("bmWidthBytes", W.LONG),
        ("bmPlanes", W.WORD),
        ("bmBitsPixel", W.WORD),
        ("bmBits", _HANDLE),
    ]


class _METAFILEPICT(C.Structure):
    _fields_ = [("mm", C.c_int), ("xExt", C.c_int), ("yExt", C.c_int), ("hMF", _HANDLE)]


class _FORMATETC(C.Structure):
    _fields_ = [
        ("cfFormat", W.WORD),
        ("ptd", C.c_void_p),
        ("dwAspect", W.DWORD),
        ("lindex", W.LONG),
        ("tymed", W.DWORD),
    ]


def _bind(dll: object, name: str, restype: object, *argtypes: object) -> object:
    function = getattr(dll, name)
    function.restype = restype
    function.argtypes = argtypes
    return function


if os.name == "nt":
    _user32 = C.WinDLL("user32", use_last_error=True)
    _kernel32 = C.WinDLL("kernel32", use_last_error=True)
    _gdi32 = C.WinDLL("gdi32", use_last_error=True)
    _ole32 = C.WinDLL("ole32", use_last_error=True)
    for _name, _restype, _args in [
        ("RegisterHotKey", W.BOOL, (_HANDLE, C.c_int, W.UINT, W.UINT)),
        ("UnregisterHotKey", W.BOOL, (_HANDLE, C.c_int)),
        ("GetForegroundWindow", _HANDLE, ()),
        ("GetWindowThreadProcessId", W.DWORD, (_HANDLE, C.POINTER(W.DWORD))),
        ("GetGUIThreadInfo", W.BOOL, (W.DWORD, C.POINTER(GUITHREADINFO))),
        ("IsWindow", W.BOOL, (_HANDLE,)),
        ("GetAsyncKeyState", C.c_short, (C.c_int,)),
        ("SendInput", W.UINT, (W.UINT, C.POINTER(INPUT), C.c_int)),
        ("OpenClipboard", W.BOOL, (_HANDLE,)),
        ("CloseClipboard", W.BOOL, ()),
        ("EmptyClipboard", W.BOOL, ()),
        ("EnumClipboardFormats", W.UINT, (W.UINT,)),
        ("GetClipboardData", _HANDLE, (W.UINT,)),
        ("SetClipboardData", _HANDLE, (W.UINT, _HANDLE)),
        ("GetClipboardSequenceNumber", W.DWORD, ()),
        ("GetClipboardOwner", _HANDLE, ()),
        ("RegisterClipboardFormatW", W.UINT, (W.LPCWSTR,)),
        ("GetClipboardFormatNameW", C.c_int, (W.UINT, W.LPWSTR, C.c_int)),
        ("CopyImage", _HANDLE, (_HANDLE, W.UINT, C.c_int, C.c_int, W.UINT)),
    ]:
        _bind(_user32, _name, _restype, *_args)
    for _name, _restype, _args in [
        ("GlobalAlloc", _HANDLE, (W.UINT, C.c_size_t)),
        ("GlobalSize", C.c_size_t, (_HANDLE,)),
        ("GlobalLock", _HANDLE, (_HANDLE,)),
        ("GlobalUnlock", W.BOOL, (_HANDLE,)),
        ("GlobalFree", _HANDLE, (_HANDLE,)),
    ]:
        _bind(_kernel32, _name, _restype, *_args)
    for _name, _restype, _args in [
        ("DeleteObject", W.BOOL, (_HANDLE,)),
        ("GetObjectW", C.c_int, (_HANDLE, C.c_int, _HANDLE)),
        ("GetPaletteEntries", W.UINT, (_HANDLE, W.UINT, W.UINT, _HANDLE)),
        ("CopyEnhMetaFileW", _HANDLE, (_HANDLE, W.LPCWSTR)),
        ("GetEnhMetaFileBits", W.UINT, (_HANDLE, W.UINT, _HANDLE)),
        ("DeleteEnhMetaFile", W.BOOL, (_HANDLE,)),
        ("GetMetaFileBitsEx", W.UINT, (_HANDLE, W.UINT, _HANDLE)),
        ("DeleteMetaFile", W.BOOL, (_HANDLE,)),
    ]:
        _bind(_gdi32, _name, _restype, *_args)
    _bind(_ole32, "OleDuplicateData", _HANDLE, _HANDLE, W.WORD, W.UINT)
    _bind(_ole32, "OleInitialize", W.LONG, C.c_void_p)
    _bind(_ole32, "OleUninitialize", None)
    _bind(_ole32, "OleGetClipboard", W.LONG, C.POINTER(C.c_void_p))
    _bind(_ole32, "CoTaskMemFree", None, C.c_void_p)
else:
    _user32 = _kernel32 = _gdi32 = _ole32 = None


class WindowsError(RuntimeError):
    def __init__(self, message: str, *, code: str = "WINDOWS_ERROR", winerror: int = 0):
        super().__init__(message)
        self.code = code
        self.winerror = winerror


class ClipboardError(WindowsError):
    """Clipboard failed; never implies that an empty snapshot was obtained."""


class ClipboardBusy(ClipboardError):
    """OpenClipboard failed before this attempt made any changes; retry later."""


def _require_windows() -> None:
    if _user32 is None:
        raise WindowsError("Эта функция доступна только в Windows.")


def _clipboard_error(message: str, code: str = "CLIPBOARD_ERROR") -> ClipboardError:
    return ClipboardError(message, code=code, winerror=C.get_last_error())


@dataclass(frozen=True)
class Hotkey:
    vk: int
    modifiers: int
    normalized: str


def parse_hotkey(value: str) -> Hotkey:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Укажите горячую клавишу, например F4 или Ctrl+Shift+Space.")
    tokens = [part.strip().upper() for part in value.split("+")]
    if not all(tokens) or len(tokens) != len(set(tokens)):
        raise ValueError("В комбинации есть пустая или повторяющаяся клавиша.")
    modifiers = {"CTRL": MOD_CONTROL, "ALT": MOD_ALT, "SHIFT": MOD_SHIFT, "WIN": MOD_WIN}
    keys = [part for part in tokens if part not in modifiers]
    if len(keys) != 1 or tokens[-1] != keys[0]:
        raise ValueError("Укажите модификаторы и одну клавишу в конце комбинации.")
    key = keys[0]
    mask = sum(modifiers[token] for token in tokens[:-1])
    match = re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", key)
    if match:
        number = int(match[1])
        if number == 12:
            raise ValueError("F12 зарезервирована Windows для отладчика.")
        vk = 0x70 + number - 1
    elif key == "SPACE" and mask:
        vk = 0x20
    elif re.fullmatch(r"[A-Z0-9]", key) and mask:
        vk = ord(key)
    else:
        raise ValueError("Используйте F1–F24 (кроме F12) либо Ctrl/Alt/Shift/Win с буквой, цифрой или Space.")
    if mask & MOD_WIN and key in {"L", "U"}:
        raise ValueError("Эта комбинация зарезервирована Windows.")
    names = [name.title() for name in ("CTRL", "ALT", "SHIFT", "WIN") if name in tokens[:-1]]
    return Hotkey(vk, mask, "+".join([*names, "Space" if key == "SPACE" else key]))


class HotkeyManager:
    def __init__(self, hwnd: int):
        _require_windows()
        self.hwnd = int(hwnd)
        self.hotkey_id = 0
        self._hotkey: Hotkey | None = None
        self._next_id = 0x4000

    def rebind(self, value: str) -> None:
        hotkey = parse_hotkey(value)
        if hotkey == self._hotkey:
            return
        new_id = self._next_id
        self._next_id = 0x4000 if new_id >= 0xBFFE else new_id + 1
        if not _user32.RegisterHotKey(self.hwnd, new_id, hotkey.modifiers | MOD_NOREPEAT, hotkey.vk):
            raise WindowsError(
                "Не удалось зарегистрировать горячую клавишу: комбинация может быть занята.",
                code="HOTKEY_CONFLICT",
                winerror=C.get_last_error(),
            )
        if self.hotkey_id and not _user32.UnregisterHotKey(self.hwnd, self.hotkey_id):
            error = C.get_last_error()
            _user32.UnregisterHotKey(self.hwnd, new_id)
            raise WindowsError("Не удалось заменить прежнюю горячую клавишу.", winerror=error)
        self.hotkey_id, self._hotkey = new_id, hotkey

    def close(self) -> None:
        if self.hotkey_id:
            if not _user32.UnregisterHotKey(self.hwnd, self.hotkey_id):
                raise WindowsError("Не удалось освободить горячую клавишу.", winerror=C.get_last_error())
            self.hotkey_id = 0
            self._hotkey = None


@dataclass(frozen=True)
class Target:
    hwnd: int
    pid: int
    tid: int
    focused_hwnd: int


def capture_target() -> Target | None:
    _require_windows()
    hwnd = _user32.GetForegroundWindow()
    if not hwnd or not _user32.IsWindow(hwnd):
        return None
    pid = W.DWORD()
    tid = _user32.GetWindowThreadProcessId(hwnd, C.byref(pid))
    if not tid or not pid.value:
        return None
    info = GUITHREADINFO()
    info.cbSize = C.sizeof(info)
    if not _user32.GetGUIThreadInfo(tid, C.byref(info)):
        return None
    if info.flags & (0x2 | 0x4 | 0x8 | 0x10):
        return None
    return Target(int(hwnd), pid.value, int(tid), int(info.hwndFocus or 0))


def target_is_current(target: Target) -> bool:
    current = capture_target()
    return current is not None and current == target


def modifiers_pressed() -> bool:
    _require_windows()
    return any(_user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C))


def _key_input(vk: int, flags: int = 0) -> INPUT:
    event = INPUT()
    event.type = 1
    event.ki = KEYBDINPUT(vk, 0, flags, 0, 0x44454C5441454348)
    return event


def send_paste(shortcut: str) -> None:
    _require_windows()
    if shortcut not in {"Ctrl+V", "Shift+Insert"}:
        raise ValueError("Поддерживаются только Ctrl+V и Shift+Insert.")
    if modifiers_pressed():
        raise WindowsError("Отпустите Ctrl, Shift, Alt и Win перед вставкой.", code="MODIFIER_HELD")
    modifier, key, extended = (0x11, 0x56, 0) if shortcut == "Ctrl+V" else (0x10, 0x2D, 1)
    events = (INPUT * 4)(
        _key_input(modifier),
        _key_input(key, extended),
        _key_input(key, extended | 2),
        _key_input(modifier, 2),
    )
    sent = _user32.SendInput(4, events, C.sizeof(INPUT))
    if sent != 4:
        error = C.get_last_error()
        # Only a prefix can have been inserted. Release only our still-down keys;
        # never replay the shortcut, which could duplicate already pasted text.
        cleanup = []
        if sent == 2:
            cleanup.append(_key_input(key, extended | 2))
        if 0 < sent < 4:
            left, right = (0xA2, 0xA3) if modifier == 0x11 else (0xA0, 0xA1)
            # The injected generic modifier normally maps to the left key. A
            # physical right modifier must not be released by this cleanup.
            if not (_user32.GetAsyncKeyState(right) & 0x8000):
                cleanup.append(_key_input(left, 2))
        if cleanup:
            _user32.SendInput(len(cleanup), (INPUT * len(cleanup))(*cleanup), C.sizeof(INPUT))
        raise WindowsError(
            "Windows не приняла ввод полностью. Возможно, целевое приложение запущено с повышенными правами.",
            code="INPUT_REJECTED_OR_PARTIAL",
            winerror=error,
        )


@contextmanager
def _open_clipboard(hwnd: int) -> Iterator[None]:
    _require_windows()
    if not hwnd:
        raise ClipboardError("Для буфера требуется существующее окно приложения.")
    if not _user32.OpenClipboard(hwnd):
        raise ClipboardBusy(
            "Буфер обмена занят другим приложением.", code="CLIPBOARD_BUSY", winerror=C.get_last_error()
        )
    try:
        yield
    finally:
        if not _user32.CloseClipboard():
            raise _clipboard_error("Не удалось закрыть буфер обмена.", "CLIPBOARD_CLOSE_FAILED")


def _register_format(name: str) -> int:
    value = _user32.RegisterClipboardFormatW(name)
    if not value:
        raise _clipboard_error("Не удалось зарегистрировать формат буфера.")
    return int(value)


def _format_name(format_id: int) -> str:
    if format_id < 0xC000:
        return str(format_id)
    buffer = C.create_unicode_buffer(256)
    if not _user32.GetClipboardFormatNameW(format_id, buffer, len(buffer)):
        raise _clipboard_error("Не удалось прочитать имя формата буфера.")
    return buffer.value


def _formats_open() -> list[int]:
    formats: list[int] = []
    previous = 0
    while True:
        C.set_last_error(0)
        current = int(_user32.EnumClipboardFormats(previous))
        if not current:
            if C.get_last_error():
                raise _clipboard_error("Не удалось перечислить форматы буфера.")
            return formats
        if current in formats or len(formats) >= MAX_CLIPBOARD_FORMATS:
            raise ClipboardError("Слишком много форматов буфера обмена.", code="CLIPBOARD_TOO_LARGE")
        formats.append(current)
        previous = current


def _unlock(handle: int) -> None:
    C.set_last_error(0)
    if not _kernel32.GlobalUnlock(handle) and C.get_last_error():
        raise _clipboard_error("Не удалось освободить блокировку памяти буфера.")


def _read_hglobal(handle: int, limit: int = MAX_CLIPBOARD_BYTES) -> bytes:
    size = int(_kernel32.GlobalSize(handle))
    if size <= 0:
        raise _clipboard_error(
            "Формат буфера содержит недоступный или пустой объект памяти.", "CLIPBOARD_UNSUPPORTED_FORMAT"
        )
    if size > limit:
        raise ClipboardError("Буфер обмена превышает лимит памяти.", code="CLIPBOARD_TOO_LARGE")
    pointer = _kernel32.GlobalLock(handle)
    if not pointer:
        raise _clipboard_error("Не удалось прочитать память буфера.")
    try:
        return C.string_at(pointer, size)
    finally:
        _unlock(handle)


def _new_hglobal(data: bytes) -> int:
    if not data:
        raise ClipboardError("Нельзя создать пустой объект памяти буфера.")
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE | 0x0040, len(data))
    if not handle:
        raise _clipboard_error("Недостаточно памяти для сохранения буфера.")
    try:
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            raise _clipboard_error("Не удалось записать память буфера.")
        try:
            C.memmove(pointer, data, len(data))
        finally:
            _unlock(handle)
    except BaseException:
        _kernel32.GlobalFree(handle)
        raise
    return int(handle)


@dataclass
class _OwnedEntry:
    format_id: int
    handle: int
    kind: str
    size: int

    def close(self) -> None:
        if not self.handle:
            return
        handle, self.handle = self.handle, 0
        if self.kind == "hglobal":
            _kernel32.GlobalFree(handle)
        elif self.kind == "bitmap" or self.kind == "palette":
            _gdi32.DeleteObject(handle)
        elif self.kind == "enhmetafile":
            _gdi32.DeleteEnhMetaFile(handle)
        elif self.kind == "metafile":
            pointer = _kernel32.GlobalLock(handle)
            if pointer:
                metafile = C.cast(pointer, C.POINTER(_METAFILEPICT)).contents.hMF
                _unlock(handle)
                if metafile:
                    _gdi32.DeleteMetaFile(metafile)
            _kernel32.GlobalFree(handle)


def _close_entries(entries: list[_OwnedEntry]) -> None:
    for entry in entries:
        entry.close()


def _clone_entry(format_id: int, handle: int, remaining: int) -> _OwnedEntry:
    name = _format_name(format_id)
    if format_id in _HGLOBAL_FORMATS or name in _REGISTERED_MEMORY_FORMATS:
        limit = min(remaining, 4096) if name == "Chromium internal source RFH token" else remaining
        if name == "Chromium internal source URL":
            limit = min(limit, 65536)
        data = _read_hglobal(handle, limit)
        if name == "Chromium internal source RFH token":
            # Chromium writes a serialized base::Pickle, not a borrowed pointer.
            # Keep every byte (including allocation padding); never interpret IDs.
            payload_size = int.from_bytes(data[:4], "little")
            if len(data) < 8 or not 0 < payload_size <= len(data) - 4 or payload_size % 4:
                raise ClipboardError(
                    "Неверный сериализованный формат Chromium.", code="CLIPBOARD_UNSUPPORTED_FORMAT"
                )
        if name in {"PNG", "image/png"} and not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ClipboardError(
                "Именованный PNG-формат не содержит PNG-данные.", code="CLIPBOARD_UNSUPPORTED_FORMAT"
            )
        return _OwnedEntry(format_id, _new_hglobal(data), "hglobal", len(data))
    if format_id in _BITMAP_FORMATS:
        bitmap = _BITMAP()
        if not _gdi32.GetObjectW(handle, C.sizeof(bitmap), C.byref(bitmap)):
            raise _clipboard_error("Не удалось прочитать изображение буфера.")
        size = abs(bitmap.bmHeight) * abs(bitmap.bmWidthBytes) + C.sizeof(bitmap)
        kind = "bitmap"
    elif format_id in _ENHMETAFILE_FORMATS:
        size = int(_gdi32.GetEnhMetaFileBits(handle, 0, None))
        kind = "enhmetafile"
    elif format_id == CF_PALETTE:
        size = int(_gdi32.GetPaletteEntries(handle, 0, 0, None)) * 4 + 4
        kind = "palette"
    elif format_id in _METAFILE_FORMATS:
        if _kernel32.GlobalSize(handle) < C.sizeof(_METAFILEPICT):
            raise ClipboardError("Неполная структура метафайла буфера.", code="CLIPBOARD_UNSUPPORTED_FORMAT")
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            raise _clipboard_error("Не удалось прочитать метафайл буфера.")
        try:
            metafile = C.cast(pointer, C.POINTER(_METAFILEPICT)).contents.hMF
            size = int(_gdi32.GetMetaFileBitsEx(metafile, 0, None)) + C.sizeof(_METAFILEPICT)
        finally:
            _unlock(handle)
        kind = "metafile"
    else:
        raise ClipboardError(
            f"Формат буфера {name!r} пока не поддержан. Исходные данные сохранены.",
            code="CLIPBOARD_UNSUPPORTED_FORMAT",
        )
    if size <= 0 or size > remaining:
        raise ClipboardError(
            "Объект буфера недоступен или превышает лимит памяти.", code="CLIPBOARD_TOO_LARGE"
        )
    if kind == "bitmap":
        duplicate = _user32.CopyImage(handle, 0, 0, 0, 0x2000)
    elif kind == "enhmetafile":
        duplicate = _gdi32.CopyEnhMetaFileW(handle, None)
    else:
        source_format = CF_PALETTE if kind == "palette" else CF_METAFILEPICT
        duplicate = _ole32.OleDuplicateData(handle, source_format, GMEM_MOVEABLE)
    if not duplicate or duplicate == handle:
        raise _clipboard_error("Не удалось независимо скопировать объект буфера.")
    return _OwnedEntry(format_id, int(duplicate), kind, size)


def _copy_entries(entries: list[_OwnedEntry]) -> list[_OwnedEntry]:
    copies: list[_OwnedEntry] = []
    try:
        for entry in entries:
            copies.append(_clone_entry(entry.format_id, entry.handle, MAX_CLIPBOARD_BYTES))
        return copies
    except BaseException:
        _close_entries(copies)
        raise


def _put_entries_open(entries: list[_OwnedEntry]) -> None:
    for entry in entries:
        if not _user32.SetClipboardData(entry.format_id, entry.handle):
            raise _clipboard_error("Не удалось опубликовать формат буфера.", "CLIPBOARD_PUBLISH_FAILED")
        entry.handle = 0  # Windows owns this object now, even if a later format fails.


def _empty_open() -> None:
    if not _user32.EmptyClipboard():
        raise _clipboard_error("Не удалось заменить буфер обмена.", "CLIPBOARD_EMPTY_FAILED")


def _com_method(pointer: C.c_void_p, index: int, result: object, *arguments: object) -> object:
    vtable = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(result, C.c_void_p, *arguments)(vtable[index])


def _ole_formats() -> list[tuple[int, int, int, int, bool]]:
    """Read authoritative application FORMATETCs, outside OpenClipboard.

    This does not retain the source IDataObject or copy its private marshal data.
    The calling UI thread must be STA-compatible; any COM failure is fail-closed.
    """
    initialized = _ole32.OleInitialize(None)
    if initialized not in (0, 1):
        raise ClipboardError(
            "Не удалось проверить OLE-форматы в текущем потоке.", code="CLIPBOARD_UNSUPPORTED_FORMAT"
        )
    source, enumerator = C.c_void_p(), C.c_void_p()
    try:
        if _ole32.OleGetClipboard(C.byref(source)) < 0 or not source.value:
            raise ClipboardError("Не удалось проверить исходный OLE-буфер.", code="CLIPBOARD_SNAPSHOT_FAILED")
        enum_formats = _com_method(source, 8, W.LONG, W.DWORD, C.POINTER(C.c_void_p))
        if enum_formats(source, 1, C.byref(enumerator)) < 0 or not enumerator.value:
            raise ClipboardError(
                "OLE-источник не перечислил все форматы.", code="CLIPBOARD_UNSUPPORTED_FORMAT"
            )
        next_format = _com_method(enumerator, 3, W.LONG, W.ULONG, C.POINTER(_FORMATETC), C.POINTER(W.ULONG))
        result = []
        while True:
            descriptor, fetched = _FORMATETC(), W.ULONG()
            status = next_format(enumerator, 1, C.byref(descriptor), C.byref(fetched))
            try:
                if status not in (0, 1) or fetched.value not in (0, 1):
                    raise ClipboardError(
                        "Ошибка перечисления OLE-форматов.", code="CLIPBOARD_SNAPSHOT_FAILED"
                    )
                if fetched.value:
                    if len(result) >= MAX_CLIPBOARD_FORMATS:
                        raise ClipboardError("Слишком много OLE-форматов.", code="CLIPBOARD_TOO_LARGE")
                    result.append(
                        (
                            int(descriptor.cfFormat),
                            int(descriptor.dwAspect),
                            int(descriptor.lindex),
                            int(descriptor.tymed),
                            bool(descriptor.ptd),
                        )
                    )
                if status == 1:
                    return result
                if not fetched.value:
                    raise ClipboardError("Некорректный OLE-перечислитель.", code="CLIPBOARD_SNAPSHOT_FAILED")
            finally:
                if descriptor.ptd:
                    _ole32.CoTaskMemFree(descriptor.ptd)
    finally:
        if enumerator.value:
            _com_method(enumerator, 2, W.ULONG)(enumerator)
        if source.value:
            _com_method(source, 2, W.ULONG)(source)
        _ole32.OleUninitialize()


@dataclass(frozen=True)
class _TransportPolicy:
    sequence: int
    owner: int
    ignored: frozenset[int]


def _transport_policy(hwnd: int) -> _TransportPolicy | None:
    """Narrow, audited exception for OLE transport around complete image payloads."""
    with _open_clipboard(hwnd):
        formats = _formats_open()
        names = {format_id: _format_name(format_id) for format_id in formats}
        ignored = frozenset(fmt for fmt, name in names.items() if name in _OLE_TRANSPORT_NAMES)
        if not ignored:
            return None
        images = {CF_BITMAP, CF_DIB, CF_DIBV5}
        if not (images.intersection(formats) or any(name in {"PNG", "image/png"} for name in names.values())):
            raise ClipboardError(
                "Этот OLE-буфер пока не поддержан; требуется автономное изображение.",
                code="CLIPBOARD_UNSUPPORTED_FORMAT",
            )
        supported_native = (
            _HGLOBAL_FORMATS | _BITMAP_FORMATS | _METAFILE_FORMATS | _ENHMETAFILE_FORMATS | {CF_PALETTE}
        )
        payloads = set(formats) - ignored
        if any(
            fmt not in supported_native and names[fmt] not in _REGISTERED_MEMORY_FORMATS for fmt in payloads
        ):
            raise ClipboardError(
                "OLE-буфер содержит непереносимый формат. Исходные данные сохранены.",
                code="CLIPBOARD_UNSUPPORTED_FORMAT",
            )
        policy = _TransportPolicy(
            int(_user32.GetClipboardSequenceNumber()), int(_user32.GetClipboardOwner() or 0), ignored
        )
    descriptors = _ole_formats()
    if not descriptors:
        raise ClipboardError(
            "OLE-источник не предоставил форматы изображения.", code="CLIPBOARD_UNSUPPORTED_FORMAT"
        )
    for format_id, aspect, index, medium, device in descriptors:
        expected_medium = (
            16
            if format_id in _BITMAP_FORMATS | {CF_PALETTE}
            else 32
            if format_id in _METAFILE_FORMATS
            else 64
            if format_id in _ENHMETAFILE_FORMATS
            else 1
        )
        # PNG is a self-contained encoded byte stream. OleGetClipboard documents
        # conversion between flat HGLOBAL and IStream media; no virtual files or
        # arbitrary registered stream formats get this exception (ADR-001).
        png_stream = names.get(format_id) in {"PNG", "image/png"} and medium in {1, 4, 5}
        if (
            format_id not in payloads
            or aspect != 1
            or index != -1
            or device
            or (medium != expected_medium and not png_stream)
        ):
            raise ClipboardError(
                "OLE-источник требует дополнительные объекты или среды хранения.",
                code="CLIPBOARD_UNSUPPORTED_FORMAT",
            )
    return policy


class ClipboardSnapshot:
    """Independent native copies. restore() intentionally replaces the clipboard.

    The unconditional method is for explicit user recovery and isolated tests;
    production automatic restoration must use ClipboardTransaction instead.
    """

    def __init__(self, entries: list[_OwnedEntry], sequence: int, owner: int):
        self._entries = entries
        self.sequence = sequence
        self.owner = owner
        self._closed = False

    @classmethod
    def _capture_open(cls, policy: _TransportPolicy | None = None) -> ClipboardSnapshot:
        entries: list[_OwnedEntry] = []
        try:
            format_ids = _formats_open()
            ignored = policy.ignored if policy is not None else frozenset()
            if policy is not None and (
                int(_user32.GetClipboardSequenceNumber()) != policy.sequence
                or int(_user32.GetClipboardOwner() or 0) != policy.owner
            ):
                raise ClipboardError(
                    "Буфер изменился во время проверки OLE; вставка отменена.",
                    code="CLIPBOARD_SNAPSHOT_FAILED",
                )
            # Reject the complete set before rendering or allocating any format.
            for format_id in format_ids:
                if format_id in ignored:
                    continue
                if not (
                    format_id
                    in _HGLOBAL_FORMATS
                    | _BITMAP_FORMATS
                    | _METAFILE_FORMATS
                    | _ENHMETAFILE_FORMATS
                    | {CF_PALETTE}
                    or _format_name(format_id) in _REGISTERED_MEMORY_FORMATS
                ):
                    raise ClipboardError(
                        f"Формат буфера {_format_name(format_id)!r} пока не поддержан. Исходные данные сохранены.",
                        code="CLIPBOARD_UNSUPPORTED_FORMAT",
                    )
            remaining = MAX_CLIPBOARD_BYTES
            for format_id in format_ids:
                if format_id in ignored:
                    continue
                handle = _user32.GetClipboardData(format_id)
                if not handle:
                    raise _clipboard_error(
                        "Не удалось получить все данные буфера. Исходные данные сохранены.",
                        "CLIPBOARD_SNAPSHOT_FAILED",
                    )
                entry = _clone_entry(format_id, int(handle), remaining)
                entries.append(entry)
                remaining -= entry.size
            if _formats_open() != format_ids:
                raise ClipboardError(
                    "Набор форматов изменился во время чтения буфера. Повторите вставку.",
                    code="CLIPBOARD_SNAPSHOT_FAILED",
                )
            sequence = int(_user32.GetClipboardSequenceNumber())
            if not sequence:
                raise ClipboardError("Windows не предоставила номер состояния буфера.")
            return cls(entries, sequence, int(_user32.GetClipboardOwner() or 0))
        except BaseException:
            _close_entries(entries)
            raise

    @classmethod
    def capture(cls, owner_hwnd: int) -> ClipboardSnapshot:
        policy = _transport_policy(owner_hwnd)
        with _open_clipboard(owner_hwnd):
            return cls._capture_open(policy)

    def prepare(self) -> list[_OwnedEntry]:
        if self._closed:
            raise ClipboardError("Снимок буфера уже освобождён.")
        return _copy_entries(self._entries)

    def restore(self, owner_hwnd: int) -> None:
        prepared = self.prepare()
        try:
            with _open_clipboard(owner_hwnd):
                _empty_open()
                _put_entries_open(prepared)
        finally:
            _close_entries(prepared)

    def close(self) -> None:
        if not self._closed:
            _close_entries(self._entries)
            self._closed = True


class ClipboardTransaction:
    def __init__(self, owner_hwnd: int):
        _require_windows()
        self.owner_hwnd = int(owner_hwnd)
        self._snapshot: ClipboardSnapshot | None = None
        self._prepared: list[_OwnedEntry] = []
        self._marker = _register_format(_MARKER_NAME)
        self._token = b""
        self._sequence = 0
        self._pending = False
        self._recovery_required = False

    @property
    def pending(self) -> bool:
        return self._pending

    @property
    def recovery_required(self) -> bool:
        return self._recovery_required

    def publish(self, text: str) -> None:
        if self._pending or self._snapshot is not None:
            raise ClipboardError("Предыдущее восстановление буфера ещё не завершено.")
        if not isinstance(text, str) or not text.strip() or "\0" in text:
            raise ClipboardError("Результат пуст или содержит недопустимый нулевой символ.")
        encoded = (text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n") + "\0").encode(
            "utf-16-le"
        )
        if len(encoded) > MAX_CLIPBOARD_BYTES:
            raise ClipboardError("Текст превышает лимит буфера.", code="CLIPBOARD_TOO_LARGE")
        self._token = uuid.uuid4().bytes
        temporary: list[_OwnedEntry] = []
        mutated = False
        try:
            for format_id, data in [
                (CF_UNICODETEXT, encoded),
                (self._marker, self._token),
                (_register_format("CanIncludeInClipboardHistory"), b"\0" * 4),
                (_register_format("CanUploadToCloudClipboard"), b"\0" * 4),
            ]:
                temporary.append(_OwnedEntry(format_id, _new_hglobal(data), "hglobal", len(data)))
            policy = _transport_policy(self.owner_hwnd)
            with _open_clipboard(self.owner_hwnd):
                self._snapshot = ClipboardSnapshot._capture_open(policy)
                self._prepared = self._snapshot.prepare()
                try:
                    _empty_open()
                    mutated = True
                    _put_entries_open(temporary)
                    self._sequence = int(_user32.GetClipboardSequenceNumber())
                    self._pending = True
                except BaseException as publication_error:
                    if mutated:
                        try:
                            _empty_open()
                            _put_entries_open(self._prepared)
                        except BaseException as rollback_error:
                            self._pending = False
                            self._recovery_required = True
                            raise ClipboardError(
                                "Публикация и полное восстановление буфера не удались. Снимок сохранён в памяти приложения.",
                                code="CLIPBOARD_RESTORE_PARTIAL",
                            ) from rollback_error
                    self.close()
                    raise publication_error
            # CloseClipboard finalizes Windows' synthesized text formats and can
            # advance the sequence. Record that completed publication without
            # immediately re-opening: clipboard listeners may briefly own the lock.
            # The GUI serializes writers to this HWND; automatic restore still
            # checks this sequence AND owner AND marker under OpenClipboard.
            if self._pending:
                sequence = int(_user32.GetClipboardSequenceNumber())
                if not sequence or int(_user32.GetClipboardOwner() or 0) != self.owner_hwnd:
                    raise ClipboardError("Буфер изменился до вставки.", code="CLIPBOARD_CHANGED")
                self._sequence = sequence
        except BaseException:
            if not mutated:
                self.close()
            raise
        finally:
            _close_entries(temporary)

    def _owned_open(self, *, check_sequence: bool = True) -> bool:
        if check_sequence and int(_user32.GetClipboardSequenceNumber()) != self._sequence:
            return False
        if int(_user32.GetClipboardOwner() or 0) != self.owner_hwnd:
            return False
        handle = _user32.GetClipboardData(self._marker)
        if not handle:
            return False
        try:
            token = _read_hglobal(int(handle), 4096)
        except ClipboardError:
            return False
        # GlobalAlloc may round the allocation upward; only the 16-byte token is
        # payload, and padding is not part of the transaction identity.
        return len(token) >= len(self._token) and token[: len(self._token)] == self._token

    def restore(self) -> str:
        if self._recovery_required:
            raise ClipboardError(
                "Требуется явное восстановление сохранённого буфера.", code="CLIPBOARD_RECOVERY_REQUIRED"
            )
        if not self._pending:
            return "superseded"
        with _open_clipboard(self.owner_hwnd):
            if not self._owned_open():
                self.close()
                return "superseded"
            try:
                _empty_open()
                _put_entries_open(self._prepared)
            except BaseException as error:
                # Keep the independent master snapshot for explicit recovery.
                # Partial clipboard replacement has no valid transaction marker.
                self._pending = False
                self._recovery_required = True
                _close_entries(self._prepared)
                self._prepared = []
                raise ClipboardError(
                    "Не удалось восстановить все форматы буфера. Снимок сохранён в памяти приложения.",
                    code="CLIPBOARD_RESTORE_PARTIAL",
                ) from error
            self.close()
            return "restored"

    def recover(self) -> None:
        """Explicit user action: replace current clipboard with retained original.

        The UI must explain that this may replace a newer user copy. Never call
        automatically after a timer, conflict or partial restoration failure.
        """
        if not self._recovery_required or self._snapshot is None:
            raise ClipboardError("Нет сохранённого снимка для восстановления.")
        self._snapshot.restore(self.owner_hwnd)
        self._recovery_required = False
        self.close()

    def close(self) -> None:
        """Dispose after completion or explicit abandonment; never a hidden restore."""
        if self._recovery_required:
            raise ClipboardError(
                "Сначала восстановите сохранённый буфер или явно откажитесь от снимка.",
                code="CLIPBOARD_RECOVERY_REQUIRED",
            )
        _close_entries(self._prepared)
        self._prepared = []
        if self._snapshot is not None:
            self._snapshot.close()
            self._snapshot = None
        self._pending = False

    def discard_recovery(self) -> None:
        """Explicitly abandon a retained original after a visible user decision."""
        self._recovery_required = False
        self.close()
