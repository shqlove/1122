from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from dialogue_tool import douyin_publish as publishing
from dialogue_tool.workbench import read_json, save_json


SLOT = "01"
ACCOUNT = "测试账号 test-01"
VIDEO = "IMG_0017.MOV"
MEDIA = {"filename": "IMG_0017.MOV", "size": 100, "media_sha256": "ab" * 32}
TARGET = (77, 94)
ALBUM_NAME = "tempVideo_12CD5758-63F1-48C7-95F3-7E68540E4BAE.MOV"
TRASH = (90, 94)
DELETE_VIDEO = (50, 84)


@pytest.fixture
def session(tmp_path, monkeypatch):
    publisher = publishing.DouyinPublish(tmp_path)
    save_json(publisher.distribution.root / "phones.json", {"01": "stable-01", "02": "stable-02"})
    monkeypatch.setattr(publishing, "devices", lambda: {
        "stable-01": {"serial": "live-01", "connectTime": 1},
        "stable-02": {"serial": "live-02", "connectTime": 1},
    })
    monkeypatch.setattr(publishing, "_read_video", lambda _uid, _filename: dict(MEDIA))
    monkeypatch.setattr(publishing, "desktop_directory", lambda: tmp_path)
    fake = SimpleNamespace(cover="#986da1", info_name="IMG_0017.MOV", taps=[], before_tap=None, scene="publish")

    def request(action, devices, data):
        assert devices in {"live-01", "live-02"}
        if action == "screenFile":
            image = Image.new("RGB", (200, 450), "white")
            draw = ImageDraw.Draw(image)
            if fake.scene == "publish":
                draw.rectangle((38, 40, 160, 130), fill=fake.cover)
                draw.rounded_rectangle((102, 412, 196, 439), radius=8, fill="#ff2852")
            elif fake.scene == "info":
                draw.rectangle((20, 80, 180, 210), fill=fake.cover)
                draw.rectangle((1, 230, 199, 425), fill="#444444")
                draw.text((12, 260), fake.info_name, fill="white")
            elif fake.scene == "fullscreen":
                draw.rectangle((20, 80, 180, 310), fill=fake.cover)
            elif fake.scene == "deletion":
                draw.rectangle((8, 300, 192, 419), fill="#d6d6d6")
                draw.rectangle((90, 376, 110, 381), fill="#e02029")
            elif fake.scene == "deletion_right":
                draw.rectangle((79, 350, 196, 425), fill="#d6d6d6")
                draw.rectangle((123, 404, 151, 413), fill="#e02029")
            image.save(Path(data["savePath"]) / "capture.png")
        elif action == "pointerEvent":
            fake.taps.append(data)
            if fake.before_tap:
                fake.before_tap()

    monkeypatch.setattr(publishing, "request", request)
    return publisher, fake


def _observation(publisher, batch_id):
    return publisher.screen(batch_id, SLOT)["observation_id"]


def _record(publisher, batch_id, status):
    return publisher.record(batch_id, SLOT, status, "核对通过", ACCOUNT, VIDEO,
                            MEDIA["media_sha256"], _observation(publisher, batch_id))


def _prepare(publisher, album_name=MEDIA["filename"]):
    batch_id = publisher.begin([SLOT])["id"]
    publisher.verify_media(batch_id, SLOT, album_name)
    _record(publisher, batch_id, "ready")
    publisher.end(batch_id)
    return batch_id


def _submit(publisher, batch_id, observation):
    return publisher.submit(batch_id, SLOT, observation, *TARGET, ACCOUNT, VIDEO, MEDIA["media_sha256"])


def _distribution_receipt(publisher, batch_id, created, *assignments):
    rows = [{"slot": slot, "job_id": job_id} for slot, job_id in assignments]
    save_json(publisher.distribution.root / "batches" / f"{batch_id}.json",
              {"id": batch_id, "action": "分发", "created": created, "rows": rows})


