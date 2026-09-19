# Сторонние компоненты VoxTurbo

VoxTurbo использует перечисленные ниже компоненты. Их авторские права и лицензии
принадлежат соответствующим правообладателям. Собственный код VoxTurbo
распространяется по лицензии MIT (файл `LICENSE` в корне репозитория); этот документ
не меняет лицензии сторонних компонентов и не заменяет их полные лицензионные тексты.

Сборка копирует реальные лицензионные файлы из установленного Python-окружения в
каталог `_internal/licenses/` внутри каталога установки и сохраняет metadata
дистрибутивов. Точные версии входят в `python-environment.txt` и
`build-manifest.json` конкретной сборки. Модель распространяется отдельно и не
входит в установщик.

| Компонент | Назначение | Лицензия / источник |
| --- | --- | --- |
| CPython | Встроенный Python runtime | Python Software Foundation License; https://www.python.org/psf/license/ |
| PySide6 / Shiboken6 | Привязки Qt и интерфейс | LGPL-3.0 / GPL / коммерческие условия Qt в зависимости от компонента; https://doc.qt.io/qtforpython-6/licenses.html |
| Qt | GUI, ввод, изображения и системная интеграция | Лицензии конкретных используемых модулей и third-party notices; https://www.qt.io/licensing/ |
| NumPy | Аудиомассивы и вычисления | BSD-3-Clause; лицензии включённых native библиотек находятся в пакетных notices; https://numpy.org/ |
| sounddevice | Доступ к аудиоустройствам | MIT; https://github.com/spatialaudio/python-sounddevice |
| PortAudio | Нативный аудиоввод | MIT-style PortAudio license; https://www.portaudio.com/license.html |
| CFFI / pycparser | Вызовы native audio API | MIT-0 / BSD-3-Clause для закреплённых версий; https://cffi.readthedocs.io/ и https://github.com/eliben/pycparser |
| ONNX Runtime | Локальное CPU-распознавание | MIT и пакетные ThirdPartyNotices; https://github.com/microsoft/onnxruntime |
| onnx-asr | Адаптер ONNX ASR | MIT; https://github.com/istupakov/onnx-asr |
| FlatBuffers / Protobuf / packaging | Зависимости ONNX Runtime и metadata | Лицензии закреплённых пакетов включены в `_internal/licenses/` и dist-info; https://github.com/google/flatbuffers , https://github.com/protocolbuffers/protobuf , https://github.com/pypa/packaging |

PyInstaller используется только при сборке; bootloader распространяется на условиях
GPL-2.0-or-later с исключением для упаковки приложений. Полный текст и исключение:
https://pyinstaller.org/en/stable/license.html . Использование упаковщика не
переименовывает лицензию приложения автоматически.

Inno Setup используется для создания установщика. Его лицензия и copyright:
https://github.com/jrsoftware/issrc/blob/main/license.txt . Build tool не является
отдельным устанавливаемым компонентом VoxTurbo. Проверенный официальный compiler
6.7.1 выводит «Non-commercial use only»; возможность коммерческого использования
конкретной сборки compiler требует отдельной проверки/лицензии до коммерческого
выпуска. Локальный проверочный артефакт не подтверждает наличие такой лицензии.

## Локальная модель

Первый профиль использует NVIDIA Parakeet TDT 0.6B v3 в ONNX int8-представлении.
Перед скачиванием приложение показывает точный источник, размер и лицензию;
закреплённый manifest модели является источником revision и SHA-256 каждого файла.
Установщик не содержит весов и не выполняет скрытую загрузку.

Исходная модель: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3 .
ONNX-конверсия: https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx .
Исходные веса NVIDIA опубликованы по CC BY 4.0; название модели, автор NVIDIA,
ссылка на источник, лицензия https://creativecommons.org/licenses/by/4.0/ и факт
ONNX-конверсии/квантования должны сохраняться при отдельном распространении.
Применимые лицензионные условия и атрибуция модели должны сохраняться при её
скачивании/отдельном распространении. Лицензия библиотеки onnx-asr не заменяет
лицензию весов модели.

## Qt и возможность замены библиотек

Сборка `onedir` сохраняет Qt/native DLL отдельными файлами; VoxTurbo не требует
изменённых приватных сборок Qt. Пользовательские права по LGPL, включая разрешённые
модификацию, замену библиотек и отладку таких изменений, не ограничиваются этим
уведомлением. Исходники соответствующей версии Qt/PySide и тексты лицензий доступны
у Qt Project: https://download.qt.io/official_releases/QtForPython/ и
https://download.qt.io/official_releases/qt/ . Перед публичным выпуском нужно
проверить соответствие конкретного состава пакета и способов предоставления
исходников требованиям включённых лицензий; один список URL не является отчётом
о завершённой лицензионной проверке.

## Статус подписи и приватность

Если в отчёте сборки указано `NotSigned`, установщик не имеет доверенной цифровой
подписи издателя; Windows может показать предупреждение. SHA-256 проверяет
целостность конкретного файла и не заменяет подпись. Не требуется отключать
Defender, SmartScreen или другие средства защиты.

Распознавание работает локально после явной загрузки модели. Аудио, распознанный
текст и буфер обмена не входят в лицензионные/диагностические файлы сборки.
