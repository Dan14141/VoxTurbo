"""Deterministic native-memory tests; never open the user's clipboard or inject input."""

import ctypes as C
import os
import struct

import pytest

from voxturbo.platform import windows as win

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("value", "vk", "modifiers", "normalized"),
    [
        ("F4", 0x73, 0, "F4"),
        (" ctrl + shift + space ", 0x20, 6, "Ctrl+Shift+Space"),
        ("Alt+F24", 0x87, 1, "Alt+F24"),
        ("Shift+Ctrl+9", ord("9"), 6, "Ctrl+Shift+9"),
    ],
)
def test_hotkey_parser(value, vk, modifiers, normalized):
    hotkey = win.parse_hotkey(value)
    assert (hotkey.vk, hotkey.modifiers, hotkey.normalized) == (vk, modifiers, normalized)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "A",
        "Space",
        "F12",
        "F25",
        "Ctrl",
        "Ctrl+Ctrl+A",
        "Ctrl++A",
        "F4+Alt",
        "Enter",
        "Ctrl+Enter",
        "Win+L",
        None,
        42,
    ],
)
def test_hotkey_rejects_invalid(value):
    with pytest.raises(ValueError):
        win.parse_hotkey(value)


class FakeClipboard:
    """Native HGLOBAL lifetime, fake clipboard ownership; no user32 calls."""

    def __init__(self):
        self.data = {}
        self.names = {}
        self.sequence = 10
        self.owner = 55
        self.opener = 0
        self.busy = False
        self.empties = 0
        self.set_attempts = 0
        self.fail_set_at = set()
        self.fail_enum = False
        self.missing_format = 0
        self.registered = {}
        self.hotkey_fail = False
        self.hotkey_events = []

    def install(self, formats, owner=55):
        self.release()
        self.data = {fmt: win._new_hglobal(payload) for fmt, payload in formats.items()}
        self.owner = owner
        self.sequence += 1

    def payloads(self):
        return {fmt: win._read_hglobal(handle) for fmt, handle in self.data.items()}

    def release(self):
        for handle in self.data.values():
            win._kernel32.GlobalFree(handle)
        self.data.clear()

    def RegisterClipboardFormatW(self, name):
        if name not in self.names:
            self.names[name] = 0xC000 + len(self.names)
        return self.names[name]

    def GetClipboardFormatNameW(self, format_id, buffer, length):
        name = next((name for name, value in self.names.items() if value == format_id), "")
        buffer.value = name[: length - 1]
        return len(buffer.value)

    def OpenClipboard(self, hwnd):
        if self.busy or self.opener:
            C.set_last_error(5)
            return 0
        self.opener = hwnd
        return 1

    def CloseClipboard(self):
        assert self.opener
        self.opener = 0
        return 1

    def EnumClipboardFormats(self, previous):
        assert self.opener
        if self.fail_enum:
            C.set_last_error(5)
            return 0
        formats = list(self.data)
        index = 0 if not previous else formats.index(previous) + 1
        return formats[index] if index < len(formats) else 0

    def GetClipboardData(self, format_id):
        assert self.opener
        return 0 if format_id == self.missing_format else self.data.get(format_id, 0)

    def EmptyClipboard(self):
        assert self.opener
        self.empties += 1
        self.release()
        self.owner = self.opener
        self.sequence += 1
        return 1

    def SetClipboardData(self, format_id, handle):
        assert self.opener and self.owner == self.opener
        self.set_attempts += 1
        if self.set_attempts in self.fail_set_at:
            C.set_last_error(8)
            return 0
        if format_id in self.data:
            win._kernel32.GlobalFree(self.data[format_id])
        self.data[format_id] = handle
        self.sequence += 1
        return handle

    def GetClipboardSequenceNumber(self):
        return self.sequence

    def GetClipboardOwner(self):
        return self.owner

    def RegisterHotKey(self, hwnd, identifier, modifiers, vk):
        self.hotkey_events.append(("register", identifier, modifiers, vk))
        if self.hotkey_fail:
            C.set_last_error(1409)
            return 0
        self.registered[identifier] = (hwnd, modifiers, vk)
        return 1

    def UnregisterHotKey(self, hwnd, identifier):
        self.hotkey_events.append(("unregister", identifier))
        self.registered.pop(identifier)
        return 1


@pytest.fixture
def clipboard(monkeypatch):
    if os.name != "nt":
        pytest.skip("Native HGLOBAL allocation requires Windows; clipboard itself is fake.")
    fake = FakeClipboard()
    monkeypatch.setattr(win, "_user32", fake)
    try:
        yield fake
    finally:
        fake.release()