def test_precheck_pins_most_recent_distribution_to_exact_phone_file(session, monkeypatch):
    publisher, _ = session
    save_json(publisher.distribution.queue, {
        "old": {"uid": "stable-02", "file": "previous.mp4", "phase": "done", "remote": "/DCIM/IMG_0001.MOV",
                "sha256": "ef" * 32},
        "new": {"uid": "stable-01", "file": "chosen.mp4", "phase": "done", "remote": f"/DCIM/{VIDEO}",
                "sha256": MEDIA["media_sha256"]},
    })
    _distribution_receipt(publisher, "older", 1, ("02", "old"))
    _distribution_receipt(publisher, "latest", 2, ("01", "new"))
    batch = publisher.begin(["01", "02"])
    assert batch["distribution_batch_id"] == "latest"
    assert batch["rows"][0]["target"]["filename"] == VIDEO
    assert batch["rows"][1]["status"] == "blocked"
    assert "未给这台手机指定" in batch["rows"][1]["detail"]
    monkeypatch.setattr(publishing, "_read_video", lambda _uid, filename: dict(MEDIA, filename=filename))
    try:
        with pytest.raises(ValueError, match="不匹配最近分发"):
            publisher.verify_media(batch["id"], SLOT, "IMG_0001.MOV")
        assert publisher.verify_media(batch["id"], SLOT, VIDEO)["media_sha256"] == MEDIA["media_sha256"]
        _record(publisher, batch["id"], "ready")
    finally:
        publisher.end(batch["id"])

    _distribution_receipt(publisher, "changed", 3, ("02", "old"))
    with pytest.raises(ValueError, match="分发清单已更新"):
        publisher.confirm(batch["id"])


def test_repeat_of_same_distributed_video_skips_second_phone_after_first_ready(session):
    publisher, _ = session
    save_json(publisher.distribution.queue, {
        "01-file": {"uid": "stable-01", "file": "chosen.mp4", "phase": "done", "remote": f"/DCIM/{VIDEO}",
                    "sha256": MEDIA["media_sha256"]},
        "02-file": {"uid": "stable-02", "file": "chosen.mp4", "phase": "done", "remote": "/DCIM/IMG_0043.MOV",
                    "sha256": MEDIA["media_sha256"]},
    })
    _distribution_receipt(publisher, "latest", 2, ("01", "01-file"), ("02", "02-file"))
    batch_id = publisher.begin(["01", "02"])["id"]
    try:
        assert not publisher.skip_duplicate_target(batch_id, "02")
        publisher.verify_media(batch_id, SLOT, VIDEO)
        _record(publisher, batch_id, "ready")
        assert publisher.skip_duplicate_target(batch_id, "02")
        row = publisher._load(batch_id)["rows"][1]
        assert row["duplicate_reason"] == "same_batch" and "01 号手机" in row["detail"]
    finally:
        publisher.end(batch_id)


def test_worker_timeout_cannot_overwrite_successful_precheck(session):
    publisher, _ = session
    batch_id = publisher.begin([SLOT])["id"]
    try:
        publisher.verify_media(batch_id, SLOT, VIDEO)
        _record(publisher, batch_id, "ready")
        with pytest.raises(ValueError, match="保留原结果"):
            publisher.record(batch_id, SLOT, "blocked", "本台手机操作超时")
    finally:
        publisher.end(batch_id)
    assert publisher._load(batch_id)["rows"][0]["status"] == "ready"


def test_publishing_and_cleanup_share_the_device_lock_across_instances(session, monkeypatch):
    publisher, _ = session
    competing = publishing.DouyinPublish(publisher.root.parent)
    prepared = _prepare(publisher)
    publisher.confirm(prepared)
    try:
        assert competing.cleanup_state()["busy"] is True
        with pytest.raises(ValueError, match="抖音批次正在运行"):
            competing.begin_auto_cleanup()
    finally:
        publisher.end(prepared)

    _auto_cleanup_fixture(publisher, monkeypatch)
    cleanup = publisher.begin_auto_cleanup()
    try:
        assert competing.state()["busy"] is True
        with pytest.raises(ValueError, match="抖音批次正在运行"):
            competing.begin([SLOT])
        with pytest.raises(ValueError, match="抖音批次正在运行"):
            competing.confirm(prepared)
    finally:
        publisher.end(cleanup["id"])


