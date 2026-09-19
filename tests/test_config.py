import json

import pytest

from voxturbo.config import Settings, data_dir, load_settings, save_settings


def test_missing_defaults(tmp_path):
    assert load_settings(tmp_path / "missing.json") == (Settings(), None)


def test_roundtrip(tmp_path):
    path = tmp_path / "кириллица" / "settings.json"
    settings = Settings(hotkey="Ctrl+F8", microphone="Микрофон | WASAPI", restore_delay_ms=900)
    save_settings(path, settings)
    assert load_settings(path) == (settings, None)


@pytest.mark.parametrize("raw", ["{", "[]", '{"schema_version":2}', '{"restore_delay_ms":true}'])
def test_corrupt_preserved(tmp_path, raw):
    path = tmp_path / "settings.json"
    path.write_text(raw, encoding="utf-8")
    settings, warning = load_settings(path)
    assert settings == Settings() and warning
    assert path.read_text(encoding="utf-8") == raw


def test_failed_atomic_replace_keeps_old(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    save_settings(path, Settings())

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("voxturbo.config.os.replace", fail)
    with pytest.raises(OSError):
        save_settings(path, Settings(hotkey="F8"))
    assert json.loads(path.read_text())["hotkey"] == "F4"
    assert not list(tmp_path.glob("*.tmp"))


def test_data_dir_migrates_legacy_deltaecho_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("VOXTURBO_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    legacy = tmp_path / "DeltaEcho"
    (legacy / "models" / "parakeet-v3-int8").mkdir(parents=True)
    (legacy / "models" / "parakeet-v3-int8" / "vocab.txt").write_text("weights", encoding="utf-8")

    assert data_dir() == tmp_path / "VoxTurbo"
    assert (tmp_path / "VoxTurbo" / "models" / "parakeet-v3-int8" / "vocab.txt").read_text() == "weights"
    assert not legacy.exists()


def test_data_dir_keeps_existing_target_and_legacy(tmp_path, monkeypatch):
    monkeypatch.delenv("VOXTURBO_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    (tmp_path / "VoxTurbo").mkdir()
    (tmp_path / "VoxTurbo" / "settings.json").write_text("{}", encoding="utf-8")
    legacy = tmp_path / "DeltaEcho"
    legacy.mkdir()
    (legacy / "settings.json").write_text('{"hotkey": "F8"}', encoding="utf-8")

    assert data_dir() == tmp_path / "VoxTurbo"
    assert (tmp_path / "VoxTurbo" / "settings.json").read_text() == "{}"
    assert (legacy / "settings.json").read_text() == '{"hotkey": "F8"}'


def test_data_dir_override_skips_migration(tmp_path, monkeypatch):
    (tmp_path / "DeltaEcho").mkdir()
    override = tmp_path / "explicit"
    monkeypatch.setenv("VOXTURBO_DATA_DIR", str(override))

    assert data_dir() == override.resolve()
    assert (tmp_path / "DeltaEcho").is_dir()
