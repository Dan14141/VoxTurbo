"""UI regression tests with real widgets. No microphone, clipboard or network I/O.

Приложение одно на процесс: второй QApplication создать нельзя, поэтому фикстура
переиспользует существующий экземпляр (его может создать test_cli).
"""

import sys

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from voxturbo.config import Settings
from voxturbo.ui import STYLE, MainWindow


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    return app


@pytest.fixture
def window(application):
    window = MainWindow(Settings())
    window.show()
    application.processEvents()
    yield window
    window.tray.hide()
    window.allow_close = True
    window.close()


def wheel_event(steps: int) -> QWheelEvent:
    return QWheelEvent(
        QPointF(5.0, 5.0),
        QPointF(5.0, 5.0),
        QPoint(0, 0),
        QPoint(0, 120 * steps),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_primary_button_stays_visible_without_hover(window):
    """Регресс: правило карточки делало фон кнопки прозрачным и её не было видно."""
    for enabled in (True, False):
        window.record_button.setEnabled(enabled)
        image = window.record_button.grab().toImage()
        edge = image.pixelColor(6, image.height() // 2)
        assert edge.alpha() == 255, f"кнопка обязана иметь непрозрачный фон (enabled={enabled})"
        assert max(edge.red(), edge.green(), edge.blue()) > 24, (
            f"фон кнопки не должен сливаться с фоном карточки (enabled={enabled})"
        )


def test_wheel_never_changes_settings_and_always_scrolls(window, application):
    """Регресс: колесо над строкой настроек меняло значение, если поле было в фокусе."""
    window.settings_button.setChecked(True)
    window.resize(700, 460)
    application.processEvents()
    scrollbar = window.centralWidget().verticalScrollBar()
    assert scrollbar.maximum() > 0, "для проверки прокрутки настройки должны не помещаться в окно"

    fields = (
        ("paste_combo", window.paste_combo, lambda w: w.currentText()),
        ("mic_combo", window.mic_combo, lambda w: w.currentText()),
        ("delay_spin", window.delay_spin, lambda w: w.value()),
        ("duration_slider", window.duration_slider, lambda w: w.value()),
    )
    for name, widget, read in fields:
        for focused in (False, True):
            if focused:
                widget.setFocus()
                application.processEvents()
            scrollbar.setValue(0)
            before = read(widget)
            QApplication.sendEvent(widget, wheel_event(-3))
            application.processEvents()
            assert read(widget) == before, f"{name}: колесо изменило настройку (фокус={focused})"
            assert scrollbar.value() > 0, f"{name}: колесо обязано прокручивать страницу"


def test_settings_values_change_by_click_not_wheel(window):
    """Значение меняется обычным способом: колесо не единственный и не случайный путь."""
    combo = window.paste_combo
    combo.setCurrentIndex(1)
    assert combo.currentText() == "Shift+Insert"
    window.delay_spin.setValue(900)
    assert window.delay_spin.value() == 900
