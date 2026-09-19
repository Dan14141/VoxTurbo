"""Controller state/ownership tests: no windows, microphone, hotkey or clipboard I/O."""

from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from voxturbo import controller
from voxturbo.config import Settings


class Signal:
    def connect(self, callback):
        self.callback = callback


class View:
    def __init__(self, text=""):
        self.text_value = text
        self.enabled = True
        self.visible = True
        self.value = 0
        self.closed = False

    def setText(self, text):
        self.text_value = text

    def setPlainText(self, text):
        self.text_value = text

    def toPlainText(self):
        return self.text_value

    def setEnabled(self, enabled):
        self.enabled = enabled

    def setVisible(self, visible):
        self.visible = visible

    def setValue(self, value):
        self.value = value

    def hide(self):
        self.visible = False

    def close(self):
        self.closed = True

    def setToolTip(self, text):
        self.tooltip = text


class Combo:
    def __init__(self):
        self.items = []

    def clear(self):
        self.items.clear()

    def addItem(self, text, data):
        self.items.append((text, data))

    def findData(self, data):
        return next((i for i, item in enumerate(self.items) if item[1] == data), -1)

    def setCurrentIndex(self, index):
        self.index = index

    def currentData(self):
        return None


class Field:
    """Минимальная замена QLineEdit/QComboBox/QSpinBox/QCheckBox для настроек."""

    def __init__(self, text="", data=None):
        self._text, self._data = text, data

    def text(self):
        return self._text

    def currentText(self):
        return self._text

    def currentData(self):
        return self._data

    def value(self):
        return self._data

    def isChecked(self):
        return bool(self._data)


class Slider:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value

    def setValue(self, value):
        self._value = value


class Timer:
    def __init__(self, *_):
        self.timeout = Signal()
        self.running = False

    def setInterval(self, value):
        self.interval = value

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class Recorder:
    def __init__(self):
        self.level = self.elapsed = 0.0
        self.limit_reached = False
        self.starts = self.stops = self.cancels = 0
        self.cancel_failure = False
        self.limit_seconds = None

    def start(self, device=None, limit_seconds=30):
        self.starts += 1
        self.limit_seconds = limit_seconds

    def stop(self):
        self.stops += 1
        return object()

    def cancel(self):
        self.cancels += 1
        if self.cancel_failure:
            raise RuntimeError("microphone unplugged")


class Executor:
    def __init__(self, **_):
        self.jobs = []
        self.closed = False

    def submit(self, function, *args):
        future = Future()
        self.jobs.append((function, args, future))
        return future

    def shutdown(self, **_):
        self.closed = True


class Hotkeys:
    def __init__(self, _hwnd):
        self.hotkey_id = 1
        self.closed = False
        self.close_failure = False

    def rebind(self, value):
        self.value = value

    def close(self):
        self.closed = True
        if self.close_failure:
            raise RuntimeError("UnregisterHotKey failed")


class Floating(View):
    def __init__(self):
        super().__init__()
        self.toggled = Signal()

    def set_status(self, *args):
        self.status = args


class Transaction:
    def __init__(self):
        self.pending = False
        self.recovery_required = False
        self.master_alive = False
        self.publish_attempts = 0
        self.restore_attempts = 0
        self.close_count = 0
        self.recover_count = 0
        self.discard_count = 0
        self.publish_errors = []
        self.restore_errors = []
        self.on_publish = lambda: None
        self.outcome = "restored"

    def publish(self, _text):
        self.publish_attempts += 1
        if self.publish_errors:
            raise self.publish_errors.pop(0)
        self.pending = self.master_alive = True
        self.on_publish()

    def restore(self):
        self.restore_attempts += 1
        if self.restore_errors:
            error = self.restore_errors.pop(0)
            if error == "partial":
                self.pending = False
                self.recovery_required = True
                raise RuntimeError("partial SetClipboardData failure")
            raise error
        self.pending = False
        self.master_alive = False
        return self.outcome

    def recover(self):
        self.recover_count += 1
        self.recovery_required = False
        self.close()

    def discard_recovery(self):
        self.discard_count += 1
        self.recovery_required = False
        self.close()

    def close(self):
        if self.recovery_required:
            raise RuntimeError("Retained original requires an explicit decision")
        self.close_count += 1
        self.master_alive = self.pending = False


