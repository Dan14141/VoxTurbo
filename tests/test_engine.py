from types import SimpleNamespace

import numpy as np
import pytest

from voxturbo import engine
from voxturbo.audio import AudioClip


def speech(sample_rate=16000, seconds=1):
    return AudioClip(np.full(int(sample_rate * seconds), 0.1, dtype=np.float32), sample_rate)


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    events = []
    store = SimpleNamespace(root=tmp_path, verify=lambda: events.append("verify"))
    model = SimpleNamespace(recognize=lambda samples, sample_rate: "  Привет, мир!  ")

    def load_model(name, path, **kwargs):
        events.append((name, path, kwargs))
        return model

    packages = {
        "onnxruntime": SimpleNamespace(SessionOptions=SimpleNamespace),
        "onnx_asr": SimpleNamespace(load_model=load_model),
    }
    monkeypatch.setattr(engine.importlib, "import_module", lambda name: packages[name])
    return engine.ParakeetEngine(store), events, model


def test_lazy_load_is_verified_once_and_cannot_resolve_hub_repo(fake_engine):
    instance, events, _ = fake_engine
    assert events == []
    assert instance.transcribe(speech()) == "Привет, мир!"
    assert instance.transcribe(speech()) == "Привет, мир!"
    assert events[0] == "verify"
    assert len(events) == 2
    name, path, options = events[1]
    assert name == "nemo-conformer-tdt"
    assert path == instance.store.root
    assert options["quantization"] == "int8"
    assert options["providers"] == ["CPUExecutionProvider"]
    assert 1 <= options["sess_options"].intra_op_num_threads <= 4


@pytest.mark.parametrize(
    "clip",
    [
        AudioClip(np.empty(0, dtype=np.float32), 16000),
        AudioClip(np.zeros(16000, dtype=np.float32), 16000),
        AudioClip(np.full(100, 0.1, dtype=np.float32), 16000),
    ],
)
def test_empty_short_and_silent_audio_does_not_load_model(fake_engine, clip):
    instance, events, _ = fake_engine
    assert instance.transcribe(clip) == ""
    assert events == []


@pytest.mark.parametrize(
    "clip, message",
    [
        (AudioClip(np.ones((100, 2), dtype=np.float32), 16000), "одномерный"),
        (AudioClip(np.ones(100, dtype=np.int16), 16000), "float32"),
        (speech(96000), "Частота"),
        (speech(seconds=61), "предел"),
        (AudioClip(np.full(16000, np.nan, dtype=np.float32), 16000), "повреждённые"),
        (AudioClip(np.full(16000, 30000, dtype=np.float32), 16000), "громкость"),
    ],
)
def test_invalid_audio_is_rejected_before_loading(fake_engine, clip, message):
    instance, events, _ = fake_engine
    with pytest.raises(engine.EngineError, match=message):
        instance.transcribe(clip)
    assert events == []


def test_actual_sample_rate_and_contiguous_array_reach_backend(fake_engine):
    instance, _, model = fake_engine
    seen = []

    def recognize(samples, sample_rate):
        seen.append((samples.flags.c_contiguous, sample_rate))
        return "Тест."

    model.recognize = recognize
    samples = np.full(96000, 0.1, dtype=np.float32)[::2]
    assert instance.transcribe(AudioClip(samples, 48000)) == "Тест."
    assert seen == [(True, 48000)]


def test_backend_failure_becomes_readable_error(fake_engine):
    instance, _, model = fake_engine
    model.recognize = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("native failure"))
    with pytest.raises(engine.EngineError, match="распознать"):
        instance.transcribe(speech())