def text(value):
    return (value + "\0").encode("utf-16-le")


def test_clipboard_transaction_preserves_all_hglobal_formats(clipboard):
    html = clipboard.RegisterClipboardFormatW("HTML Format")
    clipboard.install({win.CF_UNICODETEXT: text("Исходный текст"), html: b"<b>original</b>\0"})
    original = clipboard.payloads()
    transaction = win.ClipboardTransaction(101)
    try:
        transaction.publish("Первая строка.\nВторая строка.")
        assert transaction.pending
        assert win._read_hglobal(clipboard.data[win.CF_UNICODETEXT]).startswith(
            text("Первая строка.\r\nВторая строка.")
        )
        assert transaction.restore() == "restored"
        assert clipboard.payloads() == original
        assert not transaction.pending
        assert clipboard.opener == 0
    finally:
        transaction.close()


def test_empty_clipboard_restored(clipboard):
    clipboard.owner = 0
    transaction = win.ClipboardTransaction(101)
    transaction.publish("Text")
    assert transaction.restore() == "restored"
    assert clipboard.data == {}


def test_no_owner_does_not_mean_empty_clipboard(clipboard):
    clipboard.install({win.CF_UNICODETEXT: text("still here")}, owner=0)
    original = clipboard.payloads()
    transaction = win.ClipboardTransaction(101)
    transaction.publish("Text")
    transaction.restore()
    assert clipboard.payloads() == original


@pytest.mark.parametrize("format_id", [0x0201, 0x0080, 0x0301])
def test_private_formats_fail_before_clear(clipboard, format_id):
    clipboard.install({win.CF_UNICODETEXT: text("Keep"), format_id: b"private"})
    original = clipboard.payloads()
    transaction = win.ClipboardTransaction(101)
    with pytest.raises(win.ClipboardError, match="не поддержан"):
        transaction.publish("Text")
    assert clipboard.empties == 0
    assert clipboard.payloads() == original
    assert not transaction.pending


@pytest.mark.parametrize("name", ["DataObject", "Ole Private Data", "FileContents", "UnknownApp"])
def test_untrusted_registered_formats_fail_closed(clipboard, name):
    custom = clipboard.RegisterClipboardFormatW(name)
    clipboard.install({custom: b"untrusted"})
    transaction = win.ClipboardTransaction(101)
    with pytest.raises(win.ClipboardError):
        transaction.publish("Text")
    assert clipboard.empties == 0


def test_enum_error_is_not_empty_snapshot(clipboard):
    clipboard.install({win.CF_UNICODETEXT: text("Keep")})
    clipboard.fail_enum = True
    transaction = win.ClipboardTransaction(101)
    with pytest.raises(win.ClipboardError):
        transaction.publish("Text")
    assert clipboard.empties == 0
    assert clipboard.opener == 0


def test_missing_format_does_not_mutate(clipboard):
    clipboard.install({win.CF_UNICODETEXT: text("Keep")})
    clipboard.missing_format = win.CF_UNICODETEXT
    transaction = win.ClipboardTransaction(101)
    with pytest.raises(win.ClipboardError):
        transaction.publish("Text")
    assert clipboard.empties == 0


def test_snapshot_limit_fails_before_mutation(clipboard, monkeypatch):
    clipboard.install({win.CF_UNICODETEXT: text("original is larger than the limit")})
    monkeypatch.setattr(win, "MAX_CLIPBOARD_BYTES", 20)
    transaction = win.ClipboardTransaction(101)
    with pytest.raises(win.ClipboardError) as error:
        transaction.publish("X")
    assert error.value.code == "CLIPBOARD_TOO_LARGE"
    assert clipboard.empties == 0


def test_busy_publish_can_retry(clipboard):
    transaction = win.ClipboardTransaction(101)
    clipboard.busy = True
    with pytest.raises(win.ClipboardBusy):
        transaction.publish("Text")
    assert not transaction.pending
    assert clipboard.empties == 0
    clipboard.busy = False
    transaction.publish("Text")
    assert transaction.restore() == "restored"


def test_busy_restore_retains_snapshot_for_retry(clipboard):
    clipboard.install({win.CF_UNICODETEXT: text("Keep")})
    original = clipboard.payloads()
    transaction = win.ClipboardTransaction(101)
    transaction.publish("Text")
    clipboard.busy = True
    with pytest.raises(win.ClipboardBusy):
        transaction.restore()
    assert transaction.pending
    clipboard.busy = False
    assert transaction.restore() == "restored"
    assert clipboard.payloads() == original