@pytest.fixture
def harness(tmp_path, monkeypatch):
    fields = (
        "message model_status download_button status_label record_button cancel_button "
        "save_button copy_button download_progress recovery_row level_bar time_label transcript"
    ).split()
    window = SimpleNamespace(**{name: View() for name in fields})
    window.status_pill = View()
    window.status_pill.states = []
    window.status_pill.set_state = lambda state, text: window.status_pill.states.append((state, text))
    window.set_hotkey_hint = lambda hotkey: setattr(window, "hotkey_hint", hotkey)
    window.tones = []
    window.set_recording_tone = window.tones.append
    signal_names = (
        "toggle_requested cancel_requested download_requested save_requested copy_requested "
        "refresh_devices_requested quit_requested recover_requested discard_recovery_requested"
    ).split()
    for name in signal_names:
        setattr(window, name, Signal())
    window.mic_combo = Combo()
    window.hotkey_edit = Field("F4")
    window.paste_combo = Field("Ctrl+V")
    window.delay_spin = Field(data=700)
    window.widget_check = Field(data=True)
    window.duration_slider = Slider(30)
    window.winId = lambda: 123  # Never reaches Windows because all boundary objects are replaced.
    window.tray = View()
    window.allow_close = False
    window.closed = False
    window.opened = False
    window.close = lambda: setattr(window, "closed", True)
    window.open_window = lambda: setattr(window, "opened", True)
    app = SimpleNamespace(quit_called=False, filters=[])
    app.installNativeEventFilter = app.filters.append
    app.removeNativeEventFilter = app.filters.remove
    app.quit = lambda: setattr(app, "quit_called", True)
    state = SimpleNamespace(
        now=100.0,
        current=True,
        modifiers=False,
        transactions=[],
        sent=[],
        transaction=Transaction(),
        answer=controller.QMessageBox.StandardButton.Yes,
        constructor_failure=False,
    )

    def create_transaction(_hwnd):
        if state.constructor_failure:
            raise RuntimeError("RegisterClipboardFormat failed")
        state.transactions.append(state.transaction)
        return state.transaction

    monkeypatch.setattr(controller, "AudioRecorder", Recorder)
    monkeypatch.setattr(controller, "ThreadPoolExecutor", Executor)
    monkeypatch.setattr(controller, "FloatingWidget", Floating)
    monkeypatch.setattr(controller, "HotkeyManager", Hotkeys)
    monkeypatch.setattr(controller, "HotkeyFilter", lambda _: object())
    monkeypatch.setattr(controller, "QTimer", Timer)
    monkeypatch.setattr(controller, "list_input_devices", lambda: [])
    monkeypatch.setattr(controller, "ModelStore", lambda _: SimpleNamespace(is_ready=lambda: True))
    monkeypatch.setattr(controller, "ParakeetEngine", lambda _: SimpleNamespace(transcribe=lambda _: ""))
    monkeypatch.setattr(controller, "capture_target", lambda: SimpleNamespace(pid=-1))
    monkeypatch.setattr(controller, "target_is_current", lambda _: state.current)
    monkeypatch.setattr(controller, "modifiers_pressed", lambda: state.modifiers)
    monkeypatch.setattr(controller, "send_paste", state.sent.append)
    monkeypatch.setattr(controller, "ClipboardTransaction", create_transaction)
    monkeypatch.setattr(controller, "time", SimpleNamespace(monotonic=lambda: state.now))
    monkeypatch.setattr(controller.QMessageBox, "question", lambda *_: state.answer)
    state.controller = controller.AppController(app, window, Settings(), tmp_path)
    state.app, state.window = app, window

    def start_transcription():
        state.controller.toggle()
        state.controller.toggle()
        return state.controller.future

    def finish_transcription(text="Готовый текст."):
        start_transcription().set_result(text)
        state.controller.tick()

    state.start_transcription = start_transcription
    state.finish_transcription = finish_transcription
    return state


