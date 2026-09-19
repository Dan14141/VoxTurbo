r"""Opt-in actual Win32 integration in a dedicated EDIT window.

Run with .venv\Scripts\python scripts/windows_smoke.py --run.
The existing clipboard must be fully snapshot-compatible, otherwise no mutation
is performed. Never sends input unless the dedicated test window still has focus.
"""

from __future__ import annotations

import argparse
import ctypes as C
import os
import struct
import sys
import time
from ctypes import wintypes as W
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from voxturbo.platform import windows as win  # noqa: E402

PASTE_TEXT = "VoxTurbo: проверка 42."


def _api() -> None:
    for name, result, arguments in [
        (
            "CreateWindowExW",
            C.c_void_p,
            (
                W.DWORD,
                W.LPCWSTR,
                W.LPCWSTR,
                W.DWORD,
                C.c_int,
                C.c_int,
                C.c_int,
                C.c_int,
                C.c_void_p,
                C.c_void_p,
                C.c_void_p,
                C.c_void_p,
            ),
        ),
        ("DestroyWindow", W.BOOL, (C.c_void_p,)),
        ("ShowWindow", W.BOOL, (C.c_void_p, C.c_int)),
        ("SetForegroundWindow", W.BOOL, (C.c_void_p,)),
        ("SetFocus", C.c_void_p, (C.c_void_p,)),
        ("SetWindowTextW", W.BOOL, (C.c_void_p, W.LPCWSTR)),
        ("GetWindowTextW", C.c_int, (C.c_void_p, W.LPWSTR, C.c_int)),
        ("PeekMessageW", W.BOOL, (C.POINTER(win.MSG), C.c_void_p, W.UINT, W.UINT, W.UINT)),
        ("TranslateMessage", W.BOOL, (C.POINTER(win.MSG),)),
        ("DispatchMessageW", C.c_ssize_t, (C.POINTER(win.MSG),)),
    ]:
        win._bind(win._user32, name, result, *arguments)
    win._bind(win._kernel32, "GetModuleHandleW", C.c_void_p, W.LPCWSTR)


def _pump(seconds: float, hotkeys: list[int] | None = None) -> None:
    deadline = time.monotonic() + seconds
    message = win.MSG()
    while time.monotonic() < deadline:
        while win._user32.PeekMessageW(C.byref(message), None, 0, 0, 1):
            if message.message == win.WM_HOTKEY and hotkeys is not None:
                hotkeys.append(int(message.wParam))
            win._user32.TranslateMessage(C.byref(message))
            win._user32.DispatchMessageW(C.byref(message))
        time.sleep(0.005)


def _dib() -> bytes:
    # A 2x2, 32-bit uncompressed BITMAPINFOHEADER and four BGRA pixels.
    return struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 32, 0, 16, 0, 0, 0, 0) + bytes(
        [
            0,
            0,
            255,
            255,
            0,
            255,
            0,
            255,
            255,
            0,
            0,
            255,
            255,
            255,
            255,
            255,
        ]
    )


def _unicode(value: str) -> bytes:
    return (value + "\0").encode("utf-16-le")