@pytest.mark.parametrize("copy_marker", [False, True])
def test_user_copy_always_wins_even_when_marker_is_republished(clipboard, copy_marker):
    clipboard.install({win.CF_UNICODETEXT: text("Keep")})
    transaction = win.ClipboardTransaction(101)
    transaction.publish("Text")
    new = {win.CF_UNICODETEXT: text("new user copy")}
    if copy_marker:
        new[transaction._marker] = transaction._token
    clipboard.install(new, owner=101 if copy_marker else 202)
    newer = clipboard.payloads()
    empties_before = clipboard.empties
    assert transaction.restore() == "superseded"
    assert clipboard.payloads() == newer
    assert clipboard.empties == empties_before


@pytest.mark.parametrize("changed", ["owner", "marker"])
def test_restore_checks_owner_and_marker_even_if_sequence_matches(clipboard, changed):
    transaction = win.ClipboardTransaction(101)
    transaction.publish("Text")
    if changed == "owner":
        clipboard.owner = 202
    else:
        old = clipboard.data[transaction._marker]
        clipboard.data[transaction._marker] = win._new_hglobal(b"different-token!")
        win._kernel32.GlobalFree(old)
    empties_before = clipboard.empties
    assert transaction.restore() == "superseded"
    assert clipboard.empties == empties_before


def test_set_failure_rolls_back_original_without_leaking_transferred_handles(clipboard):
    clipboard.install({win.CF_UNICODETEXT: text("Keep")})
    original = clipboard.payloads()
    clipboard.fail_set_at = {2}
    transaction = win.ClipboardTransaction(101)
    with pytest.raises(win.ClipboardError):
        transaction.publish("Text")
    assert clipboard.payloads() == original
    assert clipboard.opener == 0
    assert not transaction.pending


def test_partial_restore_keeps_independent_master_snapshot(clipboard):
    clipboard.install({win.CF_UNICODETEXT: text("Keep")})
    original = clipboard.payloads()
    transaction = win.ClipboardTransaction(101)
    transaction.publish("Text")
    clipboard.fail_set_at.add(clipboard.set_attempts + 1)
    try:
        with pytest.raises(win.ClipboardError) as error:
            transaction.restore()
        assert error.value.code == "CLIPBOARD_RESTORE_PARTIAL"
        assert not transaction.pending
        assert transaction.recovery_required
        with pytest.raises(win.ClipboardError):
            transaction.close()
        with pytest.raises(win.ClipboardError):
            transaction.restore()
        transaction.recover()  # Explicit recovery, never automatic.
        assert clipboard.payloads() == original
        assert not transaction.recovery_required
    finally:
        transaction.discard_recovery()


def test_publication_and_rollback_failure_preserves_recovery_master(clipboard):
    clipboard.install({win.CF_UNICODETEXT: text("Keep")})
    original = clipboard.payloads()
    clipboard.fail_set_at = {2, 3}
    transaction = win.ClipboardTransaction(101)
    try:
        with pytest.raises(win.ClipboardError) as error:
            transaction.publish("Text")
        assert error.value.code == "CLIPBOARD_RESTORE_PARTIAL"
        assert transaction.recovery_required
        assert not transaction.pending
        with pytest.raises(win.ClipboardError):
            transaction.close()
        clipboard.busy = True
        with pytest.raises(win.ClipboardBusy):
            transaction.recover()
        assert transaction.recovery_required
        clipboard.busy = False
        transaction.recover()
        assert clipboard.payloads() == original
    finally:
        transaction.discard_recovery()


def test_ole_image_transport_excluded_only_after_authoritative_probe(clipboard, monkeypatch):
    data_object = clipboard.RegisterClipboardFormatW("DataObject")
    ole_private = clipboard.RegisterClipboardFormatW("Ole Private Data")
    png = clipboard.RegisterClipboardFormatW("PNG")
    payload = b"\x89PNG\r\n\x1a\nsynthetic-test-payload"
    clipboard.install({data_object: b"transport", png: payload, ole_private: b"transport"})
    monkeypatch.setattr(win, "_ole_formats", lambda: [(png, 1, -1, 4, False)])
    transaction = win.ClipboardTransaction(101)
    transaction.publish("Text")
    transaction.restore()
    assert set(clipboard.data) == {png}
    assert clipboard.payloads()[png].startswith(payload)


