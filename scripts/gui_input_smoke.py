"""Native input smoke in owned windows only; never opens the clipboard or microphone.

Run explicitly on an otherwise idle interactive desktop:
    python scripts/gui_input_smoke.py --allow-interactive
"""

from __future__ import annotations

import argparse
import ctypes as C
import json
import os
import platform
import sys
import time
from ctypes import wintypes as W
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QVBoxLayout, QWidget

from voxturbo.platform.windows import (
    INPUT,
    MSG,
    WM_HOTKEY,
    HotkeyManager,
    capture_target,
    modifiers_pressed,
)
from voxturbo.ui import FloatingWidget

WM_MOUSEACTIVATE = 0x0021
WM_LBUTTONDOWN = 0x0201
WS_EX_NOACTIVATE = 0x08000000
VK_F24 = 0x87


def bind(dll, name, result, *arguments):
    function = getattr(dll, name)
    function.restype, function.argtypes = result, arguments
    return function


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, smoke):
        super().__init__()
        self.smoke = smoke

    def nativeEventFilter(self, event_type, message):
        msg = MSG.from_address(int(message))
        if (
            msg.message == WM_HOTKEY
            and msg.hwnd == self.smoke.receiver_hwnd
            and msg.wParam == self.smoke.hotkeys.hotkey_id
        ):
            self.smoke.hotkey_events += 1
            return True, 0
        return False, 0


