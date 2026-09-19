"""Single GUI-thread coordinator for audio, inference and clipboard ownership."""

from __future__ import annotations

import os
import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from voxturbo.audio import AudioRecorder, list_input_devices
from voxturbo.config import Settings, save_settings
from voxturbo.engine import ParakeetEngine
from voxturbo.model_store import ModelStore
from voxturbo.platform.windows import (
    MSG,
    WM_HOTKEY,
    ClipboardBusy,
    ClipboardTransaction,
    HotkeyManager,
    capture_target,
    modifiers_pressed,
    send_paste,
    target_is_current,
)
from voxturbo.ui import PILL_CAPTIONS, FloatingWidget, MainWindow


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def nativeEventFilter(self, event_type, message):
        msg = MSG.from_address(int(message))
        if msg.message == WM_HOTKEY and msg.wParam == self.controller.hotkeys.hotkey_id:
            QTimer.singleShot(0, self.controller.toggle)
            return True, 0
        return False, 0


class AppController(QObject):
    def __init__(self, app: QApplication, window: MainWindow, settings: Settings, root: Path):
        super().__init__()
        self.app, self.window, self.settings, self.root = app, window, settings, root
        self.recorder = AudioRecorder()
        self.store = ModelStore(root / "models" / "parakeet-v3-int8")
        self.engine = ParakeetEngine(self.store)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voxturbo-asr")
        self.future: Future | None = None
        self.task_kind = ""
        self.generation = 0
        self.task_generation = 0
        self.cancel_event = threading.Event()
        self.progress: queue.SimpleQueue = queue.SimpleQueue()
        self.state = "idle"
        self.target = None
        self.transaction = None
        self.paste_deadline = 0.0
        self.restore_at = 0.0
        self.restore_deadline = 0.0
        self.paste_started = False
        self.paste_message = ""
        self.exiting = False
        self.devices = []
        self.widget = FloatingWidget()
        self.widget.toggled.connect(self.toggle)
        self.hotkeys = HotkeyManager(int(window.winId()))
        self.hotkey_filter = HotkeyFilter(self)
        app.installNativeEventFilter(self.hotkey_filter)
        window.toggle_requested.connect(self.toggle)
        window.cancel_requested.connect(self.cancel)
        window.download_requested.connect(self.download_model)
        window.save_requested.connect(self.save)
        window.copy_requested.connect(self.copy_result)
        window.refresh_devices_requested.connect(self.refresh_devices)
        window.quit_requested.connect(self.request_quit)
        window.recover_requested.connect(self.recover_clipboard)
        window.discard_recovery_requested.connect(self.discard_recovery)
        self.refresh_devices()
        self.refresh_model()
        self.show_message("Нажмите горячую клавишу в нужном поле или используйте плавающую кнопку.")
        self.widget.setVisible(settings.show_widget)
        try:
            self.hotkeys.rebind(settings.hotkey)
        except Exception as exc:
            self.show_message(f"Горячая клавиша недоступна: {exc}. Выберите другую в настройках.")
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def show_message(self, message: str):
        self.window.message.setText(message)

    def refresh_model(self):
        ready = self.store.is_ready()
        self.window.model_status.setText(
            "Модель готова · распознавание без сети" if ready else "Для первой диктовки скачайте модель"
        )
        self.window.download_button.setText("Проверить / скачать" if ready else "Скачать модель")
        self.render_state()

    def render_state(self):
        recording = self.state == "recording"
        idle = self.state == "idle" and not self.exiting
        captions = {
            "idle": "Готово к диктовке" if self.store.is_ready() else "Нужна модель",
            "recording": "Слушаю вас…",
            "transcribing": "Распознаю на компьютере…",
            "downloading": "Скачиваю модель…",
            "pasting": "Вставка и возврат буфера…",
            "recovery": "Нужно восстановить буфер",
        }
        self.window.status_label.setText(
            "Завершение работы…" if self.exiting and self.state != "recovery" else captions[self.state]
        )
        pill_caption = PILL_CAPTIONS[self.state]
        pill_state = self.state
        if self.state == "idle" and not self.store.is_ready():
            pill_caption, pill_state = "Нужна модель", "attention"
        self.window.status_pill.set_state(pill_state, pill_caption)
        self.window.set_recording_tone(recording)
        self.window.record_button.setText("Остановить и распознать" if recording else "Начать запись")
        self.window.record_button.setEnabled(
            (recording or (idle and self.store.is_ready())) and not self.exiting
        )
        self.window.cancel_button.setEnabled(
            self.state in ("recording", "downloading", "transcribing") and not self.exiting
        )
        self.window.save_button.setEnabled(idle)
        self.window.download_button.setEnabled(idle)
        self.window.copy_button.setEnabled(idle and bool(self.window.transcript.toPlainText()))
        self.window.download_progress.setVisible(self.state == "downloading")
        self.window.recovery_row.setVisible(self.state == "recovery")
        captions_small = {
            "idle": f"Говорить · {self.settings.hotkey}",
            "recording": "Остановить запись",
            "transcribing": "Распознаю…",
            "downloading": "Загрузка модели…",
            "pasting": "Возвращаю буфер…",
            "recovery": "Откройте VoxTurbo",
        }
        self.widget.set_status(captions_small[self.state], recording, self.recorder.level if recording else 0)
        self.widget.setToolTip(f"{self.settings.hotkey}: запись / стоп. Перетащите кнопку для перемещения.")
        if not recording:
            self.window.level_bar.setValue(0)
            self.window.time_label.setText("ЛОКАЛЬНО · CPU")

    def refresh_devices(self):
        if self.state != "idle":
            return
        try:
            self.devices = list_input_devices()
            self.window.mic_combo.clear()
            self.window.mic_combo.addItem("Системный микрофон", None)
            for device in self.devices:
                key = f"{device.host_api}|{device.name}"
                self.window.mic_combo.addItem(f"{device.name} · {device.host_api}", key)
            selected = self.window.mic_combo.findData(self.settings.microphone)
            self.window.mic_combo.setCurrentIndex(max(0, selected))
            if self.settings.microphone and selected < 0:
                self.show_message(
                    "Сохранённый микрофон не найден. Выберите устройство и сохраните настройки."
                )
        except Exception as exc:
            self.show_message(f"Не удалось получить микрофоны: {exc}")

    def toggle(self):
        if self.exiting:
            return
        if self.state == "recording":
            self.stop_recording()
        elif self.state == "idle":
            self.start_recording()

    def start_recording(self):
        if not self.store.is_ready():
            self.show_message("Сначала скачайте модель в окне VoxTurbo.")
            return
        self.generation += 1
        self.target = capture_target()
        if self.target is not None and self.target.pid == os.getpid():
            self.target = None
        try:
            selected_device = None
            if self.settings.microphone:
                matches = [
                    d for d in list_input_devices() if f"{d.host_api}|{d.name}" == self.settings.microphone
                ]
                if not matches:
                    raise ValueError("Выбранный микрофон отключён. Выберите другое устройство.")
                selected_device = matches[0].index
            self.recorder.start(selected_device, self.settings.max_recording_seconds)
            self.state = "recording"
            self.show_message(
                "Повторное нажатие остановит запись. До "
                f"{self.settings.max_recording_seconds} секунд; звук остаётся в памяти."
                if self.target
                else "Текст появится здесь. Для вставки в другое окно используйте F4 или виджет."
            )
        except Exception as exc:
            self.cancel_audio()
            self.target = None
            self.show_message(f"Не удалось начать запись: {exc}")
        self.render_state()

    def stop_recording(self):
        try:
            clip = self.recorder.stop()
            self.task_generation = self.generation
            self.task_kind = "transcribe"
            self.future = self.executor.submit(self.engine.transcribe, clip)
            self.state = "transcribing"
            self.show_message(
                "Первое распознавание также проверяет и загружает модель. Это может занять немного времени."
            )
        except Exception as exc:
            self.state = "idle"
            self.show_message(f"Не удалось завершить запись: {exc}")
        self.render_state()

    def download_model(self):
        if self.state != "idle" or self.exiting:
            return
        self.cancel_event = threading.Event()
        self.window.download_progress.setValue(0)
        self.task_kind = "download"
        self.task_generation = self.generation
        self.future = self.executor.submit(
            self.store.download, lambda done, total: self.progress.put((done, total)), self.cancel_event
        )
        self.state = "downloading"
        self.show_message("Загрузка с Hugging Face. Каждый файл проверяется по SHA-256.")
        self.render_state()

    def cancel(self):
        self.generation += 1
        if self.state == "recording":
            error = self.cancel_audio()
            self.state = "idle"
            self.show_message(f"Запись остановлена. {error}" if error else "Запись отменена.")
        elif self.state == "downloading":
            self.cancel_event.set()
            self.show_message("Отменяю скачивание…")
        elif self.state == "transcribing":
            self.show_message("Результат отменён. Дождитесь завершения текущего вычисления.")
        self.render_state()

    def tick(self):
        if self.state == "recording":
            self.window.time_label.setText(
                f"{self.recorder.elapsed:04.1f} / {self.settings.max_recording_seconds} c"
            )
            self.window.level_bar.setValue(min(100, int(self.recorder.level * 400)))
            self.widget.set_status("Остановить запись", True, self.recorder.level)
            if self.recorder.limit_reached:
                self.stop_recording()
        while not self.progress.empty():
            done, total = self.progress.get()
            self.window.download_progress.setValue(int(done / max(total, 1) * 1000))
            self.window.model_status.setText(f"{done / 1024**2:.0f} из {total / 1024**2:.0f} МиБ")
        if self.future is not None and self.future.done():
            completed, self.future = self.future, None
            kind, valid = self.task_kind, self.task_generation == self.generation
            self.state = "idle"
            try:
                result = completed.result()
                if kind == "download":
                    self.show_message("Модель проверена. Можно диктовать без сети.")
                    self.refresh_model()
                elif valid and not self.exiting:
                    self.window.transcript.setPlainText(result)
                    if result.strip():
                        self.begin_paste()
                    else:
                        self.show_message("Речь не распознана. Попробуйте говорить ближе к микрофону.")
                else:
                    self.show_message("Распознавание отменено. Буфер обмена не изменён.")
            except Exception as exc:
                self.show_message(f"Операция не завершена: {exc}")
                self.refresh_model()
            self.render_state()
        if self.state == "pasting":
            self.advance_paste()
        if self.exiting and self.future is None and self.state not in ("pasting", "recovery"):
            self.finish_quit()

    def begin_paste(self):
        if self.target is None or not target_is_current(self.target):
            self.show_message(
                "Поле ввода изменилось или не было выбрано. Текст доступен здесь для копирования."
            )
            return
        transaction = ClipboardTransaction(int(self.window.winId()))
        self.state = "pasting"
        self.paste_started = False
        self.paste_message = ""
        self.transaction = transaction
        self.paste_deadline = time.monotonic() + 2.0
        self.show_message("Вставляю текст и возвращаю прежнее содержимое буфера…")

    def advance_paste(self):
        now = time.monotonic()
        if not self.paste_started:
            if self.exiting or not target_is_current(self.target):
                self.end_paste("Поле ввода изменилось. Текст доступен в окне VoxTurbo.")
                return
            if now > self.paste_deadline:
                self.end_paste(
                    "Вставка отменена: буфер занят или клавиши удерживаются. Текст доступен здесь."
                )
                return
            if modifiers_pressed():
                return
            try:
                self.transaction.publish(self.window.transcript.toPlainText())
                self.paste_started = True
                self.restore_at = now + self.settings.restore_delay_ms / 1000
                self.restore_deadline = self.restore_at + 2.0
                if not target_is_current(self.target) or modifiers_pressed():
                    self.paste_message = "Поле или состояние клавиш изменилось. Вставка отменена."
                    self.restore_at = now
                else:
                    send_paste(self.settings.paste_shortcut)
            except ClipboardBusy:
                return
            except Exception as exc:
                self.paste_message = f"Автовставка не выполнена: {exc}. Текст доступен здесь."
                if self.transaction.recovery_required:
                    self.enter_recovery()
                elif self.transaction.pending:
                    self.paste_started = True
                    self.restore_at = now
                    self.restore_deadline = now + 2.0
                else:
                    self.end_paste(self.paste_message)
                return
        if now < self.restore_at:
            return
        try:
            outcome = self.transaction.restore()
            message = self.paste_message or (
                "Команда вставки отправлена. Прежний буфер восстановлен."
                if outcome == "restored"
                else "Команда вставки отправлена. Новое содержимое буфера сохранено."
            )
            self.end_paste(message)
        except Exception as exc:
            if self.transaction.recovery_required:
                self.enter_recovery()
                return
            if now >= self.restore_deadline:
                # Keep the sole snapshot alive and continue gentle retries; never silently discard it.
                self.show_message(f"Не удаётся вернуть буфер: {exc}. Снимок удерживается, повторяю попытку.")
                self.restore_at = now + 1.0

    def enter_recovery(self):
        self.state = "recovery"
        self.show_message(
            "Windows не вернула все форматы. Независимый снимок сохранён в памяти. "
            "Выберите, вернуть его или оставить текущее содержимое буфера."
        )
        self.render_state()
        self.window.open_window()

    def recover_clipboard(self):
        if self.state != "recovery":
            return
        answer = QMessageBox.question(
            self.window,
            "Восстановление буфера",
            "Заменить текущее содержимое буфера сохранённым снимком? "
            "Если после ошибки вы копировали новые данные, они будут заменены.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.transaction.recover()
            self.end_paste("Сохранённый буфер восстановлен по вашей команде.")
        except Exception as exc:
            self.show_message(f"Восстановление не удалось: {exc}. Снимок остаётся в памяти.")

    def discard_recovery(self):
        if self.state != "recovery":
            return
        answer = QMessageBox.question(
            self.window,
            "Оставить текущий буфер",
            "Освободить сохранённый снимок и оставить текущее содержимое буфера?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.transaction.discard_recovery()
            self.end_paste("Текущий буфер сохранён. Старый снимок освобождён по вашей команде.")

    def end_paste(self, message: str):
        if self.transaction is not None:
            self.transaction.close()
            self.transaction = None
        self.state = "idle"
        self.show_message(message)
        self.render_state()

    def copy_result(self):
        if self.state == "idle":
            self.app.clipboard().setText(self.window.transcript.toPlainText())
            self.show_message("Текст скопирован по вашей команде.")

    def save(self):
        if self.state != "idle":
            return
        candidate = Settings(
            hotkey=self.window.hotkey_edit.text().strip(),
            paste_shortcut=self.window.paste_combo.currentText(),
            restore_delay_ms=self.window.delay_spin.value(),
            microphone=self.window.mic_combo.currentData(),
            show_widget=self.window.widget_check.isChecked(),
            max_recording_seconds=self.window.duration_slider.value(),
        )
        try:
            candidate.validated()
            self.hotkeys.rebind(candidate.hotkey)
            try:
                save_settings(self.root / "settings.json", candidate)
            except Exception:
                self.hotkeys.rebind(self.settings.hotkey)
                raise
            self.settings = candidate
            self.widget.setVisible(candidate.show_widget)
            self.window.set_hotkey_hint(candidate.hotkey)
            self.show_message("Настройки сохранены.")
            self.render_state()
        except Exception as exc:
            self.show_message(f"Настройки не сохранены: {exc}")

    def request_quit(self):
        if self.exiting:
            return
        if self.state == "recovery":
            self.window.open_window()
            self.show_message("Перед выходом выберите действие с сохранённым снимком буфера.")
            return
        self.exiting = True
        self.generation += 1
        self.cancel_event.set()
        if self.state == "recording":
            self.cancel_audio()
            self.state = "idle"
        self.widget.hide()
        self.show_message("Завершаю текущую операцию перед выходом…")
        self.render_state()
        if self.future is None and self.state != "pasting":
            self.finish_quit()

    def finish_quit(self):
        self.timer.stop()
        self.app.removeNativeEventFilter(self.hotkey_filter)
        self.cancel_audio()
        try:
            self.hotkeys.close()
        except Exception:
            # Destroying the owner window also unregisters its hotkeys.
            pass
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.window.tray.hide()
        self.widget.close()
        self.window.allow_close = True
        self.window.close()
        self.app.quit()

    def cancel_audio(self) -> str | None:
        try:
            self.recorder.cancel()
        except Exception as exc:
            return str(exc)
        return None