@pytest.mark.parametrize(
    "descriptor",
    [
        ("transport", 1, -1, 1, False),
        ("image", 1, 0, 1, False),
        ("image", 2, -1, 1, False),
        ("image", 1, -1, 1, True),
        ("image", 1, -1, 8, False),
    ],
)
def test_ole_image_with_nonportable_descriptor_fails_closed(clipboard, monkeypatch, descriptor):
    data_object = clipboard.RegisterClipboardFormatW("DataObject")
    clipboard.install({data_object: b"transport", win.CF_DIB: b"image"})
    which, *rest = descriptor
    format_id = data_object if which == "transport" else win.CF_DIB
    monkeypatch.setattr(win, "_ole_formats", lambda: [(format_id, *rest)])
    transaction = win.ClipboardTransaction(101)
    with pytest.raises(win.ClipboardError):
        transaction.publish("Text")
    assert clipboard.empties == 0


def test_new_copy_during_ole_probe_aborts_before_mutation(clipboard, monkeypatch):
    data_object = clipboard.RegisterClipboardFormatW("DataObject")
    clipboard.install({data_object: b"transport", win.CF_DIB: b"image"})

    def changed_probe():
        clipboard.install({data_object: b"new transport", win.CF_DIB: b"new image"})
        return [(win.CF_DIB, 1, -1, 1, False)]

    monkeypatch.setattr(win, "_ole_formats", changed_probe)
    transaction = win.ClipboardTransaction(101)
    with pytest.raises(win.ClipboardError):
        transaction.publish("Text")
    assert clipboard.empties == 0
    assert clipboard.payloads()[win.CF_DIB].startswith(b"new image")


def test_prepare_all_restore_handles_before_empty(clipboard, monkeypatch):
    clipboard.install({win.CF_UNICODETEXT: text("Keep")})
    original = clipboard.payloads()
    transaction = win.ClipboardTransaction(101)
    monkeypatch.setattr(
        win.ClipboardSnapshot, "prepare", lambda self: (_ for _ in ()).throw(MemoryError("injected"))
    )
    with pytest.raises(MemoryError):
        transaction.publish("Text")
    assert clipboard.empties == 0
    assert clipboard.payloads() == original


def test_hotkey_rebind_retains_old_registration_on_conflict(clipboard):
    manager = win.HotkeyManager(101)
    manager.rebind("F4")
    old_id = manager.hotkey_id
    manager.rebind("F4")
    assert len(clipboard.hotkey_events) == 1
    clipboard.hotkey_fail = True
    with pytest.raises(win.WindowsError):
        manager.rebind("Ctrl+F5")
    assert manager.hotkey_id == old_id
    assert old_id in clipboard.registered
    clipboard.hotkey_fail = False
    manager.rebind("Ctrl+F5")
    assert old_id not in clipboard.registered
    assert len(clipboard.registered) == 1
    manager.close()
    assert not clipboard.registered


def test_x64_input_abi():
    if os.name != "nt" or C.sizeof(C.c_void_p) != 8:
        pytest.skip("Windows x64 ABI")
    assert C.sizeof(win.INPUT) == 40
    assert C.sizeof(win.KEYBDINPUT) == 24
    assert C.sizeof(win.MSG) == 48
    assert C.sizeof(win.GUITHREADINFO) == 72
    assert C.sizeof(win._FORMATETC) == 32


class FakeInput:
    def __init__(self, sent=4, down=()):
        self.sent = sent
        self.down = set(down)
        self.calls = []

    def GetAsyncKeyState(self, vk):
        return 0x8000 if vk in self.down else 0

    def SendInput(self, count, events, size):
        assert size == C.sizeof(win.INPUT)
        self.calls.append([(events[i].ki.wVk, events[i].ki.dwFlags) for i in range(count)])
        return self.sent if len(self.calls) == 1 else count


@pytest.mark.parametrize(
    ("shortcut", "expected"),
    [
        ("Ctrl+V", [(0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)]),
        ("Shift+Insert", [(0x10, 0), (0x2D, 1), (0x2D, 3), (0x10, 2)]),
    ],
)
def test_send_paste_sequence(monkeypatch, shortcut, expected):
    fake = FakeInput()
    monkeypatch.setattr(win, "_user32", fake)
    win.send_paste(shortcut)
    assert fake.calls == [expected]


def test_held_modifier_prevents_input(monkeypatch):
    fake = FakeInput(down=[0x12])
    monkeypatch.setattr(win, "_user32", fake)
    with pytest.raises(win.WindowsError) as error:
        win.send_paste("Ctrl+V")
    assert error.value.code == "MODIFIER_HELD"
    assert fake.calls == []