def test_opening_photos_for_cleanup_invalidates_prior_preflight_on_that_phone(session, monkeypatch):
    publisher, _ = session
    prepared = _prepare(publisher)
    old_id = "1" * 32
    save_json(publisher.root / "batches" / f"{old_id}.json", {
        "id": old_id, "mode": "publish", "status": "finished", "created": 100,
        "rows": [{"slot": SLOT, "uid": "stable-01", "filename": "IMG_1234.MOV",
                  "album_filename": "IMG_1234.MOV", "status": "published", "media_sha256": "31" * 32}],
    })
    monkeypatch.setattr(publishing, "_read_video", lambda uid, filename: (
        {"filename": filename, "media_sha256": "31" * 32, "size": 101} if filename == "IMG_1234.MOV" else dict(MEDIA)
    ))
    cleanup = publisher.begin_cleanup([f"{old_id}:{SLOT}"])["id"]
    try:
        assert publisher.state()["batch"]["rows"][0]["status"] == "ready"
        publisher.cleanup_open(cleanup, SLOT)
        assert publisher.state()["batch"]["rows"][0]["status"] == "stale"
    finally:
        publisher.end(cleanup)

    reopened = publishing.DouyinPublish(publisher.root.parent)
    assert reopened.state()["batch"]["rows"][0]["status"] == "stale"
    with pytest.raises(ValueError, match="重新点击「抖音发布」"):
        reopened.confirm(prepared)
    assert publisher._load(prepared)["rows"][0]["status"] == "ready"
    _prepare(reopened)
    assert reopened.state()["batch"]["rows"][0]["status"] == "ready"


def test_preflight_cannot_tap_red_button_or_submit(session):
    publisher, fake = session
    batch_id = publisher.begin([SLOT])["id"]
    try:
        observation = _observation(publisher, batch_id)
        with pytest.raises(ValueError, match="红色按钮"):
            publisher.gesture(batch_id, SLOT, observation, "tap", *TARGET)
        with pytest.raises(ValueError, match="用户确认"):
            _submit(publisher, batch_id, observation)
        assert fake.taps == []
    finally:
        publisher.end(batch_id)


def test_confirmed_batch_rejects_changed_picker_screen(session):
    publisher, fake = session
    batch_id = _prepare(publisher)
    publisher.confirm(batch_id)
    try:
        fake.cover = "#143c55"
        with pytest.raises(ValueError, match="当前选片页"):
            publisher.resume_prepared(batch_id, SLOT, _observation(publisher, batch_id))
        assert fake.taps == []
    finally:
        publisher.end(batch_id)


def test_unknown_submission_is_recorded_before_tap_and_cannot_be_retried(session):
    publisher, fake = session
    batch_id = _prepare(publisher)
    publisher.confirm(batch_id)
    try:
        publisher.resume_prepared(batch_id, SLOT, _observation(publisher, batch_id))
        for _ in range(2):
            publisher.gesture(batch_id, SLOT, _observation(publisher, batch_id), "tap", *TARGET)
        with pytest.raises(ValueError, match="红色按钮"):
            publisher.gesture(batch_id, SLOT, _observation(publisher, batch_id), "tap", *TARGET)
        _record(publisher, batch_id, "armed")

        def lose_reply():
            stored = read_json(publisher.root / "batches" / f"{batch_id}.json")
            assert stored["rows"][0]["status"] == "submission_unknown"
            raise TimeoutError("回执丢失")

        fake.before_tap = lose_reply
        observation = _observation(publisher, batch_id)
        with pytest.raises(TimeoutError, match="回执丢失"):
            _submit(publisher, batch_id, observation)
        with pytest.raises(ValueError, match="尚未提交"):
            _submit(publisher, batch_id, observation)
        with pytest.raises(ValueError, match="红色按钮"):
            publisher.gesture(batch_id, SLOT, _observation(publisher, batch_id), "tap", *TARGET)
    finally:
        publisher.end(batch_id)

    reopened = publishing.DouyinPublish(publisher.root.parent)
    assert reopened.state()["batch"]["rows"][0]["status"] == "submission_unknown"
    fresh = reopened.begin([SLOT])["id"]
    try:
        reopened.verify_media(fresh, SLOT, MEDIA["filename"])
        with pytest.raises(ValueError, match="已有这条视频的提交记录"):
            _record(reopened, fresh, "ready")
        assert len(fake.taps) == 3
    finally:
        reopened.end(fresh)