def test_late_cancelled_result_is_never_shown_or_published(harness):
    h = harness
    h.window.transcript.setPlainText("Предыдущий текст")
    future = h.start_transcription()
    h.controller.cancel()
    h.controller.toggle()  # Busy while the native inference is still running.
    assert h.controller.recorder.starts == 1
    assert h.controller.state == "transcribing"
    future.set_result("Отменённый результат")
    h.controller.tick()
    assert h.controller.future is None
    assert h.controller.state == "idle"
    assert h.window.transcript.toPlainText() == "Предыдущий текст"
    assert h.transactions == h.sent == []


def test_focus_changed_during_recognition_keeps_text_without_publishing(harness):
    h = harness
    future = h.start_transcription()
    h.current = False
    future.set_result("Сохраните этот текст.")
    h.controller.tick()
    assert h.controller.state == "idle"
    assert h.window.transcript.toPlainText() == "Сохраните этот текст."
    assert h.transactions == h.sent == []


def test_focus_changed_after_publication_rolls_back_without_input(harness):
    h = harness
    h.transaction.on_publish = lambda: setattr(h, "current", False)
    h.finish_transcription()
    assert h.sent == []
    assert h.transaction.publish_attempts == h.transaction.restore_attempts == 1
    assert h.controller.state == "idle"
    assert not h.transaction.master_alive


def test_busy_publication_is_retried_but_paste_is_sent_only_once(harness):
    h = harness
    h.transaction.publish_errors = [controller.ClipboardBusy("busy")]
    h.finish_transcription()
    assert h.controller.state == "pasting"
    assert h.sent == []
    h.now += 0.1
    h.controller.tick()
    assert h.transaction.publish_attempts == 2
    assert h.sent == ["Ctrl+V"]
    h.controller.tick()
    assert h.sent == ["Ctrl+V"]
    h.now += 4
    h.controller.tick()
    assert h.transaction.restore_attempts == 1
    assert h.controller.transaction is None
    assert h.controller.state == "idle"


def test_busy_restore_retains_master_and_retries_after_deadline(harness):
    h = harness
    h.transaction.restore_errors = [controller.ClipboardBusy("busy")]
    h.finish_transcription()
    h.now += 4
    h.controller.tick()
    assert h.transaction.master_alive
    assert h.controller.state == "pasting"
    h.controller.tick()
    assert h.transaction.restore_attempts == 1  # Backoff, not an 80 ms retry storm.
    h.now += 1.1
    h.controller.tick()
    assert h.transaction.restore_attempts == 2
    assert h.sent == ["Ctrl+V"]
    assert h.controller.state == "idle"


def test_partial_restore_keeps_master_until_explicit_recovery(harness):
    h = harness
    h.transaction.restore_errors = ["partial"]
    h.finish_transcription()
    h.now += 4
    h.controller.tick()
    assert h.controller.state == "recovery"
    assert h.transaction.master_alive
    assert h.controller.transaction is h.transaction
    assert h.transaction.close_count == 0
    for _ in range(3):
        h.now += 5
        h.controller.tick()
    assert h.transaction.restore_attempts == 1  # No destructive automatic recovery.
    h.answer = controller.QMessageBox.StandardButton.No
    h.controller.recover_clipboard()
    assert h.transaction.master_alive and h.transaction.recover_count == 0
    h.answer = controller.QMessageBox.StandardButton.Yes
    h.controller.recover_clipboard()
    assert h.transaction.recover_count == 1
    assert not h.transaction.master_alive
    assert h.controller.state == "idle"


def test_quit_then_partial_restore_waits_for_discard_before_exit(harness):
    h = harness
    h.transaction.restore_errors = ["partial"]
    h.finish_transcription()
    h.controller.request_quit()
    assert h.controller.exiting
    assert not h.controller.hotkeys.closed
    h.now += 4
    h.controller.tick()
    assert h.controller.state == "recovery"
    assert h.controller.exiting
    assert not h.app.quit_called
    h.controller.tick()
    assert not h.app.quit_called and h.transaction.master_alive
    h.controller.discard_recovery()
    assert h.transaction.discard_count == 1
    assert not h.transaction.master_alive
    h.controller.tick()
    assert h.app.quit_called and h.window.closed
    assert h.controller.hotkeys.closed and h.controller.executor.closed