def test_partial_input_only_cleans_up_never_repeats_shortcut(monkeypatch):
    if os.name != "nt":
        pytest.skip("ctypes get_last_error is Windows-specific")
    fake = FakeInput(sent=2)
    monkeypatch.setattr(win, "_user32", fake)
    with pytest.raises(win.WindowsError) as error:
        win.send_paste("Ctrl+V")
    assert error.value.code == "INPUT_REJECTED_OR_PARTIAL"
    assert len(fake.calls) == 2
    assert all(flags & 2 for _, flags in fake.calls[1])


def test_target_identity_covers_pid_thread_and_focus(monkeypatch):
    class FakeTarget:
        hwnd, pid, tid, focus, flags = 100, 200, 300, 400, 0

        def GetForegroundWindow(self):
            return self.hwnd

        def IsWindow(self, hwnd):
            return bool(hwnd)

        def GetWindowThreadProcessId(self, hwnd, pid):
            C.cast(pid, C.POINTER(win.W.DWORD)).contents.value = self.pid
            return self.tid

        def GetGUIThreadInfo(self, tid, pointer):
            info = C.cast(pointer, C.POINTER(win.GUITHREADINFO)).contents
            assert info.cbSize == C.sizeof(win.GUITHREADINFO)
            info.hwndFocus, info.flags = self.focus, self.flags
            return 1

    fake = FakeTarget()
    monkeypatch.setattr(win, "_user32", fake)
    target = win.capture_target()
    assert win.target_is_current(target)
    for field in ("hwnd", "pid", "tid", "focus"):
        original = getattr(fake, field)
        setattr(fake, field, original + 1)
        assert not win.target_is_current(target)
        setattr(fake, field, original)
    fake.flags = 4
    assert win.capture_target() is None


@pytest.mark.skipif(os.name != "nt", reason="Native GDI objects require Windows")
def test_bitmap_copy_remains_valid_after_original_is_deleted():
    win._bind(
        win._gdi32,
        "CreateDIBSection",
        C.c_void_p,
        C.c_void_p,
        C.c_void_p,
        win.W.UINT,
        C.POINTER(C.c_void_p),
        C.c_void_p,
        win.W.DWORD,
    )
    header = C.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 32, 0, 16, 0, 0, 0, 0))
    pixels = bytes([0, 0, 255, 255, 0, 255, 0, 255, 255, 0, 0, 255, 255, 255, 255, 255])
    bits = C.c_void_p()
    original = win._gdi32.CreateDIBSection(None, header, 0, C.byref(bits), None, 0)
    assert original and bits.value
    clone = None
    try:
        C.memmove(bits, pixels, len(pixels))
        clone = win._clone_entry(win.CF_BITMAP, original, win.MAX_CLIPBOARD_BYTES)
        assert clone.handle != original
        assert win._gdi32.DeleteObject(original)
        original = 0
        bitmap = win._BITMAP()
        assert win._gdi32.GetObjectW(clone.handle, C.sizeof(bitmap), C.byref(bitmap))
        assert (bitmap.bmWidth, bitmap.bmHeight, bitmap.bmBitsPixel) == (2, 2, 32)
        assert C.string_at(bitmap.bmBits, len(pixels)) == pixels
    finally:
        if original:
            win._gdi32.DeleteObject(original)
        if clone is not None:
            clone.close()


@pytest.mark.skipif(os.name != "nt", reason="Native GDI objects require Windows")
def test_enhanced_metafile_copy_outlives_original():
    win._bind(
        win._gdi32, "CreateEnhMetaFileW", C.c_void_p, C.c_void_p, win.W.LPCWSTR, C.c_void_p, win.W.LPCWSTR
    )
    win._bind(win._gdi32, "CloseEnhMetaFile", C.c_void_p, C.c_void_p)
    win._bind(win._gdi32, "Rectangle", win.W.BOOL, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)
    dc = win._gdi32.CreateEnhMetaFileW(None, None, None, None)
    assert dc
    assert win._gdi32.Rectangle(dc, 0, 0, 8, 8)
    original = win._gdi32.CloseEnhMetaFile(dc)
    assert original
    clone = None
    try:
        size = win._gdi32.GetEnhMetaFileBits(original, 0, None)
        expected = C.create_string_buffer(size)
        assert win._gdi32.GetEnhMetaFileBits(original, size, expected) == size
        clone = win._clone_entry(win.CF_ENHMETAFILE, original, win.MAX_CLIPBOARD_BYTES)
        assert clone.handle != original
        assert win._gdi32.DeleteEnhMetaFile(original)
        original = 0
        actual = C.create_string_buffer(size)
        assert win._gdi32.GetEnhMetaFileBits(clone.handle, size, actual) == size
        assert actual.raw == expected.raw
    finally:
        if original:
            win._gdi32.DeleteEnhMetaFile(original)
        if clone is not None:
            clone.close()
