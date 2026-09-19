r"""Actual clipboard tests in a new, noninteractive Windows window station.

The child changes its own process window station before creating any HWND. It
never calls SendInput, SetForegroundWindow or SwitchDesktop. The user's clipboard
and interactive windows are outside this station and are never touched.
"""

from __future__ import annotations

import argparse
import ctypes as C
import os
import subprocess
import sys
import threading
import uuid
from ctypes import wintypes as W
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from windows_smoke import _api, _dib, _read_payload, _unicode  # noqa: E402

from voxturbo.platform import windows as win  # noqa: E402


def _set_payloads(hwnd: int, payloads: dict[int, bytes]) -> None:
    entries = []
    try:
        for fmt, data in payloads.items():
            entries.append(win._OwnedEntry(fmt, win._new_hglobal(data), "hglobal", len(data)))
        with win._open_clipboard(hwnd):
            win._empty_open()
            win._put_entries_open(entries)
    finally:
        win._close_entries(entries)


def _assert_equal_payload(hwnd: int, fmt: int, expected: bytes) -> None:
    actual = _read_payload(hwnd, fmt)
    if actual[: len(expected)] != expected:
        raise AssertionError(f"Clipboard payload mismatch for format {fmt}.")


def _child() -> int:
    _api()
    for name, result, args in [
        ("GetProcessWindowStation", C.c_void_p, ()),
        ("CreateWindowStationW", C.c_void_p, (W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p)),
        ("SetProcessWindowStation", W.BOOL, (C.c_void_p,)),
        ("CloseWindowStation", W.BOOL, (C.c_void_p,)),
        ("GetThreadDesktop", C.c_void_p, (W.DWORD,)),
        ("CreateDesktopW", C.c_void_p, (W.LPCWSTR, W.LPCWSTR, C.c_void_p, W.DWORD, W.DWORD, C.c_void_p)),
        ("SetThreadDesktop", W.BOOL, (C.c_void_p,)),
        ("CloseDesktop", W.BOOL, (C.c_void_p,)),
        ("SendMessageW", C.c_ssize_t, (C.c_void_p, W.UINT, C.c_size_t, C.c_ssize_t)),
    ]:
        win._bind(win._user32, name, result, *args)
    win._bind(win._kernel32, "GetCurrentThreadId", W.DWORD)
    previous_station = win._user32.GetProcessWindowStation()
    previous_desktop = win._user32.GetThreadDesktop(win._kernel32.GetCurrentThreadId())
    station = desktop = hwnd = None
    transactions = []
    try:
        station = win._user32.CreateWindowStationW(f"VoxTurboTest-{uuid.uuid4().hex}", 0, 0x003F, None)
        if not station:
            raise OSError(
                C.get_last_error(), "CreateWindowStationW was denied; interactive station unchanged."
            )
        if not win._user32.SetProcessWindowStation(station):
            raise OSError(
                C.get_last_error(), "SetProcessWindowStation was denied; no clipboard operation ran."
            )
        desktop = win._user32.CreateDesktopW("Default", None, None, 0, 0x0087, None)
        if not desktop:
            raise OSError(C.get_last_error(), "CreateDesktopW failed before any clipboard operation.")
        if not win._user32.SetThreadDesktop(desktop):
            raise OSError(C.get_last_error(), "SetThreadDesktop failed before any clipboard operation.")
        if win._user32.GetProcessWindowStation() != station:
            raise RuntimeError("Process is not in the isolated window station.")
        hwnd = win._user32.CreateWindowExW(
            0, "EDIT", "", 0x0004, 0, 0, 300, 120, None, None, win._kernel32.GetModuleHandleW(None), None
        )
        if not hwnd:
            raise C.WinError(C.get_last_error())
        hwnd = int(hwnd)
        html = win._register_format("HTML Format")
        source = {
            win.CF_DIB: _dib(),
            html: b"<html>test-fixture</html>\0",
            win.CF_UNICODETEXT: _unicode("Original fixture"),
        }
        _set_payloads(hwnd, source)
        source_dib = _read_payload(hwnd, win.CF_DIB)
        transaction = win.ClipboardTransaction(hwnd)
        transactions.append(transaction)
        transaction.publish("VoxTurbo: isolated clipboard 42.")
        win._user32.SendMessageW(hwnd, 0x0302, 0, 0)  # WM_PASTE, this exact private EDIT only.
        received = C.create_unicode_buffer(256)
        win._user32.GetWindowTextW(hwnd, received, len(received))
        assert received.value == "VoxTurbo: isolated clipboard 42."
        assert transaction.restore() == "restored"
        assert _read_payload(hwnd, win.CF_DIB) == source_dib
        _assert_equal_payload(hwnd, html, source[html])
        _assert_equal_payload(hwnd, win.CF_UNICODETEXT, source[win.CF_UNICODETEXT])
        print(
            "PASS: actual DIB/bitmap synthesis, Unicode, HTML snapshot/publish/restore; private EDIT WM_PASTE."
        )

        transaction = win.ClipboardTransaction(hwnd)
        transactions.append(transaction)
        transaction.publish("Temporary")
        new_copy = _unicode("New copy has priority")
        _set_payloads(hwnd, {win.CF_UNICODETEXT: new_copy})
        assert transaction.restore() == "superseded"
        _assert_equal_payload(hwnd, win.CF_UNICODETEXT, new_copy)
        print("PASS: actual newer copy wins, including the same owner HWND.")

        unsupported = win._register_format("VoxTurbo.Smoke.Unknown.Payload")
        _set_payloads(hwnd, {win.CF_UNICODETEXT: new_copy, unsupported: b"opaque\0"})
        sequence = win._user32.GetClipboardSequenceNumber()
        transaction = win.ClipboardTransaction(hwnd)
        transactions.append(transaction)
        try:
            transaction.publish("Must not replace")
        except win.ClipboardError as error:
            assert error.code == "CLIPBOARD_UNSUPPORTED_FORMAT"
        else:
            raise AssertionError("Unsupported format was not rejected.")
        assert win._user32.GetClipboardSequenceNumber() == sequence
        _assert_equal_payload(hwnd, unsupported, b"opaque\0")
        print("PASS: unknown format rejected before any clipboard mutation.")

        _set_payloads(hwnd, {win.CF_UNICODETEXT: new_copy})
        opened, release = threading.Event(), threading.Event()
        lock_errors = []

        def hold_clipboard():
            try:
                with win._open_clipboard(hwnd):
                    opened.set()
                    if not release.wait(5):
                        raise RuntimeError("Clipboard busy test release timed out.")
            except BaseException as error:
                lock_errors.append(error)
                opened.set()

        holder = threading.Thread(target=hold_clipboard)
        holder.start()
        try:
            assert opened.wait(3) and not lock_errors
            transaction = win.ClipboardTransaction(hwnd)
            transactions.append(transaction)
            try:
                transaction.publish("Must wait")
            except win.ClipboardBusy:
                pass
            else:
                raise AssertionError("Real busy clipboard did not reject OpenClipboard.")
        finally:
            release.set()
            holder.join(5)
        assert not holder.is_alive() and not lock_errors
        _assert_equal_payload(hwnd, win.CF_UNICODETEXT, new_copy)
        print("PASS: actual OpenClipboard contention is nonblocking and preserves the original.")
        print(
            "NOT RUN: SendInput/global hotkey/foreground; noninteractive station intentionally excludes input."
        )
        return 0
    finally:
        for transaction in transactions:
            # This station contains only disposable fixtures, never user data.
            transaction.discard_recovery()
        if hwnd:
            win._user32.DestroyWindow(hwnd)
        if previous_desktop:
            win._user32.SetThreadDesktop(previous_desktop)
        if previous_station:
            win._user32.SetProcessWindowStation(previous_station)
        if desktop:
            win._user32.CloseDesktop(desktop)
        if station:
            win._user32.CloseWindowStation(station)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.name != "nt":
        print("SKIP: Windows required.")
        return 2
    if args.child:
        return _child()
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--child"],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