def _publish(publisher, batch_id, result):
    publisher.resume_prepared(batch_id, SLOT, _observation(publisher, batch_id))
    for _ in range(2):
        publisher.gesture(batch_id, SLOT, _observation(publisher, batch_id), "tap", *TARGET)
    _record(publisher, batch_id, "armed")
    _submit(publisher, batch_id, _observation(publisher, batch_id))
    assert publisher.cleanup_context(batch_id, SLOT) is None
    _record(publisher, batch_id, result)


@pytest.mark.parametrize("result", ["published", "reviewing"])
def test_publish_never_deletes_and_only_verified_results_are_candidates(session, result):
    publisher, fake = session
    batch_id = _prepare(publisher)
    with pytest.raises(ValueError, match="布尔值"):
        publisher.confirm(batch_id, delete_after="true")
    with pytest.raises(ValueError, match="独立"):
        publisher.confirm(batch_id, delete_after=True)
    publisher.confirm(batch_id)
    try:
        _publish(publisher, batch_id, result)
        assert publisher.cleanup_context(batch_id, SLOT) is None
        with pytest.raises(ValueError, match="独立清理批次"):
            publisher.cleanup_open(batch_id, SLOT)
        assert len(fake.taps) == 3
    finally:
        publisher.end(batch_id)
    row = publisher.state()["batch"]["rows"][0]
    assert row["status"] == result
    assert row["cleanup_status"] == "retained"
    state = publisher.cleanup_state()
    assert state["batch"] is None
    assert bool(state["candidates"]) == (result == "published")


def _published_batch(publisher, album_name=ALBUM_NAME):
    batch_id = _prepare(publisher, album_name)
    publisher.confirm(batch_id)
    try:
        _publish(publisher, batch_id, "published")
    finally:
        publisher.end(batch_id)
    return batch_id


def _open_trash(publisher, fake, batch_id, candidate_id=None):
    assert publisher.cleanup_context(batch_id, SLOT, candidate_id)["album_filename"] == ALBUM_NAME
    publisher.cleanup_open(batch_id, SLOT)
    fake.scene = "info"
    with pytest.raises(ValueError, match="文件名与本次发布"):
        publisher.cleanup_verify(batch_id, SLOT, "IMG_9999.MOV", _observation(publisher, batch_id))
    publisher.cleanup_verify(batch_id, SLOT, Path(ALBUM_NAME).stem, _observation(publisher, batch_id))
    with pytest.raises(ValueError, match="先关闭"):
        publisher.cleanup_trash(batch_id, SLOT, _observation(publisher, batch_id), *TRASH)
    publisher.gesture(batch_id, SLOT, _observation(publisher, batch_id), "swipe_down")
    fake.scene = "fullscreen"
    publisher.cleanup_trash(batch_id, SLOT, _observation(publisher, batch_id), *TRASH)
    fake.scene = "deletion"


