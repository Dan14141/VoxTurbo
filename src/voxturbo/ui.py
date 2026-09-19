"""Qt views. All calls are made from the GUI thread."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from voxturbo.audio import MAX_RECORDING_SECONDS, MIN_RECORDING_SECONDS
from voxturbo.config import Settings

# Фирменный знак: пять полос с общим градиентом на тёмной плите. Геометрия снята
# с исходного логотипа, поэтому scripts/make_icon.py рисует ровно тот же знак.
BAR_CENTERS = (0.222, 0.359, 0.499, 0.640, 0.777)
BAR_WIDTHS = (0.089, 0.096, 0.100, 0.096, 0.089)
BAR_HEIGHTS = (0.231, 0.410, 0.580, 0.410, 0.231)
BRAND_TOP = QColor("#67facf")
BRAND_MIDDLE = QColor("#03d1dd")
BRAND_BOTTOM = QColor("#0367f0")
# Во время записи знак и индикаторы меняют палитру на фиолетовую: состояние видно
# по цвету, а бирюзовый остаётся признаком покоя.
RECORD_TOP = QColor("#d8b4fe")
RECORD_MIDDLE = QColor("#a855f7")
RECORD_BOTTOM = QColor("#6d28d9")

STYLE = """
QWidget { background: #0b1119; color: #e9f2f9; font-family: 'Segoe UI'; font-size: 14px; }
QLabel { background: transparent; }
QLabel#brand { font-size: 21px; font-weight: 700; letter-spacing: 1px; }
QLabel#tagline { color: #8ea3b6; font-size: 12px; }
QLabel#headline { font-size: 24px; font-weight: 600; }
QLabel#status { font-size: 20px; font-weight: 600; }
QLabel#cardtitle { font-size: 16px; font-weight: 600; }
QLabel#muted { color: #93a7ba; }
QLabel#footer { color: #6f8394; font-size: 12px; }
QLabel#pill { font-size: 12px; font-weight: 600; padding: 6px 13px; border-radius: 12px; }
QLabel#key { background: #16222e; border: 1px solid #2b3d4e; border-radius: 7px;
             color: #a8c4d6; font-size: 12px; padding: 4px 9px; }
QSlider::groove:horizontal { height: 6px; background: #17222e; border-radius: 3px; }
QSlider::sub-page:horizontal { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                              stop:0 #4fe3c1, stop:1 #2f9df6); border-radius: 3px; }
QSlider::handle:horizontal { background: #e9f2f9; width: 16px; margin: -6px 0; border-radius: 8px; }
QSlider::handle:horizontal:hover { background: #ffffff; }
QFrame#card { background: #121b25; border: 1px solid #223140; border-radius: 16px; }
/* Только метки: правило вида "QFrame#card QWidget" делало прозрачным фон кнопок,
   из-за чего основная кнопка исчезала до наведения курсора. */
QFrame#card QLabel { background: transparent; }
QPushButton { background: #1a2634; border: 1px solid #2b3b4c; border-radius: 10px;
              padding: 10px 18px; font-weight: 600; }
QPushButton:hover { background: #223143; border-color: #3d5a72; }
QPushButton:pressed { background: #16212c; }
QPushButton:disabled { background: #141d27; border-color: #1f2b38; color: #64798d; }
QPushButton#primary { background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                      stop:0 #4fe3c1, stop:1 #2f9df6); border: 0; color: #05231e;
                      padding: 13px 22px; font-size: 15px; font-weight: 700; }
QPushButton#primary:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                             stop:0 #6ef0d3, stop:1 #4fb2ff); }
QPushButton#primary:disabled { background: #23323f; border: 1px solid #3d5266; color: #a9bccd; }
QPushButton#ghost { background: transparent; border: 1px solid #2b3b4c; }
QPushButton#ghost:hover { background: #182430; border-color: #3d5a72; }
QPlainTextEdit, QLineEdit, QComboBox, QSpinBox { background: #0e1620; border: 1px solid #243343;
              border-radius: 10px; padding: 10px; selection-background-color: #1f6f63; }
QPlainTextEdit { padding: 12px; }
QPlainTextEdit:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #3fa8c9; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox QAbstractItemView { background: #101a24; border: 1px solid #2b3b4c;
                              selection-background-color: #223143; }
QProgressBar { border: 0; background: #17222e; border-radius: 4px; min-height: 8px; max-height: 8px; }
QProgressBar::chunk { border-radius: 4px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                     stop:0 #4fe3c1, stop:1 #2f9df6); }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #3a4c5f; border-radius: 5px;
                       background: #0e1620; }
QCheckBox::indicator:checked { background: #4fe3c1; border-color: #4fe3c1; }
QToolButton { background: transparent; border: 0; color: #7fd9e8; font-weight: 600; padding: 4px 2px; }
QToolButton:hover { color: #a6ecf5; }
QMenu { background: #121b25; border: 1px solid #2b3b4c; padding: 6px; }
QMenu::item { padding: 8px 18px; border-radius: 7px; }
QMenu::item:selected { background: #223143; }
QScrollArea { border: 0; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #243343; border-radius: 5px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: #2f4459; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { background: #16212c; color: #e9f2f9; border: 1px solid #2c3d4f; padding: 6px; }
"""

PILL_TONES = {
    "idle": ("#12352e", "#6ff0cf"),
    "attention": ("#33291a", "#f5cf82"),
    "recording": ("#2a1c3f", "#c9a6ff"),
    "transcribing": ("#33291a", "#f5cf82"),
    "downloading": ("#33291a", "#f5cf82"),
    "pasting": ("#1c2c3d", "#8fc7ff"),
    "recovery": ("#3a1c26", "#ff9aac"),
}
PILL_CAPTIONS = {
    "idle": "Готово",
    "recording": "Запись",
    "transcribing": "Распознавание",
    "downloading": "Загрузка",
    "pasting": "Вставка",
    "recovery": "Нужен выбор",
}


def draw_logo(
    painter: QPainter,
    size: float,
    top: QColor = BRAND_TOP,
    middle: QColor = BRAND_MIDDLE,
    bottom: QColor = BRAND_BOTTOM,
) -> None:
    """Рисует фирменный знак в квадрат заданного размера."""
    inset = size * 0.045
    plate = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    radius = plate.width() * 0.235
    plate_gradient = QLinearGradient(plate.topLeft(), plate.bottomRight())
    plate_gradient.setColorAt(0.0, QColor("#12202f"))
    plate_gradient.setColorAt(1.0, QColor("#070c15"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(plate_gradient)
    painter.drawRoundedRect(plate, radius, radius)

    center_y = plate.center().y()
    tallest = plate.height() * BAR_HEIGHTS[2]
    gradient = QLinearGradient(0.0, center_y - tallest / 2, 0.0, center_y + tallest / 2)
    gradient.setColorAt(0.0, top)
    gradient.setColorAt(0.5, middle)
    gradient.setColorAt(1.0, bottom)
    painter.setBrush(gradient)
    for center, width, height in zip(BAR_CENTERS, BAR_WIDTHS, BAR_HEIGHTS):
        bar_width = plate.width() * width
        bar_height = plate.height() * height
        bar = QRectF(
            plate.left() + plate.width() * center - bar_width / 2,
            center_y - bar_height / 2,
            bar_width,
            bar_height,
        )
        painter.drawRoundedRect(bar, bar_width / 2, bar_width / 2)


def logo_pixmap(size: int) -> QPixmap:
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_logo(painter, float(size))
    painter.end()
    return canvas


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(logo_pixmap(size))
    return icon


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(11)
    return frame, layout


class StatusPill(QLabel):
    """Компактный индикатор состояния с цветом по режиму работы."""

    def __init__(self):
        super().__init__("Подготовка")
        self.setObjectName("pill")
        self.set_tone("idle")

    def set_tone(self, state: str):
        background, foreground = PILL_TONES.get(state, PILL_TONES["idle"])
        self.setStyleSheet(
            f"QLabel#pill {{ background: {background}; color: {foreground};"
            " padding: 6px 13px; border-radius: 12px; font-size: 12px; font-weight: 600; }"
        )

    def set_state(self, state: str, text: str):
        self.set_tone(state)
        self.setText(text)


class WheelGuard(QObject):
    """Колесо мыши прокручивает страницу и никогда не меняет настройку.

    Счётчик и ползунок принимают колесо, когда на них есть фокус: после клика в
    настройках достаточно провести курсором по строке, чтобы значение изменилось
    незаметно для пользователя. Поэтому колесо над полями настроек всегда уходит в
    область прокрутки, а значения меняются кликом, перетаскиванием или вводом.
    """

    def __init__(self, scroll_area: QScrollArea):
        super().__init__(scroll_area)
        self.scroll_area = scroll_area

    def watch(self, *widgets: QWidget) -> None:
        for widget in widgets:
            widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            QApplication.sendEvent(self.scroll_area.viewport(), event)
            return True
        return False


class FloatingWidget(QWidget):
    toggled = Signal()

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("VoxTurbo: запись или остановка диктовки")
        self.resize(258, 68)
        self.caption = "Говорить · F4"
        self.recording = False
        self.level = 0.0
        self._press = None
        self._dragged = False
        self._hover = False
        self._move_to_corner()

    def _move_to_corner(self):
        rect = QApplication.primaryScreen().availableGeometry()
        self.move(rect.right() - self.width() - 28, rect.bottom() - self.height() - 28)

    def set_status(self, caption: str, recording: bool = False, level: float = 0.0):
        self.caption, self.recording, self.level = caption, recording, level
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def nativeEvent(self, event_type, message):
        from voxturbo.platform.windows import MSG

        msg = MSG.from_address(int(message))
        if msg.message == 0x0021:  # WM_MOUSEACTIVATE -> MA_NOACTIVATE
            return True, 3
        return super().nativeEvent(event_type, message)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#3d5a72" if self._hover else "#243444"))
        painter.setBrush(QColor("#101b26"))
        painter.drawRoundedRect(0.5, 0.5, self.width() - 1, self.height() - 1, 24, 24)

        mark = self.height() - 24.0
        painter.save()
        painter.translate(14.0, (self.height() - mark) / 2)
        draw_logo(
            painter,
            mark,
            top=RECORD_TOP if self.recording else BRAND_TOP,
            middle=RECORD_MIDDLE if self.recording else BRAND_MIDDLE,
            bottom=RECORD_BOTTOM if self.recording else BRAND_BOTTOM,
        )
        painter.restore()

        painter.setPen(QColor("#eef8f7"))
        font = painter.font()
        font.setPixelSize(14)
        font.setWeight(font.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(58, 0, self.width() - 72, self.height(), Qt.AlignmentFlag.AlignVCenter, self.caption)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint() - self.pos()
            self._dragged = False

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._press
            if (new_pos - self.pos()).manhattanLength() > 4:
                self._dragged = True
                self.move(new_pos)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None:
            if not self._dragged:
                self.toggled.emit()
            self._press = None


class MainWindow(QMainWindow):
    toggle_requested = Signal()
    cancel_requested = Signal()
    download_requested = Signal()
    save_requested = Signal()
    copy_requested = Signal()
    refresh_devices_requested = Signal()
    quit_requested = Signal()
    recover_requested = Signal()
    discard_recovery_requested = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.allow_close = False
        self.hide_on_close = QSystemTrayIcon.isSystemTrayAvailable()
        self.setWindowTitle("VoxTurbo")
        self.setWindowIcon(app_icon())
        self.resize(800, 860)
        self.setMinimumSize(640, 520)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(26, 18, 26, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(13)
        mark = QLabel()
        mark.setPixmap(logo_pixmap(42))
        mark.setFixedSize(42, 42)
        header.addWidget(mark)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        titles.addWidget(label("VoxTurbo", "brand"))
        titles.addWidget(label("·Локальная диктовка·", "tagline"))
        header.addLayout(titles)
        header.addStretch(1)
        self.status_pill = StatusPill()
        header.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        status_card, status_layout = card()
        row = QHBoxLayout()
        self.status_label = label("Подготовка…", "status")
        self.time_label = label("ЛОКАЛЬНО · CPU", "tagline")
        row.addWidget(self.status_label, 1)
        row.addWidget(self.time_label, 0, Qt.AlignmentFlag.AlignBottom)
        status_layout.addLayout(row)
        self.message = label("", "muted", True)
        status_layout.addWidget(self.message)
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        status_layout.addWidget(self.level_bar)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.record_button = QPushButton("Начать запись")
        self.record_button.setObjectName("primary")
        self.record_button.setToolTip("Первое нажатие — запись, второе — распознавание и вставка.")
        self.record_button.clicked.connect(self.toggle_requested)
        self.cancel_button = QPushButton("Отмена")
        self.cancel_button.setObjectName("ghost")
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.cancel_button.setEnabled(False)
        buttons.addWidget(self.record_button, 1)
        buttons.addWidget(self.cancel_button)
        status_layout.addLayout(buttons)
        self.recovery_row = QWidget()
        recovery_layout = QHBoxLayout(self.recovery_row)
        recovery_layout.setContentsMargins(0, 0, 0, 0)
        recovery_layout.setSpacing(10)
        recover = QPushButton("Вернуть сохранённый буфер")
        recover.clicked.connect(self.recover_requested)
        discard = QPushButton("Оставить текущий")
        discard.setObjectName("ghost")
        discard.clicked.connect(self.discard_recovery_requested)
        recovery_layout.addWidget(recover)
        recovery_layout.addWidget(discard)
        recovery_layout.addStretch(1)
        self.recovery_row.hide()
        status_layout.addWidget(self.recovery_row)
        layout.addWidget(status_card)

        model_card, model_layout = card()
        model_row = QHBoxLayout()
        model_column = QVBoxLayout()
        model_column.setSpacing(4)
        model_column.addWidget(label("Модель распознавания", "cardtitle"))
        self.model_status = label("Проверка файлов модели…", "muted", True)
        model_column.addWidget(self.model_status)
        model_row.addLayout(model_column, 1)
        self.download_button = QPushButton("Скачать модель")
        self.download_button.clicked.connect(self.download_requested)
        model_row.addWidget(self.download_button, 0, Qt.AlignmentFlag.AlignTop)
        model_layout.addLayout(model_row)
        self.download_progress = QProgressBar()
        self.download_progress.setRange(0, 1000)
        self.download_progress.setTextVisible(False)
        self.download_progress.hide()
        model_layout.addWidget(self.download_progress)
        model_layout.addWidget(
            label(
                "Parakeet TDT 0.6B v3 · INT8 · 25 языков, включая русский · 639 МиБ\n"
                "NVIDIA, CC BY 4.0 · одно скачивание, дальше распознавание без сети",
                "muted",
                True,
            )
        )
        layout.addWidget(model_card)

        result_card, result_layout = card()
        result_row = QHBoxLayout()
        result_row.addWidget(label("Последняя диктовка", "cardtitle"), 1)
        self.copy_button = QPushButton("Копировать")
        self.copy_button.setObjectName("ghost")
        self.copy_button.setToolTip("Заменяет текущий буфер обмена выбранным текстом.")
        self.copy_button.clicked.connect(self.copy_requested)
        self.copy_button.setEnabled(False)
        result_row.addWidget(self.copy_button)
        result_layout.addLayout(result_row)
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText(
            "Здесь появится распознанный текст. История на диск не записывается."
        )
        self.transcript.setMinimumHeight(84)
        result_layout.addWidget(self.transcript, 1)
        layout.addWidget(result_card)

        self.settings_button = QToolButton()
        self.settings_button.setText("Показать настройки")
        self.settings_button.setCheckable(True)
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.toggled.connect(self._toggle_settings)
        layout.addWidget(self.settings_button)

        self.settings_card, settings_layout = card()
        settings_layout.addWidget(label("Горячая клавиша и вставка", "cardtitle"))
        form = QFormLayout()
        form.setSpacing(12)
        self.hotkey_edit = QLineEdit(settings.hotkey)
        self.hotkey_edit.setPlaceholderText("F4 или Ctrl+Alt+Space")
        self.paste_combo = QComboBox()
        self.paste_combo.addItems(["Ctrl+V", "Shift+Insert"])
        self.paste_combo.setCurrentText(settings.paste_shortcut)
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(200, 3000)
        self.delay_spin.setSingleStep(100)
        self.delay_spin.setSuffix(" мс")
        self.delay_spin.setValue(settings.restore_delay_ms)
        self.delay_spin.setToolTip(
            "Увеличьте, если приложение не успевает прочитать текст перед возвратом картинки."
        )
        self.mic_combo = QComboBox()
        self.mic_combo.setMinimumContentsLength(20)
        self.mic_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.mic_combo.addItem("Системный микрофон", None)
        mic_row = QHBoxLayout()
        mic_row.setSpacing(9)
        mic_row.addWidget(self.mic_combo, 1)
        refresh = QPushButton("Обновить")
        refresh.setObjectName("ghost")
        refresh.clicked.connect(self.refresh_devices_requested)
        mic_row.addWidget(refresh)
        self.duration_slider = QSlider(Qt.Orientation.Horizontal)
        self.duration_slider.setRange(MIN_RECORDING_SECONDS, MAX_RECORDING_SECONDS)
        self.duration_slider.setSingleStep(5)
        self.duration_slider.setPageStep(5)
        self.duration_slider.setTickInterval(5)
        self.duration_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.duration_slider.setValue(settings.max_recording_seconds)
        self.duration_slider.setToolTip(
            "Автоматическая остановка записи. Длинная диктовка дольше распознаётся и занимает больше памяти."
        )
        self.duration_label = label(f"{settings.max_recording_seconds} с", "key")
        self.duration_slider.valueChanged.connect(lambda value: self.duration_label.setText(f"{value} с"))
        duration_row = QHBoxLayout()
        duration_row.setSpacing(9)
        duration_row.addWidget(self.duration_slider, 1)
        duration_row.addWidget(self.duration_label)
        form.addRow("Горячая клавиша", self.hotkey_edit)
        form.addRow("Микрофон", mic_row)
        form.addRow("Длина диктовки", duration_row)
        form.addRow("Комбинация вставки", self.paste_combo)
        form.addRow("Возврат буфера через", self.delay_spin)
        settings_layout.addLayout(form)
        self.widget_check = QCheckBox("Показывать плавающую кнопку")
        self.widget_check.setChecked(settings.show_widget)
        settings_layout.addWidget(self.widget_check)
        settings_row = QHBoxLayout()
        settings_row.addStretch(1)
        self.save_button = QPushButton("Сохранить настройки")
        self.save_button.clicked.connect(self.save_requested)
        settings_row.addWidget(self.save_button)
        settings_layout.addLayout(settings_row)
        self.settings_card.hide()
        layout.addWidget(self.settings_card)

        footer = QHBoxLayout()
        footer.addWidget(label("Windows 10 · версия 0.1.0", "footer"), 1)
        self.hotkey_chip = label(f"Клавиша: {settings.hotkey}", "key")
        footer.addWidget(self.hotkey_chip)
        layout.addLayout(footer)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        self.setCentralWidget(scroll)
        self.wheel_guard = WheelGuard(scroll)
        self.wheel_guard.watch(self.paste_combo, self.mic_combo, self.delay_spin, self.duration_slider)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("VoxTurbo — локальная диктовка")
        menu = QMenu(self)
        menu.addAction("Открыть VoxTurbo", self.open_window)
        menu.addAction("Запись / стоп", self.toggle_requested.emit)
        menu.addSeparator()
        menu.addAction("Выход", self.quit_requested.emit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        if self.hide_on_close:
            self.tray.show()

    def _toggle_settings(self, shown: bool):
        self.settings_card.setVisible(shown)
        self.settings_button.setText("Скрыть настройки" if shown else "Показать настройки")

    def set_hotkey_hint(self, hotkey: str):
        self.hotkey_chip.setText(f"Клавиша: {hotkey}")

    def set_recording_tone(self, recording: bool):
        """Индикатор уровня меняет палитру вместе со знаком: фиолетовый = идёт запись."""
        self.level_bar.setStyleSheet(
            "QProgressBar::chunk { border-radius: 4px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            " stop:0 #d8b4fe, stop:1 #a855f7); }"
            if recording
            else ""
        )

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick, QSystemTrayIcon.ActivationReason.Trigger):
            self.open_window()

    def open_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
        elif self.hide_on_close:
            self.hide()
            event.ignore()
        else:
            event.ignore()
            self.quit_requested.emit()