def _retry_clipboard(operation, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while True:
        try:
            return operation()
        except win.ClipboardBusy:
            if time.monotonic() >= deadline:
                raise
            _pump(0.04)


def _put_fixture(hwnd: int, original: win.ClipboardSnapshot) -> int:
    html = win._register_format("HTML Format")
    payloads = [
        (win.CF_DIB, _dib()),
        (html, b"<html><body><b>fixture</b></body></html>\0"),
        (win.CF_UNICODETEXT, _unicode("VoxTurbo original fixture")),
    ]
    prepared = []
    try:
        for format_id, data in payloads:
            prepared.append(win._OwnedEntry(format_id, win._new_hglobal(data), "hglobal", len(data)))
        with win._open_clipboard(hwnd):
            if (
                win._user32.GetClipboardSequenceNumber() != original.sequence
                or int(win._user32.GetClipboardOwner() or 0) != original.owner
            ):
                raise RuntimeError("Clipboard changed after snapshot; refusing to replace it.")
            win._empty_open()
            win._put_entries_open(prepared)
        return int(win._user32.GetClipboardSequenceNumber())
    finally:
        win._close_entries(prepared)


def _read_payload(hwnd: int, format_id: int) -> bytes:
    with win._open_clipboard(hwnd):
        handle = win._user32.GetClipboardData(format_id)
        if not handle:
            raise RuntimeError(f"Missing clipboard format {format_id}.")
        return win._read_hglobal(int(handle))


def run() -> int:
    if os.name != "nt":
        print("SKIP: Windows is required.")
        return 2
    _api()
    if C.sizeof(win.INPUT) != 40:
        raise RuntimeError("Expected Windows x64 INPUT ABI.")
    hwnd = win._user32.CreateWindowExW(
        0x00040000,
        "EDIT",
        "VoxTurbo isolated smoke receiver",
        0x00CF0000 | 0x0004 | 0x0040,
        120,
        120,
        620,
        240,
        None,
        None,
        win._kernel32.GetModuleHandleW(None),
        None,
    )
    if not hwnd:
        raise C.WinError(C.get_last_error())
    hwnd = int(hwnd)
    original = None
    transaction = None
    manager = None
    our_sequence = None
    result = 1
    try:
        try:
            original = _retry_clipboard(lambda: win.ClipboardSnapshot.capture(hwnd))
        except win.ClipboardError as error:
            print(f"SKIP: original clipboard cannot be safely preserved ({error.code}): {error}; unchanged.")
            return 2
        win._user32.ShowWindow(hwnd, 5)
        win._user32.SetForegroundWindow(hwnd)
        win._user32.SetFocus(hwnd)
        _pump(0.15)
        target = win.capture_target()
        if target is None or target.hwnd != hwnd or target.focused_hwnd != hwnd:
            print("SKIP: dedicated receiver did not obtain focus; no input sent.")
            return 2
        if win.modifiers_pressed():
            print("SKIP: physical modifier is held; no input sent.")
            return 2

        # A real global registration and WM_HOTKEY event, confined to our window.
        manager = win.HotkeyManager(hwnd)
        manager.rebind("F24")
        if not win.target_is_current(target):
            raise RuntimeError("Target changed before hotkey smoke; no input sent.")
        events = (win.INPUT * 2)(win._key_input(0x87), win._key_input(0x87, 2))
        if win._user32.SendInput(2, events, C.sizeof(win.INPUT)) != 2:
            raise RuntimeError("Windows rejected the hotkey smoke input.")
        observed = []
        _pump(0.1, observed)
        if observed != [manager.hotkey_id]:
            raise RuntimeError("Expected exactly one WM_HOTKEY from the test F24 key.")
        manager.close()
        manager = None

        our_sequence = _retry_clipboard(lambda: _put_fixture(hwnd, original))
        baseline_dib = _retry_clipboard(lambda: _read_payload(hwnd, win.CF_DIB))
        original_fixture = _retry_clipboard(lambda: _read_payload(hwnd, win.CF_UNICODETEXT))
        our_sequence = int(win._user32.GetClipboardSequenceNumber())
        for shortcut in ("Ctrl+V", "Shift+Insert"):
            if not win.target_is_current(target):
                raise RuntimeError("Target changed before paste smoke; no input sent.")
            win._user32.SetWindowTextW(hwnd, "")
            transaction = win.ClipboardTransaction(hwnd)
            _retry_clipboard(lambda: transaction.publish(PASTE_TEXT))
            our_sequence = int(win._user32.GetClipboardSequenceNumber())
            if not win.target_is_current(target):
                raise RuntimeError("Target changed after publication; no input sent.")
            win.send_paste(shortcut)
            _pump(0.7)
            received = C.create_unicode_buffer(1024)
            win._user32.GetWindowTextW(hwnd, received, len(received))
            if received.value != PASTE_TEXT:
                current = win.capture_target()
                raise RuntimeError(
                    f"Dedicated EDIT did not receive the expected text via {shortcut}: "
                    f"received={received.value!r} foreground={int(win._user32.GetForegroundWindow() or 0)} "
                    f"receiver={hwnd} captured={(target.hwnd, target.focused_hwnd)} "
                    f"current={'none' if current is None else (current.hwnd, current.focused_hwnd)} "
                    f"modifiers={win.modifiers_pressed()} sequence={int(win._user32.GetClipboardSequenceNumber())}"
                )
            if _retry_clipboard(transaction.restore) != "restored":
                raise RuntimeError("Clipboard changed externally; test restore was correctly skipped.")
            transaction.close()
            transaction = None
            our_sequence = int(win._user32.GetClipboardSequenceNumber())
            if _retry_clipboard(lambda: _read_payload(hwnd, win.CF_DIB)) != baseline_dib:
                raise RuntimeError("DIB changed across the paste transaction.")
            if _retry_clipboard(lambda: _read_payload(hwnd, win.CF_UNICODETEXT)) != original_fixture:
                raise RuntimeError("Original fixture text changed across the paste transaction.")
            our_sequence = int(win._user32.GetClipboardSequenceNumber())
            print(f"PASS: {shortcut}, exact Unicode in dedicated EDIT, DIB and original text restored.")
        print("PASS: real RegisterHotKey/WM_HOTKEY, target identity, Windows x64 ABI.")
        result = 0
    except Exception as error:
        print(f"FAIL: {type(error).__name__}: {error}")
        result = 1
    finally:
        if transaction is not None:
            try:
                if transaction.pending:
                    outcome = _retry_clipboard(transaction.restore)
                    if outcome == "restored":
                        our_sequence = int(win._user32.GetClipboardSequenceNumber())
            except win.ClipboardError:
                print("FAIL: pending test transaction could not be restored.")
                result = 1
            if transaction.recovery_required:
                # This transaction's original is our disposable synthetic fixture;
                # the external user's original has its own separate snapshot.
                transaction.discard_recovery()
                print(
                    "FAIL: test fixture requires recovery; no unconditional user clipboard overwrite attempted."
                )
                result = 1
            else:
                transaction.close()
        if manager is not None:
            manager.close()
        if original is not None:
            prepared = []
            try:
                if our_sequence is not None:
                    prepared = original.prepare()

                    def restore_original():
                        with win._open_clipboard(hwnd):
                            if (
                                int(win._user32.GetClipboardOwner() or 0) == hwnd
                                and win._user32.GetClipboardSequenceNumber() == our_sequence
                            ):
                                win._empty_open()
                                win._put_entries_open(prepared)
                                print("PASS: original external clipboard restored.")
                            else:
                                print(
                                    "SKIP: newer external clipboard preserved; original snapshot discarded."
                                )

                    _retry_clipboard(restore_original, timeout=10.0)
            except win.ClipboardError as error:
                print(f"FAIL: external clipboard restoration failed ({error.code}).")
                result = 1
            finally:
                win._close_entries(prepared)
                original.close()
        win._user32.DestroyWindow(hwnd)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run the explicit interactive test.")
    args = parser.parse_args()
    if not args.run:
        parser.error("Pass --run on a free interactive desktop; this opens one dedicated test window.")
    raise SystemExit(run())