def test_cleanup_accepts_verified_right_side_confirmation(session):
    publisher, fake = session
    published_id = _published_batch(publisher)
    batch_id = publisher.begin_cleanup([f"{published_id}:{SLOT}"])["id"]
    try:
        _open_trash(publisher, fake, batch_id)
        fake.scene = "deletion_right"
        observation_id = _observation(publisher, batch_id)
        with pytest.raises(ValueError, match="请选择系统"):
            publisher.cleanup_delete(batch_id, SLOT, observation_id, 68.5, 80)
        response = publisher.cleanup_delete(batch_id, SLOT, observation_id, 68.5, 90.7)
        assert response["cleanup_status"] == "deletion_unknown"
    finally:
        publisher.end(batch_id)


@pytest.mark.parametrize("name_changes", [False, True])
def test_cleanup_closes_info_if_only_playback_changes(session, name_changes):
    publisher, fake = session
    published_id = _published_batch(publisher)
    batch_id = publisher.begin_cleanup([f"{published_id}:{SLOT}"])["id"]
    try:
        publisher.cleanup_open(batch_id, SLOT)
        fake.scene = "info"
        publisher.cleanup_verify(batch_id, SLOT, ALBUM_NAME, _observation(publisher, batch_id))
        observation_id = _observation(publisher, batch_id)
        tap_count = len(fake.taps)
        fake.cover = "#131313"
        if name_changes:
            fake.info_name = "IMG_0018.MOV"
            with pytest.raises(ValueError, match="信息页画面已变化"):
                publisher.gesture(batch_id, SLOT, observation_id, "tap", 50, 94)
            assert len(fake.taps) == tap_count
        else:
            publisher.gesture(batch_id, SLOT, observation_id, "tap", 50, 94)
            assert len(fake.taps) == tap_count + 1
            with pytest.raises(ValueError, match="信息页尚未关闭"):
                publisher.cleanup_trash(batch_id, SLOT, _observation(publisher, batch_id), *TRASH)
    finally:
        publisher.end(batch_id)


def test_cleanup_unknown_is_durable_and_no_duplicate_delete(session):
    publisher, fake = session
    published_id = _published_batch(publisher)
    candidate_id = f"{published_id}:{SLOT}"
    batch_id = publisher.begin_cleanup([candidate_id])["id"]
    assert publisher.state()["batch"]["id"] == published_id
    try:
        _open_trash(publisher, fake, batch_id)

        def lose_reply():
            stored = read_json(publisher.root / "batches" / f"{batch_id}.json")
            row = stored["rows"][0]
            assert row["status"] == "published"
            assert row["cleanup_status"] == "deletion_unknown"
            raise TimeoutError("删除回执丢失")

        fake.before_tap = lose_reply
        with pytest.raises(TimeoutError, match="删除回执丢失"):
            publisher.cleanup_delete(batch_id, SLOT, _observation(publisher, batch_id), *DELETE_VIDEO)
        with pytest.raises(ValueError, match="独立清理批次"):
            publisher.cleanup_delete(batch_id, SLOT, _observation(publisher, batch_id), *DELETE_VIDEO)
        checked = publisher.cleanup_record(batch_id, SLOT, "blocked", "连接超时")
        assert checked["cleanup_status"] == "deletion_unknown"
    finally:
        publisher.end(batch_id)

    reopened = publishing.DouyinPublish(publisher.root.parent)
    row = reopened.cleanup_state()["batch"]["rows"][0]
    assert row["status"] == "published" and row["cleanup_status"] == "deletion_unknown"
    assert reopened.state()["batch"]["rows"][0]["cleanup_status"] == "retained"
    assert reopened.cleanup_state()["candidates"][0]["available"] is False
    with pytest.raises(ValueError, match="结果待核对"):
        reopened.begin_cleanup([candidate_id])
    assert len(fake.taps) == 6