class Smoke:
    def __init__(self, app, report_path):
        self.app = app
        self.report_path = report_path
        self.started = time.monotonic()
        self.finished = False
        self.moved_cursor = False
        self.click_point = None
        self.hotkeys = None
        self.filter = None
        self.hotkey_events = 0
        self.toggle_events = 0
        self.report = {
            "ok": False,
            "platform": platform.platform(),
            "clipboard_accessed": False,
            "microphone_opened": False,
            "test": "owned QLineEdit + actual FloatingWidget + F24 RegisterHotKey/SendInput",
        }
        self.user32 = C.WinDLL("user32", use_last_error=True)
        self.foreground = bind(self.user32, "GetForegroundWindow", C.c_void_p)
        self.set_foreground = bind(self.user32, "SetForegroundWindow", W.BOOL, C.c_void_p)
        self.get_exstyle = bind(self.user32, "GetWindowLongPtrW", C.c_ssize_t, C.c_void_p, C.c_int)
        self.send_message = bind(
            self.user32, "SendMessageW", C.c_ssize_t, C.c_void_p, W.UINT, C.c_size_t, C.c_ssize_t
        )
        self.get_rect = bind(self.user32, "GetWindowRect", W.BOOL, C.c_void_p, C.POINTER(W.RECT))
        self.get_cursor = bind(self.user32, "GetCursorPos", W.BOOL, C.POINTER(W.POINT))
        self.set_cursor = bind(self.user32, "SetCursorPos", W.BOOL, C.c_int, C.c_int)
        self.window_at = bind(self.user32, "WindowFromPoint", C.c_void_p, W.POINT)
        self.get_ancestor = bind(self.user32, "GetAncestor", C.c_void_p, C.c_void_p, W.UINT)
        self.get_pid = bind(self.user32, "GetWindowThreadProcessId", W.DWORD, C.c_void_p, C.POINTER(W.DWORD))
        self.key_state = bind(self.user32, "GetAsyncKeyState", C.c_short, C.c_int)
        self.send_input = bind(self.user32, "SendInput", W.UINT, W.UINT, C.POINTER(INPUT), C.c_int)
        self.original_cursor = W.POINT()
        if not self.get_cursor(C.byref(self.original_cursor)):
            raise RuntimeError("Cannot read initial cursor position")

        self.receiver = QWidget()
        self.receiver.setWindowTitle("VoxTurbo — выделенный тест ввода, без буфера обмена")
        layout = QVBoxLayout(self.receiver)
        layout.addWidget(QLabel("Тест F24 и плавающей кнопки. Микрофон и буфер не используются."))
        self.edit = QLineEdit("VoxTurbo input smoke fixture")
        layout.addWidget(self.edit)
        self.receiver.resize(540, 110)
        screen = app.primaryScreen().availableGeometry()
        self.receiver.move(screen.center().x() - 270, screen.center().y() - 100)
        self.receiver.show()
        self.receiver_hwnd = int(self.receiver.winId())
        self.edit_hwnd = int(self.edit.winId())
        self.popup = FloatingWidget()
        self.popup.set_status("Тест плавающей кнопки")
        self.popup.move(self.receiver.x() + 140, self.receiver.y() + 145)
        self.popup.toggled.connect(self.on_toggle)
        self.popup.show()
        self.popup_hwnd = int(self.popup.winId())
        self.edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.receiver.raise_()
        self.receiver.activateWindow()
        # Only request foreground for the explicitly created receiver. No AttachThreadInput,
        # forced activation of another app, Alt key injection or retry loop is used.
        self.set_foreground(self.receiver_hwnd)
        self.filter = HotkeyFilter(self)
        app.installNativeEventFilter(self.filter)
        QTimer.singleShot(350, lambda: self.run_step(self.test_hotkey))
        QTimer.singleShot(6500, lambda: self.finish(False, "Smoke exceeded its 6.5 second deadline"))

    def on_toggle(self):
        self.toggle_events += 1

    def require_owned_focus(self):
        foreground = self.foreground()
        pid = W.DWORD()
        self.get_pid(self.receiver_hwnd, C.byref(pid))
        target = capture_target()
        self.report["focus_checks"] = {
            "foreground_is_receiver": foreground == self.receiver_hwnd,
            "qt_focus_is_edit": self.app.focusWidget() is self.edit,
            "native_focus_is_receiver": bool(target and target.focused_hwnd == self.receiver_hwnd),
            "native_focus_is_edit": bool(target and target.focused_hwnd == self.edit_hwnd),
        }
        if (
            foreground != self.receiver_hwnd
            or pid.value != os.getpid()
            or target is None
            or target.hwnd != self.receiver_hwnd
            or target.focused_hwnd not in (self.edit_hwnd, self.receiver_hwnd)
            or self.app.focusWidget() is not self.edit
        ):
            raise RuntimeError("Owned receiver/focus is no longer current; no input was injected")
        if modifiers_pressed():
            raise RuntimeError("A modifier is held; input smoke aborted")

    def run_step(self, function):
        if self.finished:
            return
        try:
            function()
        except Exception as exc:
            self.finish(False, str(exc))

    def send_owned(self, events):
        self.require_owned_focus()
        sent = self.send_input(len(events), events, C.sizeof(INPUT))
        if sent != len(events):
            # Release an injected prefix only when the receiver still owns focus.
            # No retry of a complete click/shortcut, which could duplicate actions.
            if sent == 1:
                self.require_owned_focus()
                release = (INPUT * 1)(events[-1])
                self.send_input(1, release, C.sizeof(INPUT))
            raise RuntimeError(f"SendInput accepted only {sent}/{len(events)} events")

    def test_hotkey(self):
        self.require_owned_focus()
        if self.key_state(VK_F24) & 0x8000:
            raise RuntimeError("F24 is already held; test aborted")
        self.hotkeys = HotkeyManager(self.receiver_hwnd)
        self.hotkeys.rebind("F24")
        events = (INPUT * 2)()
        events[0].type = events[1].type = 1
        events[0].ki.wVk = events[1].ki.wVk = VK_F24
        events[1].ki.dwFlags = 0x0002
        self.send_owned(events)
        QTimer.singleShot(200, lambda: self.run_step(self.test_popup))

    def test_popup(self):
        self.require_owned_focus()
        if self.hotkey_events != 1:
            raise RuntimeError(f"Expected one native WM_HOTKEY, got {self.hotkey_events}")
        self.report["f24_wm_hotkey_count"] = self.hotkey_events
        style = self.get_exstyle(self.popup_hwnd, -20)
        self.report["popup_ws_ex_noactivate"] = bool(style & WS_EX_NOACTIVATE)
        if not style & WS_EX_NOACTIVATE:
            raise RuntimeError("FloatingWidget has no native WS_EX_NOACTIVATE style")
        activation = self.send_message(
            self.popup_hwnd, WM_MOUSEACTIVATE, self.receiver_hwnd, (WM_LBUTTONDOWN << 16) | 1
        )
        self.report["wm_mouseactivate_result"] = int(activation)
        if activation != 3:
            raise RuntimeError("FloatingWidget did not return MA_NOACTIVATE")
        rect = W.RECT()
        if not self.get_rect(self.popup_hwnd, C.byref(rect)):
            raise RuntimeError("Cannot read owned popup coordinates")
        point = W.POINT((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        # WindowFromPoint uses physical Windows coordinates, avoiding Qt DPI ambiguity.
        if self.get_ancestor(self.window_at(point), 2) != self.popup_hwnd:
            raise RuntimeError("Owned popup is covered by another window; no click injected")
        pid = W.DWORD()
        self.get_pid(self.popup_hwnd, C.byref(pid))
        if pid.value != os.getpid():
            raise RuntimeError("Popup does not belong to this process")
        if self.key_state(1) & 0x8000:
            raise RuntimeError("Left mouse button is already held")
        self.require_owned_focus()
        if not self.set_cursor(point.x, point.y):
            raise RuntimeError("Cannot move cursor to the owned popup")
        self.moved_cursor = True
        self.click_point = point
        actual = W.POINT()
        if not self.get_cursor(C.byref(actual)) or (actual.x, actual.y) != (point.x, point.y):
            raise RuntimeError("Cursor moved unexpectedly; no click injected")
        if self.get_ancestor(self.window_at(actual), 2) != self.popup_hwnd:
            raise RuntimeError("Popup is no longer under cursor; no click injected")
        events = (INPUT * 2)()
        events[0].type = events[1].type = 0
        events[0].mi.dwFlags = 0x0002  # MOUSEEVENTF_LEFTDOWN
        events[1].mi.dwFlags = 0x0004  # MOUSEEVENTF_LEFTUP
        self.send_owned(events)
        QTimer.singleShot(200, lambda: self.run_step(self.check_popup_result))

    def check_popup_result(self):
        self.require_owned_focus()
        self.report["popup_toggle_count"] = self.toggle_events
        self.report["receiver_focus_preserved"] = True
        self.report["receiver_fixture_unchanged"] = self.edit.text() == "VoxTurbo input smoke fixture"
        if self.toggle_events != 1 or not self.report["receiver_fixture_unchanged"]:
            raise RuntimeError("Popup click did not toggle exactly once or modified the receiver")
        self.finish(True)

    def finish(self, ok, error=None):
        if self.finished:
            return
        self.finished = True
        cleanup_errors = []
        if self.moved_cursor:
            current = W.POINT()
            if self.get_cursor(C.byref(current)) and (current.x, current.y) == (
                self.click_point.x,
                self.click_point.y,
            ):
                self.report["cursor_restored"] = bool(
                    self.set_cursor(self.original_cursor.x, self.original_cursor.y)
                )
            else:
                self.report["cursor_restored"] = False
                self.report["new_cursor_position_preserved"] = True
        if self.hotkeys is not None:
            try:
                self.hotkeys.close()
            except Exception as exc:
                cleanup_errors.append(str(exc))
        if self.filter is not None:
            self.app.removeNativeEventFilter(self.filter)
        self.popup.close()
        self.receiver.close()
        self.report.update(
            ok=bool(ok and not cleanup_errors),
            elapsed_seconds=round(time.monotonic() - self.started, 3),
            cleanup_errors=cleanup_errors,
        )
        if error:
            self.report["error"] = error
        try:
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            self.report_path.write_text(
                json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps(self.report, ensure_ascii=True), flush=True)
        except Exception as exc:
            self.report["ok"] = False
            print(f"Cannot save smoke report: {exc}", file=sys.stderr, flush=True)
        finally:
            self.app.exit(0 if self.report["ok"] else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-interactive", action="store_true")
    parser.add_argument(
        "--report", type=Path, default=Path(__file__).resolve().parents[1] / "build/qa/gui-input-smoke.json"
    )
    args = parser.parse_args()
    if not args.allow_interactive:
        parser.error("Use --allow-interactive only for the dedicated foreground test")
    if sys.platform != "win32" or C.sizeof(C.c_void_p) != 8:
        parser.error("This native smoke requires Windows x64")
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    smoke = Smoke(app, args.report.resolve())
    result = app.exec()
    if not smoke.finished:
        smoke.finish(False, "Qt event loop ended before checks finished")
        return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
