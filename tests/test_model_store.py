import hashlib
import io
import os
import threading
import urllib.error
from types import SimpleNamespace

import pytest

from voxturbo import model_store


class Response(io.BytesIO):
    def geturl(self):
        return "https://cdn.huggingface.co/test.bin"


@pytest.fixture
def tiny_manifest(monkeypatch):
    contents = {"config.json": b'{"features_size":128}', "encoder-model.int8.onnx": b"example ONNX bytes"}
    files = tuple(
        model_store.ModelFile(name, len(data), hashlib.sha256(data).hexdigest())
        for name, data in contents.items()
    )
    monkeypatch.setattr(model_store, "MODEL_FILES", files)
    monkeypatch.setattr(model_store, "MODEL_SIZE_BYTES", sum(item.size for item in files))
    monkeypatch.setattr(model_store.shutil, "disk_usage", lambda _: SimpleNamespace(free=10**12))
    return contents


def install_fake_opener(monkeypatch, contents):
    requests = []

    def open_request(request, timeout):
        requests.append(request.full_url)
        assert timeout == 30
        name = request.full_url.rsplit("/", 1)[-1]
        return Response(contents[name])

    monkeypatch.setattr(
        model_store.urllib.request, "build_opener", lambda *_: SimpleNamespace(open=open_request)
    )
    return requests


def test_production_manifest_is_pinned_and_complete():
    assert len(model_store.MODEL_REVISION) == 40
    assert model_store.MODEL_SIZE_BYTES == 670_480_039
    assert {item.name for item in model_store.MODEL_FILES} == {
        "config.json",
        "vocab.txt",
        "encoder-model.int8.onnx",
        "decoder_joint-model.int8.onnx",
    }
    assert all(len(item.sha256) == 64 and "/main/" not in item.url for item in model_store.MODEL_FILES)


def test_download_verifies_files_and_preserves_valid_ones(tmp_path, monkeypatch, tiny_manifest):
    requests = install_fake_opener(monkeypatch, tiny_manifest)
    store = model_store.ModelStore(tmp_path / "модель с пробелом")
    assert not store.is_ready()
    progress = []
    store.download(lambda done, total: progress.append((done, total)), threading.Event())
    assert store.is_ready()
    store.verify()
    assert progress[-1] == (model_store.MODEL_SIZE_BYTES, model_store.MODEL_SIZE_BYTES)
    assert len(requests) == 2
    store.download(lambda *_: None, threading.Event())
    assert len(requests) == 2  # No network for already verified files.
    assert not list(store.root.glob("*.part"))


@pytest.mark.parametrize("payload", [b"bad", b"x" * 20, b"x" * 500])
def test_broken_download_is_never_promoted(tmp_path, monkeypatch, tiny_manifest, payload):
    changed = dict(tiny_manifest)
    changed["config.json"] = payload
    install_fake_opener(monkeypatch, changed)
    store = model_store.ModelStore(tmp_path)
    with pytest.raises(model_store.ModelError, match="размер|SHA"):
        store.download(lambda *_: None, threading.Event())
    assert not store.is_ready()
    assert not (tmp_path / "config.json").exists()
    assert not list(tmp_path.glob("*.part"))


def test_same_size_corruption_is_caught_by_full_verify(tmp_path, monkeypatch, tiny_manifest):
    install_fake_opener(monkeypatch, tiny_manifest)
    store = model_store.ModelStore(tmp_path)
    store.download(lambda *_: None, threading.Event())
    (tmp_path / "config.json").write_bytes(b"x" * len(tiny_manifest["config.json"]))
    assert store.is_ready()  # Cheap UI check intentionally does not hash hundreds of MB.
    with pytest.raises(model_store.ModelError, match="SHA"):
        store.verify()
    store.download(lambda *_: None, threading.Event())
    store.verify()


def test_cancel_during_download_cleans_partial_file(tmp_path, monkeypatch, tiny_manifest):
    install_fake_opener(monkeypatch, tiny_manifest)
    cancel = threading.Event()

    def progress(done, _total):
        if done:
            cancel.set()

    store = model_store.ModelStore(tmp_path)
    with pytest.raises(model_store.ModelDownloadCancelled):
        store.download(progress, cancel)
    assert not store.is_ready()
    assert not list(tmp_path.glob("*.part"))
    assert not (tmp_path / "config.json").exists()


def test_network_failure_has_bounded_retries(tmp_path, monkeypatch, tiny_manifest):
    attempts = []

    def fail(*_args, **_kwargs):
        attempts.append(1)
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(model_store.urllib.request, "build_opener", lambda *_: SimpleNamespace(open=fail))
    cancel = SimpleNamespace(is_set=lambda: False, wait=lambda _: False)
    with pytest.raises(model_store.ModelError, match="связи"):
        model_store.ModelStore(tmp_path).download(lambda *_: None, cancel)
    assert len(attempts) == 3


def test_disk_full_prevents_network(tmp_path, monkeypatch, tiny_manifest):
    monkeypatch.setattr(model_store.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    requests = install_fake_opener(monkeypatch, tiny_manifest)
    with pytest.raises(model_store.ModelError, match="места"):
        model_store.ModelStore(tmp_path).download(lambda *_: None, threading.Event())
    assert requests == []


def test_existing_valid_file_survives_later_file_failure(tmp_path, monkeypatch, tiny_manifest):
    (tmp_path / "config.json").write_bytes(tiny_manifest["config.json"])
    contents = dict(tiny_manifest)
    contents["encoder-model.int8.onnx"] = b"bad"
    install_fake_opener(monkeypatch, contents)
    with pytest.raises(model_store.ModelError):
        model_store.ModelStore(tmp_path).download(lambda *_: None, threading.Event())
    assert (tmp_path / "config.json").read_bytes() == tiny_manifest["config.json"]


def test_redirect_must_remain_https():
    handler = model_store._HTTPSRedirectHandler()
    with pytest.raises(model_store.ModelError, match="переадресацию"):
        handler.redirect_request(None, None, 302, "redirect", {}, "http://example.com/model")


def test_stale_partial_hard_link_does_not_truncate_external_file(tmp_path, monkeypatch, tiny_manifest):
    original = tmp_path / "unrelated.txt"
    original.write_bytes(b"keep this unrelated data")
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    os.link(original, model_dir / "config.json.part")
    install_fake_opener(monkeypatch, tiny_manifest)
    model_store.ModelStore(model_dir).download(lambda *_: None, threading.Event())
    assert original.read_bytes() == b"keep this unrelated data"