def test_cleanup_records_deleted_only_with_new_album_evidence(session):
    publisher, fake = session
    published_id = _published_batch(publisher)
    candidate_id = f"{published_id}:{SLOT}"
    batch_id = publisher.begin_cleanup([candidate_id])["id"]
    try:
        _open_trash(publisher, fake, batch_id)
        publisher.cleanup_delete(batch_id, SLOT, _observation(publisher, batch_id), *DELETE_VIDEO)
        with pytest.raises(ValueError, match="仍显示删除前画面"):
            publisher.cleanup_record(batch_id, SLOT, "deleted", "仍有对话框", _observation(publisher, batch_id))
        fake.scene = "album_without_original"
        publisher.cleanup_record(batch_id, SLOT, "deleted", "原片已移出图库", _observation(publisher, batch_id))
    finally:
        publisher.end(batch_id)
    row = publisher.cleanup_state()["batch"]["rows"][0]
    assert row["status"] == "published" and row["cleanup_status"] == "deleted"
    with pytest.raises(ValueError, match="已确认从相册删除"):
        publisher.begin_cleanup([candidate_id])


def test_cleanup_retains_shared_source_when_peer_did_not_publish(session):
    publisher, fake = session
    batch_id = publisher.begin([SLOT, "02"])["id"]
    publisher.verify_media(batch_id, SLOT, ALBUM_NAME)
    _record(publisher, batch_id, "ready")
    publisher.verify_media(batch_id, "02", MEDIA["filename"])
    publisher.record(batch_id, "02", "blocked", "无法发表作品")
    publisher.end(batch_id)
    publisher.confirm(batch_id)
    try:
        _publish(publisher, batch_id, "published")
        assert len(fake.taps) == 3
    finally:
        publisher.end(batch_id)
    candidate, = publisher.cleanup_state()["candidates"]
    assert candidate["id"] == f"{batch_id}:{SLOT}" and not candidate["available"]
    assert "相册可能同步" in candidate["reason"]


def test_legacy_published_uses_exact_canonical_name_and_preclick_block_can_retry(session):
    publisher, fake = session
    published_id = _published_batch(publisher)
    path = publisher.root / "batches" / f"{published_id}.json"
    legacy = read_json(path)
    del legacy["rows"][0]["album_filename"]
    save_json(path, legacy)
    candidate, = publisher.cleanup_state()["candidates"]
    assert candidate["available"] and candidate["album_filename"] == MEDIA["filename"]
    assert candidate["id"] == f"{published_id}:{SLOT}"
    batch_id = publisher.begin_cleanup([candidate["id"]])["id"]
    publisher.cleanup_open(batch_id, SLOT)
    fake.scene = "info"
    with pytest.raises(ValueError, match="文件名与本次发布"):
        publisher.cleanup_verify(batch_id, SLOT, ALBUM_NAME, _observation(publisher, batch_id))
    publisher.cleanup_record(batch_id, SLOT, "blocked", "相册显示名称无法核对")
    publisher.end(batch_id)

    fresh = publisher.begin_cleanup([candidate["id"]])["id"]
    try:
        publisher.cleanup_open(fresh, SLOT)
        checked = publisher.cleanup_verify(fresh, SLOT, Path(MEDIA["filename"]).stem, _observation(publisher, fresh))
        assert checked["cleanup_status"] == "matched"
        assert len(fake.taps) == 3
    finally:
        publisher.end(fresh)


def _seed_other_publication(publisher, status, *, digest=MEDIA["media_sha256"], source=None):
    batch_id = "f" * 32
    save_json(publisher.root / "batches" / f"{batch_id}.json", {
        "id": batch_id, "mode": "publish", "status": "finished", "created": 1,
        "rows": [{
            "slot": "01", "uid": "stable-01", "filename": "IMG_3001.MOV", "status": status,
            "account": "原抖音号", "submitted": 2, "updated": 3,
            "media_sha256": digest, "source_sha256": source,
        }],
    })


