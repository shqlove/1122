from pathlib import Path

import pytest

from dialogue_tool import phone_distribution as phones
from dialogue_tool.workbench import save_json


@pytest.fixture
def setup(tmp_path, monkeypatch):
    queue = phones.PhoneDistribution(tmp_path)
    save_json(queue.root / "phones.json", {"01": "stable1", "02": "stable2"})
    library = {}
    for media_id in ("a", "b"):
        path = tmp_path / (media_id + ".mp4")
        path.write_bytes(media_id.encode())
        library[media_id] = {"path": str(path), "kind": "post", "available": True}
    online = {"stable1": {"serial": "live1"}, "stable2": {"serial": "live2"}}
    monkeypatch.setattr(phones, "devices", lambda: online)
    monkeypatch.setattr(phones.time, "sleep", lambda _: None)
    return queue, library, online


def test_timeout_then_reopen_verifies_before_resend(setup, monkeypatch):
    queue, lib, online = setup
    online.pop("stable2")
    received = set()
    calls = []

    def send(action, devices, data):
        calls.append((action, devices))
        assert data["fileType"] == 2
        received.add(phones.file_digest(Path(data["filePath"])))
        raise TimeoutError("回执丢失")

    monkeypatch.setattr(phones, "request", send)
    monkeypatch.setattr(phones, "find_on_phone", lambda uid, size, sha: "/DCIM/m.mov" if sha in received else None)
    first, second = queue.start({"01": "a", "02": "b"}, lib)
    assert (first["phase"], second["phase"]) == ("verify", "queued")
    reopened = phones.PhoneDistribution(queue.root.parent)
    assert reopened.resume(first["id"], True)["phase"] == "done"
    assert calls == [("uploadFile", "live1")]
    online["stable2"] = {"serial": "reconnected2"}
    reopened.resume(second["id"])
    assert calls[-1] == ("uploadFile", "reconnected2")


def test_retry_only_after_negative_verification_and_explicit_request(setup, monkeypatch):
    queue, lib, _ = setup
    now = [1000]
    calls = []
    monkeypatch.setattr(phones.time, "time", lambda: now[0])
    monkeypatch.setattr(phones, "request", lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(phones, "find_on_phone", lambda *args: None)
    job, = queue.start({"01": "a"}, lib)
    assert queue.resume(job["id"], True)["phase"] == "verify"
    now[0] += 121
    assert queue.resume(job["id"])["phase"] == "missing"
    assert queue.start({"01": "a"}, lib)[0]["attempts"] == 1
    queue.resume(job["id"], True)
    assert len(calls) == 2


def test_reject_entire_invalid_assignment_before_transferring(setup):
    queue, lib, _ = setup
    lib["b"]["trashed"] = True
    with pytest.raises(ValueError):
        queue.start({"01": "a", "02": "b"}, lib)
    assert not queue.jobs()


def test_batch_receipts_keep_exact_pairs_and_per_click_outcomes(setup, monkeypatch):
    queue, lib, _ = setup
    remote = {("stable2", phones.file_digest(Path(lib["a"]["path"])))}
    uploads = []

    def send(action, devices, data):
        uploads.append((devices, Path(data["filePath"]).name))
        remote.add((devices.replace("live", "stable"), phones.file_digest(Path(data["filePath"]))))

    monkeypatch.setattr(phones, "request", send)
    monkeypatch.setattr(phones, "find_on_phone",
                        lambda uid, size, sha: "/DCIM/test.mov" if (uid, sha) in remote else None)
    plan = {"01": ["a", "b"], "02": ["a"]}
    first = queue.start(plan, lib)[0]["batch_id"]
    second = queue.start(plan, lib)[0]["batch_id"]
    receipts = {b["id"]: b["rows"] for b in phones.PhoneDistribution(queue.root.parent).state()["batches"]}
    assert uploads == [("live1", "a.mp4"), ("live1", "b.mp4")]
    assert [(r["slot"], r["file"], r["result"]) for r in receipts[first]] == [
        ("01", "a.mp4", "uploaded"), ("01", "b.mp4", "uploaded"), ("02", "a.mp4", "found")]
    assert all(r["result"] == "skipped_record" and not r["submitted"] for r in receipts[second])


def _save_publication(queue, digest, status="published", *, source=None):
    batch_id = "2" * 32
    save_json(queue.root.parent / "douyin-publish" / "batches" / f"{batch_id}.json", {
        "id": batch_id, "mode": "publish", "status": "finished", "created": 1,
        "rows": [{
            "slot": "01", "uid": "stable1", "filename": "IMG_0777.MOV", "status": status,
            "account": "历史抖音号", "submitted": 2, "updated": 3,
            "media_sha256": digest, "source_sha256": source,
        }],
    })


def test_distribution_rejects_reexport_of_published_video_before_writes(setup, monkeypatch):
    queue, lib, _ = setup
    old = phones.file_digest(Path(lib["a"]["path"]))
    new = phones.file_digest(Path(lib["b"]["path"]))
    origin = "11" * 32
    queue.postprocess_root = queue.root.parent / "postprocessing"
    save_json(queue.postprocess_root / "batches" / "history" / "manifest.json", {
        "jobs": [
            {"outputSha256": old, "sourceSha256": origin},
            {"outputSha256": new, "sourceSha256": origin},
        ],
    })
    _save_publication(queue, old)
    calls = []
    monkeypatch.setattr(phones, "request", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="01 号手机.*历史抖音号.*已发布"):
        queue.start({"02": "b"}, lib)
    assert not queue.jobs() and not calls


def test_distribution_keeps_readonly_verify_after_publication_but_denies_upload(setup, monkeypatch):
    queue, lib, online = setup
    calls = []
    found = False

    def find(*_args):
        return "/DCIM/100APPLE/IMG_0777.MOV" if found else None

    monkeypatch.setattr(phones, "request", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(phones, "find_on_phone", find)
    second = online.pop("stable2")
    verifying, queued = queue.start({"01": "a", "02": "a"}, lib)
    assert [verifying["phase"], queued["phase"]] == ["verify", "queued"]
    assert calls == [("uploadFile",)]
    _save_publication(queue, verifying["sha256"])
    online["stable2"] = second

    before = queue.jobs()
    with pytest.raises(ValueError, match="历史抖音号"):
        queue.resume(queued["id"])
    with pytest.raises(ValueError, match="历史抖音号"):
        queue.resume(verifying["id"], resend=True)
    assert queue.jobs() == before and calls == [("uploadFile",)]

    checked = queue.resume(verifying["id"])
    assert checked["phase"] == "verify"
    assert "历史抖音号" in checked["detail"]
    found = True
    assert queue.resume(verifying["id"])["phase"] == "done"
    assert calls == [("uploadFile",)]
    assert all(job["publication"]["status"] == "published" for job in queue.state()["jobs"])


def test_distribution_does_not_block_pending_publication(setup, monkeypatch):
    queue, lib, _ = setup
    _save_publication(queue, phones.file_digest(Path(lib["a"]["path"])), "reviewing")
    calls = []
    monkeypatch.setattr(phones, "request", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(phones, "find_on_phone",
                        lambda *args: "/DCIM/100APPLE/IMG_0777.MOV" if calls else None)
    row, = queue.start({"02": "a"}, lib)
    assert row["phase"] == "done"
    assert calls == [("uploadFile",)]