def test_declining_discard_preserves_recovery_snapshot(harness):
    h = harness
    h.transaction.restore_errors = ["partial"]
    h.finish_transcription()
    h.now += 4
    h.controller.tick()
    h.answer = controller.QMessageBox.StandardButton.No
    h.controller.discard_recovery()
    assert h.transaction.discard_count == 0
    assert h.transaction.master_alive
    assert h.controller.state == "recovery"


def test_audio_cancel_failure_still_leaves_recording_state(harness):
    h = harness
    h.controller.toggle()
    h.controller.recorder.cancel_failure = True
    h.controller.cancel()
    assert h.controller.state == "idle"
    assert h.controller.future is None
    assert "microphone unplugged" in h.window.message.text_value
    assert h.transactions == []


def test_quit_cleanup_continues_after_audio_and_hotkey_failures(harness):
    h = harness
    h.controller.toggle()
    h.controller.recorder.cancel_failure = True
    h.controller.hotkeys.close_failure = True
    h.controller.request_quit()
    assert h.app.quit_called
    assert h.window.closed and h.window.allow_close
    assert h.controller.widget.closed
    assert h.controller.executor.closed
    assert not h.controller.timer.running
    assert not h.app.filters


def test_transaction_constructor_failure_does_not_leave_pasting_state(harness):
    h = harness
    h.constructor_failure = True
    h.finish_transcription()
    assert h.controller.state == "idle"
    assert h.controller.transaction is None
    assert h.controller.future is None
    assert "RegisterClipboardFormat failed" in h.window.message.text_value
    h.controller.toggle()
    assert h.controller.state == "recording"


def test_quit_waits_for_inference_and_discards_its_late_result(harness):
    h = harness
    future = h.start_transcription()
    h.controller.request_quit()
    assert not h.app.quit_called
    assert not h.controller.executor.closed
    future.set_result("Do not paste after quit")
    h.controller.tick()
    assert h.app.quit_called and h.controller.executor.closed
    assert h.transactions == h.sent == []


def test_empty_result_does_not_start_clipboard_transaction(harness):
    h = harness
    h.finish_transcription("")
    assert h.controller.state == "idle"
    assert h.transactions == h.sent == []


def test_recording_limit_queues_inference_exactly_once(harness):
    h = harness
    h.controller.toggle()
    h.controller.recorder.limit_reached = True
    h.controller.recorder.elapsed = 25
    h.controller.tick()
    h.controller.tick()
    assert h.controller.recorder.stops == 1
    assert len(h.controller.executor.jobs) == 1
    assert h.controller.state == "transcribing"


def test_new_clipboard_content_is_preserved_after_paste(harness):
    h = harness
    h.transaction.outcome = "superseded"
    h.finish_transcription()
    h.now += 4
    h.controller.tick()
    assert h.controller.state == "idle"
    assert h.sent == ["Ctrl+V"]
    assert "Новое содержимое" in h.window.message.text_value


def test_recording_length_comes_from_settings_and_is_saved(harness, monkeypatch):
    h = harness
    saved = []
    monkeypatch.setattr(controller, "save_settings", lambda path, settings: saved.append(settings))

    h.window.duration_slider.setValue(55)
    h.controller.save()
    assert [item.max_recording_seconds for item in saved] == [55]
    assert h.controller.settings.max_recording_seconds == 55
    assert h.window.hotkey_hint == "F4"

    h.controller.toggle()
    assert h.controller.recorder.limit_seconds == 55
    h.controller.recorder.elapsed = 3.5
    h.controller.tick()
    assert h.window.time_label.text_value == "03.5 / 55 c"
    h.controller.toggle()
    h.controller.tick()


def test_recording_uses_the_default_length_before_any_save(harness):
    h = harness
    h.controller.toggle()
    assert h.controller.recorder.limit_seconds == 30
    h.controller.recorder.elapsed = 2.0
    h.controller.tick()
    assert h.window.time_label.text_value == "02.0 / 30 c"


def test_out_of_range_slider_value_is_not_saved(harness, monkeypatch):
    h = harness
    saved = []
    monkeypatch.setattr(controller, "save_settings", lambda path, settings: saved.append(settings))

    h.window.duration_slider.setValue(90)
    h.controller.save()
    assert saved == []
    assert "не сохранены" in h.window.message.text_value
    assert h.controller.settings.max_recording_seconds == 30