@pytest.mark.parametrize("status", ["published", "reviewing", "submission_unknown"])
def test_preflight_blocks_same_video_across_phone_and_account(session, status):
    publisher, fake = session
    _seed_other_publication(publisher, status)
    batch_id = publisher.begin(["02"])["id"]
    try:
        publisher.verify_media(batch_id, "02", MEDIA["filename"])
        screen = publisher.screen(batch_id, "02")
        with pytest.raises(ValueError, match="01 号手机.*原抖音号"):
            publisher.record(batch_id, "02", "ready", "核对通过", "新抖音号", VIDEO,
                             MEDIA["media_sha256"], screen["observation_id"])
        assert fake.taps == []
        row = publisher.state()["batch"]["rows"][0]
        assert row["status"] == "blocked"
        assert row["account"] == "新抖音号"
        assert row["duplicate_reason"] == ("published" if status == "published" else "pending")
    finally:
        publisher.end(batch_id)


def test_same_batch_duplicate_has_only_one_ready_destination(session):
    publisher, fake = session
    batch_id = publisher.begin(["01", "02"])["id"]
    try:
        publisher.verify_media(batch_id, "01", MEDIA["filename"])
        _record(publisher, batch_id, "ready")
        publisher.verify_media(batch_id, "02", MEDIA["filename"])
        screen = publisher.screen(batch_id, "02")
        with pytest.raises(ValueError, match="已安排给本批次 01 号"):
            publisher.record(batch_id, "02", "ready", "核对通过", "另一抖音号", VIDEO,
                             MEDIA["media_sha256"], screen["observation_id"])
    finally:
        publisher.end(batch_id)

    try:
        confirmed = publisher.confirm(batch_id)
        assert confirmed["slots"] == ["01"]
        assert [(row["slot"], row["status"]) for row in confirmed["rows"]] == [
            ("01", "ready"), ("02", "blocked"),
        ]
        assert fake.taps == []
    finally:
        publisher.end(batch_id)


def test_confirm_rechecks_legacy_reexport_lineage(session):
    publisher, fake = session
    original, source = "12" * 32, "34" * 32
    root = publisher.root.parent / "postprocessing"
    publisher.distribution.postprocess_root = root
    save_json(root / "batches" / "history" / "manifest.json", {
        "jobs": [
            {"outputSha256": original, "sourceSha256": source},
            {"outputSha256": MEDIA["media_sha256"], "sourceSha256": source},
        ],
    })
    batch_id = _prepare(publisher)
    # The old record didn't persist source lineage. The existing manifest supplies it.
    _seed_other_publication(publisher, "published", digest=original)
    with pytest.raises(ValueError, match="没有通过预检查"):
        publisher.confirm(batch_id)
    row = publisher.state()["batch"]["rows"][0]
    assert row["status"] == "blocked" and row["duplicate_reason"] == "published"
    assert "原抖音号" in row["detail"]
    assert fake.taps == []


def test_submit_rechecks_global_receipts_before_final_tap(session):
    publisher, fake = session
    batch_id = _prepare(publisher)
    publisher.confirm(batch_id)
    try:
        publisher.resume_prepared(batch_id, SLOT, _observation(publisher, batch_id))
        for _ in range(2):
            publisher.gesture(batch_id, SLOT, _observation(publisher, batch_id), "tap", *TARGET)
        _record(publisher, batch_id, "armed")
        _seed_other_publication(publisher, "submission_unknown")
        with pytest.raises(ValueError, match="待核对"):
            _submit(publisher, batch_id, _observation(publisher, batch_id))
        assert publisher.state()["batch"]["rows"][0]["status"] == "blocked"
        assert len(fake.taps) == 2
    finally:
        publisher.end(batch_id)


