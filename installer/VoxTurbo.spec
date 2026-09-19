# -*- mode: python ; coding: utf-8 -*-
"""Windows x64 onedir bundle. Run from the checked-out project, not site-packages."""

from importlib import metadata
from pathlib import Path
import sys

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

project_root = Path(SPECPATH).resolve().parent
entrypoint = project_root / "src" / "voxturbo" / "__main__.py"
if not entrypoint.is_file():
    raise FileNotFoundError(f"Missing application entrypoint: {entrypoint}")

app_version = metadata.version("voxturbo")
version_numbers = tuple(int(part) for part in app_version.split(".")[:3]) + (0,)
datas = [
    (str(project_root / "README.md"), "."),
    (str(project_root / "THIRD-PARTY-NOTICES.md"), "."),
]
application_license = project_root / "LICENSE"
if not application_license.is_file():
    raise FileNotFoundError("LICENSE is required for distribution")
datas.append((str(application_license), "."))
guide = project_root / "docs" / "user-guide.md"
if guide.is_file():
    datas.append((str(guide), "docs"))
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if not python_license.is_file():
    raise FileNotFoundError("The Python runtime LICENSE.txt is required for redistribution")
datas.append((str(python_license), "licenses/Python"))
inno_license = project_root / "build" / "tools" / "InnoSetup" / "license.txt"
if inno_license.is_file():
    datas.append((str(inno_license), "licenses/InnoSetup"))
extra_licenses = project_root / "installer" / "licenses"
if extra_licenses.is_dir():
    for license_file in extra_licenses.rglob("*"):
        if license_file.is_file():
            datas.append((str(license_file), str(Path("licenses") / license_file.relative_to(extra_licenses).parent)))
# scripts/build.ps1 кладёт текст лицензии приложения как .txt: его показывает
# установщик и он остаётся в {app}\licenses\VoxTurbo.
staged_license = project_root / "build" / "licenses" / "VoxTurbo" / "LICENSE.txt"
if staged_license.is_file():
    datas.append((str(staged_license), "licenses/VoxTurbo"))

# Explicitly keep package assets/metadata used by the lazy ONNX loader. No weights
# are taken from the user's model cache and no downloads run during the build.
datas += collect_data_files("onnx_asr")
hiddenimports = collect_submodules("onnx_asr")
hiddenimports += ["onnxruntime", "sounddevice", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"]
binaries = collect_dynamic_libs("onnxruntime")
binaries += collect_dynamic_libs("_sounddevice_data", search_patterns=["libportaudio64bit.dll"])
datas += collect_data_files("_sounddevice_data", includes=["portaudio-binaries/README.md"])

runtime_distributions = (
    "voxturbo",
    "onnx-asr",
    "onnxruntime",
    "numpy",
    "sounddevice",
    "cffi",
    "pycparser",
    "PySide6",
    "PySide6_Essentials",
    "PySide6_Addons",
    "shiboken6",
)
for distribution_name in (*runtime_distributions, "flatbuffers", "packaging", "protobuf"):
    datas += copy_metadata(distribution_name, recursive=True)

# Dist-info metadata above preserves the original license paths. Also expose
# actual installed runtime license texts under licenses/ for human inspection.
license_roots = set()
for distribution_name in (*runtime_distributions, "pyinstaller", "flatbuffers", "packaging", "protobuf"):
    distribution = metadata.distribution(distribution_name)
    for relative_file in distribution.files or ():
        parts = Path(str(relative_file)).parts
        if ".." in parts:
            continue
        lowered = str(relative_file).lower()
        filename = Path(str(relative_file)).name.lower()
        is_license = filename.startswith(("license", "copying", "notice", "copyright", "thirdparty", "third_party"))
        is_license = is_license or "/licenses/" in lowered.replace("\\", "/")
        if not is_license:
            continue
        source = Path(distribution.locate_file(relative_file))
        if source.is_file():
            destination = str(Path("licenses") / distribution_name / Path(str(relative_file)).parent)
            key = (str(source), destination)
            if key not in license_roots:
                license_roots.add(key)
                datas.append(key)

version_resource = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=version_numbers,
        prodvers=version_numbers,
        mask=0x3F,
        flags=0,
        OS=0x40004,
        fileType=0x1,
        subtype=0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "VoxTurbo"),
                        StringStruct("FileDescription", "VoxTurbo — локальная диктовка"),
                        StringStruct("FileVersion", app_version),
                        StringStruct("InternalName", "VoxTurbo"),
                        StringStruct("OriginalFilename", "VoxTurbo.exe"),
                        StringStruct("ProductName", "VoxTurbo"),
                        StringStruct("ProductVersion", app_version),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

analysis = Analysis(
    [str(entrypoint)],
    pathex=[str(project_root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "torch", "tensorflow", "pytest", "ruff"],
    noarchive=False,
)
# The upstream sounddevice hook includes every bundled platform/ASIO binary.
# This product supports Windows x64's ordinary host APIs, so keep that one DLL.
def keep_portaudio_entry(entry):
    destination = entry[0].replace("\\", "/")
    if "_sounddevice_data/portaudio-binaries/" not in destination:
        return True
    return Path(destination).name in {"libportaudio64bit.dll", "README.md"}


analysis.binaries = [entry for entry in analysis.binaries if keep_portaudio_entry(entry)]
analysis.datas = [entry for entry in analysis.datas if keep_portaudio_entry(entry)]
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="VoxTurbo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    version=version_resource,
    uac_admin=False,
    icon=str(project_root / "installer" / "voxturbo.ico"),
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="VoxTurbo",
)
