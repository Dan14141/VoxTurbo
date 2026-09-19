from types import SimpleNamespace

import numpy as np
import pytest

from voxturbo import audio


class FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        self.stopped = False
        self.aborted = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True

    def feed(self, samples, status=None):
        self.kwargs["callback"](samples, len(samples), None, status)


@pytest.fixture
def backend(monkeypatch):
    streams = []

    def stream_factory(**kwargs):
        stream = FakeStream(**kwargs)
        streams.append(stream)
        return stream

    device = {"name": "Тестовый микрофон", "max_input_channels": 1, "hostapi": 0, "default_samplerate": 48000}
    fake = SimpleNamespace(
        query_devices=lambda *_: device,
        query_hostapis=lambda: [{"name": "Windows WASAPI"}],
        check_input_settings=lambda **_: None,
        InputStream=stream_factory,
        streams=streams,
        device=device,
    )
    monkeypatch.setattr(audio, "_get_backend", lambda: fake)
    return fake


def test_device_listing_filters_outputs_and_keeps_native_indexes(backend):
    backend.query_devices = lambda: [
        {"name": "Динамики", "max_input_channels": 0, "hostapi": 0},
        backend.device,
    ]
    assert audio.list_input_devices() == [audio.InputDevice(1, "Тестовый микрофон", "Windows WASAPI")]


def test_recording_copies_portaudio_memory_and_closes_stream(backend):
    recorder = audio.AudioRecorder()
    recorder.start(device=7)
    stream = backend.streams[0]
    source = np.full((1600, 1), 0.25, dtype=np.float32)
    stream.feed(source)
    source[:] = 0  # PortAudio may immediately reuse its buffer.
    assert recorder.elapsed == pytest.approx(0.1)
    assert recorder.level == pytest.approx(0.25)
    clip = recorder.stop()
    assert stream.closed and stream.stopped
    assert stream.kwargs["device"] == 7
    assert clip.sample_rate == 16000
    assert clip.samples.shape == (1600,)
    assert np.all(clip.samples == 0.25)


def test_unsupported_16k_uses_actual_default_rate(backend):
    def check(**kwargs):
        if kwargs["samplerate"] == 16000:
            raise RuntimeError("unsupported")

    backend.check_input_settings = check
    recorder = audio.AudioRecorder()
    recorder.start()
    backend.streams[0].feed(np.zeros((4800, 1), dtype=np.float32))
    clip = recorder.stop()
    assert clip.sample_rate == 48000
    assert clip.duration == pytest.approx(0.1)


def test_unsupported_default_rate_fails_without_opening_stream(backend):
    backend.device["default_samplerate"] = 96000
    backend.check_input_settings = lambda **_: (_ for _ in ()).throw(RuntimeError("unsupported"))
    with pytest.raises(audio.AudioError, match="96000"):
        audio.AudioRecorder().start()
    assert not backend.streams


def test_capture_stops_at_the_configured_limit(backend):
    recorder = audio.AudioRecorder()
    recorder.start(limit_seconds=30)
    stream = backend.streams[0]
    stream.feed(np.ones((480_100, 1), dtype=np.float32))
    stream.feed(np.ones((500, 1), dtype=np.float32))
    assert recorder.limit_reached
    assert recorder.elapsed == 30
    assert not stream.stopped  # Never stop PortAudio from its own callback.
    assert len(recorder.stop().samples) == 480_000


def test_capture_honours_a_shorter_and_the_longest_limit(backend):
    short = audio.AudioRecorder()
    short.start(limit_seconds=5)
    backend.streams[-1].feed(np.ones((16_000 * 6, 1), dtype=np.float32))
    assert short.limit_reached
    assert short.elapsed == 5

    longest = audio.AudioRecorder()
    longest.start(limit_seconds=audio.MAX_RECORDING_SECONDS)
    backend.streams[-1].feed(np.ones((16_000 * 59, 1), dtype=np.float32))
    assert not longest.limit_reached
    assert longest.elapsed == 59


@pytest.mark.parametrize("value", [4, 61, 0, -5, True, 30.5, "30"])
def test_start_rejects_a_limit_outside_the_allowed_range(backend, value):
    with pytest.raises(audio.AudioError, match="Длина диктовки"):
        audio.AudioRecorder().start(limit_seconds=value)
    assert backend.streams == []


def test_overflow_is_reported_instead_of_silent_data_loss(backend):
    recorder = audio.AudioRecorder()
    recorder.start()
    backend.streams[0].feed(np.ones((100, 1), dtype=np.float32), status=True)
    with pytest.raises(audio.AudioError, match="потеряны"):
        recorder.stop()
    assert backend.streams[0].closed


def test_cancel_releases_audio_and_allows_restart(backend):
    recorder = audio.AudioRecorder()
    recorder.start()
    backend.streams[0].feed(np.ones((100, 1), dtype=np.float32))
    with pytest.raises(audio.AudioError, match="уже"):
        recorder.start()
    recorder.cancel()
    assert backend.streams[0].aborted and backend.streams[0].closed
    assert recorder.elapsed == recorder.level == 0
    recorder.cancel()
    recorder.start()
    assert recorder.stop().samples.size == 0


def test_start_failure_closes_created_stream(backend):
    def broken_factory(**kwargs):
        stream = FakeStream(**kwargs)
        stream.start = lambda: (_ for _ in ()).throw(RuntimeError("device unplugged"))
        backend.streams.append(stream)
        return stream

    backend.InputStream = broken_factory
    with pytest.raises(audio.AudioError, match="микрофон"):
        audio.AudioRecorder().start()
    assert backend.streams[0].closed