def _auto_cleanup_fixture(publisher, monkeypatch):
    assets = {
        ("stable-01", MEDIA["filename"]): MEDIA["media_sha256"],
        ("stable-01", "IMG_0034.MOV"): "34" * 32,
        ("stable-02", "IMG_0056.MOV"): "56" * 32,
        ("stable-02", "IMG_0078.MOV"): "78" * 32,
    }
    names = [ALBUM_NAME, "IMG_0034.MOV", "IMG_0056.MOV", "IMG_0078.MOV"]
    for index, ((uid, filename), digest) in enumerate(assets.items()):
        batch_id = f"{index + 1:032x}"
        save_json(publisher.root / "batches" / f"{batch_id}.json", {
            "id": batch_id, "mode": "publish", "status": "finished", "created": 100 - index,
            "rows": [{
                "slot": uid[-2:], "uid": uid, "filename": filename, "album_filename": names[index],
                "status": "reviewing" if index == 3 else "published", "media_sha256": digest,
                "account": "测试账号", "updated": 200 - index,
            }],
        })

    def video(uid, filename):
        return {"media_sha256": assets[(uid, filename)], "filename": filename, "size": 100}

    monkeypatch.setattr(publishing, "_read_video", video)


def test_auto_cleanup_queues_only_verified_publications_and_switches_exact_row(session, monkeypatch):
    publisher, fake = session
    _auto_cleanup_fixture(publisher, monkeypatch)
    assert publisher.cleanup_state()["eligible_count"] == 3
    batch = publisher.begin_auto_cleanup()
    batch_id = batch["id"]
    first, second, other = batch["rows"]
    assert batch["slots"] == ["01", "02"]
    assert [(row["slot"], row["filename"]) for row in batch["rows"]] == [
        ("01", MEDIA["filename"]), ("01", "IMG_0034.MOV"), ("02", "IMG_0056.MOV"),
    ]
    try:
        with pytest.raises(ValueError, match="本台手机"):
            publisher.cleanup_context(batch_id, "01", other["candidate_id"])
        with pytest.raises(ValueError, match="不在本批次"):
            publisher.cleanup_open(batch_id, "01")
        publisher.cleanup_context(batch_id, "01", first["candidate_id"])
        publisher.cleanup_open(batch_id, "01")
        fake.scene = "info"
        previous_view = _observation(publisher, batch_id)

        next_source = publisher.cleanup_context(batch_id, "01", second["candidate_id"])
        assert next_source["filename"] == "IMG_0034.MOV"
        publisher.cleanup_open(batch_id, "01")
        with pytest.raises(ValueError, match="过期"):
            publisher.cleanup_verify(batch_id, "01", "IMG_0034.MOV", previous_view)
        with pytest.raises(ValueError, match="文件名"):
            publisher.cleanup_verify(batch_id, "01", ALBUM_NAME, _observation(publisher, batch_id))
        publisher.cleanup_verify(batch_id, "01", "IMG_0034", _observation(publisher, batch_id))
        assert fake.taps == []
    finally:
        publisher.end(batch_id)


def test_auto_cleanup_uncertain_delete_blocks_only_later_rows_on_same_phone(session, monkeypatch):
    publisher, fake = session
    _auto_cleanup_fixture(publisher, monkeypatch)
    batch = publisher.begin_auto_cleanup()
    batch_id = batch["id"]
    first, second, other = batch["rows"]
    try:
        _open_trash(publisher, fake, batch_id, first["candidate_id"])

        def lost_delete():
            raise TimeoutError("删除回执超时")

        fake.before_tap = lost_delete
        with pytest.raises(TimeoutError, match="回执超时"):
            publisher.cleanup_delete(batch_id, "01", _observation(publisher, batch_id), *DELETE_VIDEO)
        assert publisher.cleanup_context(batch_id, "01", second["candidate_id"]) is None
        context = publisher.cleanup_context(batch_id, "02", other["candidate_id"])
        assert context["filename"] == "IMG_0056.MOV"
        rows = publisher.cleanup_state()["batch"]["rows"]
        assert [row["cleanup_status"] for row in rows] == ["deletion_unknown", "blocked", "waiting"]
        assert "待核对" in rows[1]["cleanup_detail"]
    finally:
        publisher.end(batch_id)
