"""Bound iPhone control and durable records for two-stage Douyin publishing."""

from __future__ import annotations

import base64
import hashlib
import io
import math
import re
import threading
import time
import uuid
from contextlib import ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageChops, ImageFilter

from .phone_distribution import PhoneDistribution, devices, request
from .project_run import AnalysisBusy, analysis_lock, analysis_running
from .projects import file_digest
from .providers import ProviderError, run
from .publication_history import publication_message
from .settings import desktop_directory, settings
from .workbench import read_json, save_json


APPS = {"photos": "com.apple.mobileslideshow", "douyin": "com.ss.iphone.ugc.Aweme"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
CAPTURE_SUFFIXES = {".png", ".jpg", ".jpeg"}
FRESH_SECONDS = 90
CLEANUP_ACTIVE = {"waiting", "checking", "matched", "confirming", "deletion_unknown"}
_IMPORT_NAME = re.compile(r"tempVideo_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?:\.(?:mp4|mov|m4v))?", re.I)


class _UnknownVideoName(ValueError):
    pass


def _matches_screen(prepared, current, regions=("cover", "settings", "actions")):
    """Ignore the clock; compare the cover, posting settings and actions."""
    if not prepared or prepared["size"] != current["size"]:
        return False
    for name in regions:
        if name not in prepared or name not in current:
            return False
        before = base64.b64decode(prepared[name])
        after = base64.b64decode(current[name])
        if sum(abs(a - b) for a, b in zip(before, after)) > len(before) * 5:
            return False
    return True


def _screen_regions(image):
    width, height = image.size
    regions = {"size": [width, height]}
    for name, box in {
        "cover": (.15, .075, .84, .29),
        "settings": (.025, .30, .975, .88),
        "actions": (.48, .89, .98, .965),
        "picker_tile": (.005, .20, .33, .405),
        "picker_selection": (.005, .785, .30, .90),
    }.items():
        left, top, right, bottom = box
        crop = image.crop((int(left * width), int(top * height), int(right * width), int(bottom * height)))
        regions[name] = base64.b64encode(crop.resize((28, 16)).convert("L").tobytes()).decode()
    return regions


_PHOTO_INFO_SIZE = (192, 96)


def _photo_info_pixels(image):
    width, height = image.size
    panel = image.crop((int(width * .025), int(height * .50), int(width * .975), int(height * .86)))
    return panel.convert("L").resize(_PHOTO_INFO_SIZE).tobytes()


def _matches_photo_info(prepared, image):
    if prepared["size"] != list(image.size):
        return False
    if not prepared.get("photo_info"):
        return _matches_screen(prepared, _screen_regions(image))
    # Keep the complete filename area in the comparison, excluding the playing preview above it.
    before = Image.frombytes("L", _PHOTO_INFO_SIZE, base64.b64decode(prepared["photo_info"]))
    current = Image.frombytes("L", _PHOTO_INFO_SIZE, _photo_info_pixels(image))
    delta = ImageChops.difference(before, current).filter(ImageFilter.BoxBlur(3))
    return delta.getextrema()[1] <= 4


def _red_action_at(image, x, y):
    if x < 52 or y < 85:
        return False
    return _red_fraction(image, x, y) >= .24


def _red_fraction(image, x, y):
    width, height = image.size
    cx, cy = x * width / 100, y * height / 100
    dx, dy = width * .035, height * .012
    crop = image.crop((max(0, int(cx - dx)), max(0, int(cy - dy)),
                       min(width, int(cx + dx)), min(height, int(cy + dy))))
    pixels = crop.tobytes()
    red = sum(r > 135 and r > g * 1.25 and r > b * 1.15
              for r, g, b in zip(pixels[::3], pixels[1::3], pixels[2::3]))
    return red / (len(pixels) // 3) if pixels else 0


def _album_name(filename):
    if not isinstance(filename, str) or not filename.isprintable() or any(c in filename for c in "/\\:"):
        raise ValueError("请使用照片详情中显示的原始视频名称")
    name = Path(filename.strip())
    return (name.stem if name.suffix.lower() in VIDEO_SUFFIXES else name.name).casefold()


def _media_identity(row):
    uid, filename, digest = (row.get(key) for key in ("uid", "filename", "media_sha256"))
    if (not isinstance(uid, str) or not uid or not isinstance(filename, str)
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)
            or Path(filename).suffix.lower() not in VIDEO_SUFFIXES):
        return None
    try:
        if not _album_name(filename):
            return None
    except ValueError:
        return None
    return uid, filename.casefold(), digest.lower()


def _video_entries(afc):
    for folder in afc.listdir("/DCIM"):
        if not re.fullmatch(r"[0-9]{3}APPLE", folder, flags=re.I):
            continue
        directory = f"/DCIM/{folder}"
        for entry in afc.listdir_info(directory):
            if Path(entry.st_name).suffix.lower() in VIDEO_SUFFIXES and entry.st_size > 0:
                yield f"{directory}/{entry.st_name}", entry


def _digest_video(afc, remote, size):
    digest = hashlib.sha256()
    read_size = 0
    for chunk in afc.iter_content(remote):
        if not chunk:
            raise OSError("读取手机视频中断")
        read_size += len(chunk)
        digest.update(chunk)
    if read_size != size:
        raise OSError("手机视频未完整回读")
    return {"filename": remote.rsplit("/", 1)[-1], "size": size, "media_sha256": digest.hexdigest()}


def _read_video(uid, filename):
    import tidevice

    if not filename or not filename.isprintable() or any(c in filename for c in "/\\:"):
        raise ValueError("请填写系统相册信息页显示的单个视频文件名")
    wanted = filename.strip().casefold()
    with closing(tidevice.Device(uid).sync) as afc:
        afc.get_socket().settimeout(12)
        matches = [(remote, entry.st_size) for remote, entry in _video_entries(afc)
                   if wanted in (entry.st_name.casefold(), Path(entry.st_name).stem.casefold())]
        if not matches:
            raise _UnknownVideoName("USB 相册中找不到这个文件名，请回到系统相册核对")
        if len(matches) != 1:
            raise ValueError("USB 相册中有多个同名文件，请回到系统相册核对")
        return _digest_video(afc, *matches[0])


class DouyinPublish:
    def __init__(self, workspace, *, postprocess_root=None):
        self.distribution = PhoneDistribution(workspace, postprocess_root=postprocess_root)
        self.root = Path(workspace) / "douyin-publish"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lease = None
        self._active_id = None
        self._observations = {}
        self._media_candidates = {}
        self._cleanup_current = {}
        self._mutex = threading.RLock()

    def _path(self, batch_id):
        if not isinstance(batch_id, str) or not re.fullmatch(r"[0-9a-f]{32}", batch_id):
            raise ValueError("发布批次编号无效")
        return self.root / "batches" / (batch_id + ".json")

    def _batches(self):
        return [read_json(path) for path in (self.root / "batches").glob("*.json")]

    def _latest(self, *, cleanup=False):
        return max((batch for batch in self._batches() if (batch["mode"] == "cleanup") == cleanup),
                   key=lambda batch: batch["created"], default=None)

    def _load(self, batch_id):
        path = self._path(batch_id)
        if not path.is_file():
            raise ValueError("找不到这个抖音发布批次")
        return read_json(path)

    def _save(self, batch):
        save_json(self._path(batch["id"]), batch)

    def _public(self, batch):
        if batch is None:
            return None
        return {**batch, "rows": [
            {k: v for k, v in row.items() if k not in {
                "uid", "prepared_screen", "armed_screen", "cleanup_target", "cleanup_info_screen",
                "cleanup_dialog_screen", "cleanup_fullscreen",
            }}
            for row in batch["rows"]
        ]}

    def _visible_batch(self, *, cleanup=False):
        batch = self._latest(cleanup=cleanup)
        if batch and batch["status"] == "running" and not analysis_running(self.root):
            batch = self._load(batch["id"])
            if batch["status"] == "running":
                self._finish(batch, "工作台上次运行中断，请核对手机上的结果", status="interrupted")
        if not cleanup:
            self._invalidate_after_cleanup(batch)
        return self._public(batch)

    def _invalidate_after_cleanup(self, batch):
        if not batch or batch["mode"] != "prepare" or batch["status"] != "finished":
            return
        ready = {(row["slot"], row["uid"]): row for row in batch["rows"] if row["status"] == "ready"}
        if not ready:
            return
        for cleanup in self._batches():
            if cleanup["mode"] != "cleanup" or cleanup["created"] < batch["created"]:
                continue
            for cleaned in cleanup["rows"]:
                row = ready.get((cleaned["slot"], cleaned.get("uid")))
                if not row or row["status"] != "ready":
                    continue
                # Older cleanup records did not separately record the time Photos was opened.
                legacy_opened = cleanup["created"] if cleaned.get("cleanup_status") != "waiting" else None
                opened = cleaned.get("cleanup_opened_at", legacy_opened)
                if opened and opened > (row.get("updated") or batch["created"]):
                    row.update(status="stale", detail="发布预检后这台手机执行了相册清理，请重新点击「抖音发布」检查选片。")

    def _device_state(self):
        try:
            slots = self.distribution.bindings()
        except (OSError, ValueError) as exc:
            return {}, {}, f"无法读取效卫手机绑定。{exc}"
        try:
            online = devices()
            error = ""
        except Exception:
            online = {}
            error = "无法连接效卫，请检查苹果投屏客户端和 API"
        return slots, online, error

    @staticmethod
    def _phones(slots, online):
        return [{"slot": slot, "suffix": uid[-6:], "online": uid in online} for slot, uid in sorted(slots.items())]

    def state(self):
        slots, online, error = self._device_state()
        return {"phones": self._phones(slots, online), "batch": self._visible_batch(),
                "busy": analysis_running(self.root), "error": error}

    def cleanup_state(self):
        slots, online, error = self._device_state()
        candidates = [candidate for candidate, _ in self._cleanup_candidates(slots, online).values()]
        latest = self._visible_batch(cleanup=True)
        previous = sorted((batch for batch in self._batches() if batch["mode"] == "cleanup"
                           and (latest is None or batch["id"] != latest["id"])),
                          key=lambda batch: batch["created"], reverse=True)[:4]
        recent = [latest, *(self._public(batch) for batch in previous)] if latest else []
        return {"phones": self._phones(slots, online), "batch": latest, "recent_batches": recent,
                "candidates": candidates, "eligible_count": sum(candidate["available"] for candidate in candidates),
                "busy": analysis_running(self.root), "error": error}

    def _acquire(self):
        if self._lease is not None:
            raise ValueError("已有抖音批次正在运行")
        with ExitStack() as stack:
            try:
                stack.enter_context(analysis_lock(self.root))
            except AnalysisBusy as exc:
                raise ValueError("已有抖音批次正在运行") from exc
            stack.enter_context(self.distribution.writer())
            self._lease = stack.pop_all()

    def _release(self):
        lease, self._lease = self._lease, None
        self._active_id = None
        self._observations.clear()
        self._media_candidates.clear()
        self._cleanup_current.clear()
        if lease is not None:
            lease.close()

    def _active_batch(self, batch_id):
        if batch_id != self._active_id or self._lease is None:
            raise ValueError("这个批次当前未运行")
        return self._load(batch_id)

    def _active(self, batch_id, slot):
        batch = self._active_batch(batch_id)
        automatic_cleanup = batch["mode"] == "cleanup" and batch.get("automatic")
        current = self._cleanup_current.get(slot)
        row = next((row for row in batch["rows"] if row["slot"] == slot
                    and (not automatic_cleanup or row.get("candidate_id") == current)), None)
        if row is None:
            raise ValueError("这台手机不在本批次")
        return batch, row

    def _recover_abandoned(self):
        for batch in self._batches():
            if batch["status"] == "running":
                self._finish(batch, "上次运行中断，请核对手机上的结果", status="interrupted")
                self._save(batch)

    def begin(self, slots, mode="prepare"):
        if mode != "prepare":
            raise ValueError("发布前请先运行预检查")
        bound = self.distribution.bindings()
        if (not isinstance(slots, list) or not slots or
                not all(isinstance(slot, str) and slot in bound for slot in slots)):
            raise ValueError("请选择本机已绑定的 iPhone")
        with self._mutex:
            self._acquire()
            try:
                self._recover_abandoned()
                rows = [{"slot": slot, "uid": bound[slot], "status": "running", "detail": "待检查",
                         "account": "", "video": ""} for slot in sorted(set(slots))]
                try:
                    online = devices()
                except Exception:
                    online = {}
                    unavailable = "无法连接效卫，请检查苹果投屏和 API"
                else:
                    unavailable = "手机离线，请重新连接并解锁"
                for row in rows:
                    if row["uid"] not in online:
                        row.update(status="blocked", detail=unavailable, updated=time.time())
                receipt = self._latest_distribution()
                self._set_distribution_targets(rows, receipt)
                batch = {"id": uuid.uuid4().hex, "mode": mode, "status": "running", "created": time.time(),
                         "distribution_batch_id": receipt["id"] if receipt else None,
                         "slots": [row["slot"] for row in rows], "rows": rows}
                self._save(batch)
                self._active_id = batch["id"]
                return self._public(batch)
            except BaseException:
                self._release()
                raise

    def _latest_distribution(self):
        return next((batch for batch in self.distribution.batches() if batch["action"] == "分发"), None)

    def _set_distribution_targets(self, rows, receipt):
        if receipt is None:
            return
        jobs = self.distribution.jobs()
        history = self.distribution._history()
        for row in rows:
            if row["status"] != "running":
                continue
            assignments = [item for item in receipt["rows"] if item["slot"] == row["slot"]]
            if len(assignments) != 1:
                detail = ("最近一次分发未给这台手机指定成片" if not assignments else
                          "最近一次分发为这台手机选了多条成片，请单独分发要发布的那条")
                row.update(status="blocked", detail=detail, updated=time.time())
                continue
            job_id = assignments[0]["job_id"]
            job = jobs.get(job_id)
            if not job or job["uid"] != row["uid"] or job["phase"] != "done" or not job.get("remote"):
                row.update(status="blocked", detail="最近分发的成片尚未在这台手机核验成功", updated=time.time())
                continue
            source, filename, digest = job["file"], Path(job["remote"]).name, job["sha256"]
            row.update(source=source, video=filename, target={"job_id": job_id, "source": source,
                       "filename": filename, "media_sha256": digest, "source_sha256": job.get("source_sha256")})
            previous = history.find(digest, job.get("source_sha256"))
            if previous:
                row.update(status="already_published" if previous["status"] == "published" else "blocked",
                           detail=publication_message(previous), updated=time.time())
            else:
                row["detail"] = "正在核对最近分发的成片与手机相册"

    def skip_duplicate_target(self, batch_id, slot):
        with self._mutex:
            batch, row = self._active(batch_id, slot)
            target = row.get("target")
            if batch["mode"] != "prepare" or row["status"] != "running" or not target:
                return False
            history = self.distribution._history()
            keys = history.identity(target["media_sha256"], target.get("source_sha256"))
            for other in batch["rows"]:
                if other["status"] == "ready" and keys & history.identity(other.get("media_sha256"), other.get("source_sha256")):
                    detail = f'同一成片已列入 {other["slot"]} 号手机（{other["account"]}）的发布确认清单，只能发布一次'
                    self._block_duplicate(row, ("same_batch", detail))
                    self._save(batch)
                    return True
            return False

    def confirm(self, batch_id, delete_after=False):
        if type(delete_after) is not bool:
            raise ValueError("删除原片选项必须是布尔值")
        if delete_after:
            raise ValueError("发布会保留手机原片，请通过独立的相册清理技能选择要删除的已发布视频")
        with self._mutex:
            self._acquire()
            try:
                self._recover_abandoned()
                batch = self._latest()
                if not batch or batch["id"] != batch_id or batch["mode"] != "prepare" or batch["status"] != "finished":
                    raise ValueError("请确认最新一批已完成的预检查结果")
                receipt = self._latest_distribution()
                if "distribution_batch_id" in batch and batch["distribution_batch_id"] != (receipt["id"] if receipt else None):
                    raise ValueError("分发清单已更新，请重新检查待发成片")
                self._invalidate_after_cleanup(batch)
                if not any(row["status"] == "ready" for row in batch["rows"]) and any(
                        row["status"] == "stale" for row in batch["rows"]):
                    raise ValueError("手机在预检后执行了相册清理，请重新点击「抖音发布」检查选片")
                history = self.distribution._history()
                ready = []
                for row in batch["rows"]:
                    if row["status"] != "ready":
                        continue
                    duplicate = self._duplicate(history, row, ready)
                    if duplicate:
                        self._block_duplicate(row, duplicate)
                    else:
                        ready.append(row)
                        row.update(cleanup_status="retained", cleanup_detail="保留手机相册原片")
                if not ready:
                    self._save(batch)
                    raise ValueError("本批次没有通过预检查的手机")
                batch.update(mode="publish", status="running", slots=[row["slot"] for row in ready], confirmed_at=time.time(),
                             delete_after_publish=False)
                self._save(batch)
                self._active_id = batch_id
                return self._public(batch)
            except BaseException:
                self._release()
                raise

    def _finish(self, batch, message, *, status="finished"):
        for row in batch["rows"]:
            if (row["status"] in {"running", "preparing_publish", "armed"}
                    or batch["mode"] == "publish" and row["status"] == "ready"):
                row.update(status="blocked", detail=message or "流程结束前未完成核对")
            cleanup = row.get("cleanup_status")
            if cleanup in CLEANUP_ACTIVE and cleanup != "deletion_unknown":
                if batch["mode"] == "cleanup":
                    row.update(cleanup_status="blocked", cleanup_detail=message or "原片删除未完成，请在相册核对")
                else:
                    row.update(cleanup_status="retained", cleanup_detail="已保留手机相册原片")
        batch.update(status=status, ended=time.time())

    def end(self, batch_id, message=""):
        with self._mutex:
            if batch_id != self._active_id:
                return
            try:
                batch = self._load(batch_id)
                self._finish(batch, message)
                self._save(batch)
            finally:
                self._release()

    def _target(self, row):
        uid = row["uid"]
        if self.distribution.bindings().get(row["slot"]) != uid:
            raise ValueError("这台手机的绑定已变化，请重新检查")
        target = devices().get(uid)
        if not target:
            raise ValueError("手机离线，请重新连接并解锁")
        return (target["serial"], target.get("connectTime"))

    def _capture(self, serial):
        # Inherit the workspace ACL. The separately running Xiaowei process
        # writes the screenshot and the workbench must be able to reopen it.
        directory = self.root / ("capture-" + uuid.uuid4().hex[:12])
        directory.mkdir()
        try:
            request("screenFile", devices=serial, data={"savePath": str(directory)})
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                for path in directory.iterdir():
                    if path.suffix.lower() not in CAPTURE_SUFFIXES or not path.is_file():
                        continue
                    try:
                        with Image.open(path) as source:
                            image = source.convert("RGB")
                        image.thumbnail((640, 1160))
                        return image
                    except OSError:
                        continue
                time.sleep(.15)
            raise ValueError("效卫未返回可读取的手机画面，请检查投屏和解锁状态")
        finally:
            try:
                for path in directory.iterdir():
                    if path.suffix.lower() in CAPTURE_SUFFIXES and path.is_file() and not path.is_symlink():
                        path.unlink()
                directory.rmdir()
            except OSError:
                pass

    def screen(self, batch_id, slot):
        with self._mutex:
            batch, row = self._active(batch_id, slot)
            if (row["status"] not in {"running", "ready", "preparing_publish", "armed", "submission_unknown"}
                    and not self._cleanup_allowed(batch, row)):
                raise ValueError("该设备本阶段已结束")
            self._observations.pop(slot, None)
            target = self._target(row)
            image = self._capture(target[0])
            identifier = uuid.uuid4().hex
            self._observations[slot] = {"id": identifier, "time": time.monotonic(), "target": target,
                                        "screen": _screen_regions(image), "image": image}
            out = io.BytesIO()
            image.save(out, "JPEG", quality=88)
            return {"observation_id": identifier, "images": [{
                "image_url": "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode(),
                "width": image.width, "height": image.height,
            }]}

    def open(self, batch_id, slot, app):
        with self._mutex:
            batch, row = self._active(batch_id, slot)
            if app not in APPS:
                raise ValueError("只能通过发布技能打开系统相册或抖音")
            if batch["mode"] != "prepare" or row["status"] != "running":
                raise ValueError("仅预检查可以主动打开应用")
            target = self._target(row)
            self._observations.pop(slot, None)
            request("startApp", devices=target[0], data={"app": APPS[app]})
            row["detail"] = "正在核对系统相册中的视频" if app == "photos" else "正在核对抖音账号和选片"
            self._save(batch)
            return {"opened": app}

    @staticmethod
    def _coordinates(x, y):
        try:
            x, y = float(x), float(y)
        except (ValueError, TypeError):
            raise ValueError("触控坐标需为 0–100 的百分比") from None
        if not all(math.isfinite(v) and 0 <= v <= 100 for v in (x, y)):
            raise ValueError("触控坐标需为 0–100 的百分比")
        return x, y

    def _observation(self, slot, observation_id, target):
        observation = self._observations.get(slot)
        if (not observation or observation["id"] != observation_id or observation["target"] != target
                or time.monotonic() - observation["time"] > FRESH_SECONDS):
            raise ValueError("画面已过期或设备连接变化，请先重新截图")
        return observation

    def gesture(self, batch_id, slot, observation_id, kind, x=None, y=None):
        with self._mutex:
            batch, row = self._active(batch_id, slot)
            cleanup = row.get("cleanup_status") if self._cleanup_allowed(batch, row) else None
            if not (batch["mode"] == "prepare" and row["status"] == "running" or
                    batch["mode"] == "publish" and row["status"] in {"preparing_publish", "submission_unknown"} or
                    cleanup in {"checking", "matched", "deletion_unknown"}):
                raise ValueError("请先核对已检查的选片页，或使用专用发布工具")
            target = self._target(row)
            observation = self._observation(slot, observation_id, target)
            if kind == "tap":
                x, y = self._coordinates(x, y)
                if cleanup == "checking":
                    if x >= 68 and (y <= 16 or y >= 80):
                        raise ValueError("清理只处理单个核对过的原片，不能进入多选或直接点击垃圾桶")
                    if 38 <= x <= 62 and 65 <= y <= 95:
                        latest = self._capture(target[0])
                        if (_red_fraction(latest, x, y) >= .02
                                or _red_fraction(observation["image"], x, y) >= .02):
                            raise ValueError("不得通过普通触控确认删除，请先核对单条原片")
                if cleanup == "matched" and not (40 <= x <= 60 and 85 <= y <= 98):
                    raise ValueError("原片已锁定，只可关闭当前视频的信息页")
                if cleanup == "deletion_unknown":
                    if 0 <= x <= 12 and 6 <= y <= 14:
                        action = "cleanup_gallery_returned"
                        regions = ("cover", "actions")
                    elif 39 <= x <= 61 and 44 <= y <= 68:
                        action = "cleanup_notice_dismissed"
                        regions = ("cover", "settings", "actions")
                    else:
                        raise ValueError("删除确认已发出，请在系统说明或返回图库后核验")
                    if row.get(action) or tuple(row["cleanup_target"]) != target:
                        raise ValueError("请核对已显示的删除结果，切勿重复操作")
                    latest = self._capture(target[0])
                    latest_screen = _screen_regions(latest)
                    if (_red_fraction(latest, x, y) >= .02
                            or _matches_screen(row["cleanup_dialog_screen"], latest_screen)
                            or not _matches_screen(observation["screen"], latest_screen, regions)):
                        raise ValueError("请核对系统说明或返回图库按钮后重试查看")
                    row[action] = True
                    self._save(batch)
                if x >= 52 and y >= 85:
                    latest = self._capture(target[0])
                    red = _red_action_at(latest, x, y)
                    if (red != _red_action_at(observation["image"], x, y)
                            or red and not _matches_screen(observation["screen"], _screen_regions(latest), ("actions",))):
                        self._observations.pop(slot, None)
                        raise ValueError("屏幕已变化，请重新截图后操作")
                    if red:
                        if row["status"] != "preparing_publish" or row["next_taps"] >= 2:
                            raise ValueError("右下角红色按钮不能用普通触控提交，请停在当前页面")
                        row["next_taps"] += 1
                        self._save(batch)
                data = {"type": "10", "x": str(x), "y": str(y)}
            elif kind == "swipe_up":
                data = {"type": "6"}
            elif kind == "swipe_down":
                data = {"type": "7"}
            else:
                raise ValueError("不支持此触控动作")
            if cleanup == "matched":
                if (row.get("cleanup_info_dismissed") or kind not in {"tap", "swipe_down"}
                        or tuple(row["cleanup_target"]) != target
                        or not _matches_photo_info(row["cleanup_info_screen"], observation["image"])):
                    raise ValueError("只可从已核对的信息页关闭一次说明，随后核对垃圾桶")
                latest = self._capture(target[0])
                if not _matches_photo_info(row["cleanup_info_screen"], latest):
                    raise ValueError("信息页画面已变化，请保留原片")
                row["cleanup_info_dismissed"] = True
                self._save(batch)
            elif cleanup == "deletion_unknown" and kind != "tap":
                raise ValueError("删除确认已发出，请查看结果，切勿重复操作")
            self._observations.pop(slot)
            request("pointerEvent", devices=target[0], data=data)
            return {"processed": kind}

    def resume_prepared(self, batch_id, slot, observation_id):
        with self._mutex:
            batch, row = self._active(batch_id, slot)
            if batch["mode"] != "publish" or row["status"] != "ready":
                raise ValueError("仅可继续经过用户确认的预检查结果")
            observation = self._observation(slot, observation_id, self._target(row))
            if not _matches_screen(row["prepared_screen"], observation["screen"],
                                   ("cover", "settings", "actions", "picker_tile", "picker_selection")):
                raise ValueError("当前选片页与已核对的截图不同，请重新检查")
            self._observations.pop(slot)
            row.update(status="preparing_publish", detail="已确认原选片，正在准备发布", next_taps=0)
            self._save(batch)
            return {"status": row["status"]}

    def _source(self, file):
        digest = file["media_sha256"]
        names = [job.get("file", "") for job in self.distribution.jobs().values() if job.get("sha256") == digest]
        generated_name = next((name for name in names if self._is_generated(name)), "")
        if generated_name:
            return {"generated": True, "source": generated_name}
        for path in (desktop_directory() / "最终视频").rglob("*.mp4"):
            try:
                if self._is_generated(path.name) and path.stat().st_size == file["size"] and file_digest(path) == digest:
                    return {"generated": True, "source": path.name}
            except OSError:
                continue
        return {"generated": False, "source": next(iter(names), "")}

    @staticmethod
    def _is_generated(filename):
        return bool(re.match(r"^第\d+组_(?:优云智算H3|即梦)_", filename or "", re.I))

    def _video_sources(self):
        local_paths = [Path(job["path"]) for job in self.distribution.jobs().values() if job.get("path")]
        local_paths.extend(path for path in (desktop_directory() / "最终视频").rglob("*.mp4")
                           if self._is_generated(path.name))
        by_size = {}
        for path in dict.fromkeys(local_paths):
            try:
                if path.suffix.lower() in VIDEO_SUFFIXES and path.is_file():
                    by_size.setdefault(path.stat().st_size, []).append(path)
            except OSError:
                continue
        return by_size

    def _media_previews(self, path, filename, config):
        ffprobe, ffmpeg = config.get("ffprobe"), config.get("ffmpeg")
        if not ffprobe or not ffmpeg:
            raise ValueError("缺少 FFmpeg 或 FFprobe，无法核对导入视频的时长和画面")
        output = run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", str(path)], timeout=10)
        if output.returncode:
            raise ValueError("无法读取匹配成片的时长")
        duration = float(output.stdout.strip())
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("视频时长无效")
        images = []
        with TemporaryDirectory(prefix="video-", dir=self.root) as directory:
            for seconds in (0, round(min(4, duration / 4), 2)):
                frame = Path(directory) / f"frame-{seconds}.jpg"
                result = run([ffmpeg, "-v", "error", "-nostdin", "-ss", str(seconds), "-i", str(path),
                              "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=320:-2",
                              "-q:v", "3", "-update", "1", str(frame)], timeout=12)
                if result.returncode or not frame.is_file():
                    continue
                with Image.open(frame) as image:
                    images.append({"filename": filename, "seconds": seconds, "width": image.width,
                                   "height": image.height, "image_url": "data:image/jpeg;base64,"
                                   + base64.b64encode(frame.read_bytes()).decode()})
        return round(duration, 3), images

    def _candidate_matches(self, uid):
        import tidevice

        sources = self._video_sources()
        if not sources:
            raise ValueError("未找到本地成片，无法用 USB 文件哈希核对导入素材")
        config = settings()
        matched = []
        seen = set()
        local_digests = {}
        with closing(tidevice.Device(uid).sync) as afc:
            afc.get_socket().settimeout(12)
            recent = [(remote, entry) for remote, entry in _video_entries(afc) if entry.st_size in sources]
            recent.sort(key=lambda candidate: candidate[1].st_mtime, reverse=True)
            for remote, entry in recent[:6]:
                file = _digest_video(afc, remote, entry.st_size)
                digest = file["media_sha256"]
                if digest in seen:
                    continue
                seen.add(digest)
                for path in sources[entry.st_size]:
                    try:
                        if path not in local_digests:
                            local_digests[path] = file_digest(path)
                        if local_digests[path] == digest:
                            matched.append((file, path))
                            break
                    except OSError:
                        continue
        candidates, images = [], []
        for file, path in matched:
            try:
                duration, previews = self._media_previews(path, file["filename"], config)
                if previews:
                    candidates.append(dict(file, source=path.name, generated=self._is_generated(path.name),
                                           duration_seconds=duration))
                    images.extend(previews)
            except (OSError, ValueError, ProviderError):
                continue
        if not candidates:
            raise ValueError("最近的 USB 视频中没有同时通过哈希核验并可预览的本地成片")
        return candidates, images

    def verify_media(self, batch_id, slot, filename, dcim_filename=None):
        with self._mutex:
            batch, row = self._active(batch_id, slot)
            if batch["mode"] != "prepare" or row["status"] != "running":
                raise ValueError("仅预检查可登记手机视频")
            target = self._target(row)
            if dcim_filename is None:
                self._media_candidates.pop(slot, None)
                try:
                    file = _read_video(row["uid"], filename)
                except _UnknownVideoName:
                    if not _IMPORT_NAME.fullmatch(filename.strip()):
                        raise
                    for key in ("filename", "album_filename", "media_sha256", "source_sha256", "video", "source", "generated"):
                        row.pop(key, None)
                    row.update(video=filename, detail="导入名称与 USB 文件名不同，正在核对时长和画面")
                    self._save(batch)
                    if row.get("target"):
                        candidate, images = self._assigned_candidate(row)
                        candidates = [candidate]
                    else:
                        candidates, images = self._candidate_matches(row["uid"])
                    self._media_candidates[slot] = (target, filename.strip().casefold(),
                                                    {item["filename"].casefold(): item for item in candidates})
                    return {"needs_visual_match": True, "filename": filename, "candidates": candidates, "images": images}
                file.update(self._source(file))
            else:
                offered = self._media_candidates.get(slot)
                candidate = (offered[2].get(dcim_filename.strip().casefold())
                             if offered and offered[:2] == (target, filename.strip().casefold()) else None)
                if not candidate:
                    raise ValueError("该 USB 文件不在这台手机本轮的候选列表，请重新核对")
                file = _read_video(row["uid"], candidate["filename"])
                if file["media_sha256"] != candidate["media_sha256"] or file["size"] != candidate["size"]:
                    raise ValueError("候选视频已变化，请重新核对")
                file.update(source=candidate["source"], generated=candidate["generated"],
                            duration_seconds=candidate["duration_seconds"])
                self._media_candidates.pop(slot)
            self._check_assigned_media(row, file["filename"], file["media_sha256"])
            row.update(filename=file["filename"], media_sha256=file["media_sha256"],
                       source_sha256=self.distribution._history().source_for(file["media_sha256"]),
                       album_filename=filename.strip(), video=file["filename"],
                       source=file["source"], generated=file["generated"],
                       detail="已回读相册视频，正在核对选片")
            self._save(batch)
            return file

    @staticmethod
    def _check_assigned_media(row, filename, digest):
        target = row.get("target")
        if target and (filename.casefold() != target["filename"].casefold() or digest != target["media_sha256"]):
            raise ValueError(f'当前相册视频不匹配最近分发的成片「{target["source"]}」，请核对这台手机的目标视频')

    def _assigned_candidate(self, row):
        target = row["target"]
        file = _read_video(row["uid"], target["filename"])
        self._check_assigned_media(row, file["filename"], file["media_sha256"])
        job = self.distribution.jobs()[target["job_id"]]
        path = Path(job["path"])
        if not path.is_file() or file_digest(path) != file["media_sha256"]:
            raise ValueError("最近分发的本地成片已移动或修改，无法核对手机图库中的同一条视频")
        duration, images = self._media_previews(path, file["filename"], settings())
        if not images:
            raise ValueError("无法生成本次分发成片的参考图，请重新核对成片")
        return dict(file, source=target["source"], generated=self._is_generated(target["source"]),
                    duration_seconds=duration), images

    @staticmethod
    def _duplicate(history, row, ready=()):
        digest, source = row.get("media_sha256"), row.get("source_sha256")
        publication = history.find(digest, source)
        if publication:
            return publication["status"], publication_message(publication)
        keys = history.identity(digest, source)
        for other in ready:
            if keys & history.identity(other.get("media_sha256"), other.get("source_sha256")):
                return "same_batch", f'同一视频已安排给本批次 {other["slot"]} 号手机（抖音账号「{other["account"]}」），本机跳过'
        return None

    @staticmethod
    def _block_duplicate(row, duplicate):
        reason, detail = duplicate
        row.update(status="blocked", duplicate_reason=reason, detail=detail, updated=time.time())
        return detail

    def record(self, batch_id, slot, status, detail, account="", video="", media_sha256="", observation_id=""):
        with self._mutex:
            batch, row = self._active(batch_id, slot)
            if status == "ready":
                if (batch["mode"] != "prepare" or row["status"] != "running" or not account.strip()
                        or video.strip().casefold() != row.get("filename", "").casefold()
                        or not media_sha256 or row.get("media_sha256") != media_sha256):
                    raise ValueError("请先核对抖音账号、手机视频和已勾选的相册页面")
                observation = self._observation(slot, observation_id, self._target(row))
                duplicate = self._duplicate(self.distribution._history(), row,
                                            (other for other in batch["rows"] if other["status"] == "ready"))
                if duplicate:
                    row.update(account=account.strip(), video=video.strip())
                    self._observations.pop(slot, None)
                    detail = self._block_duplicate(row, duplicate)
                    self._save(batch)
                    raise ValueError(detail)
                row.update(account=account.strip(), video=video.strip(), prepared_screen=observation["screen"])
            elif status == "armed":
                if (batch["mode"] != "publish" or row["status"] != "preparing_publish" or row["next_taps"] != 2
                        or account.strip() != row["account"] or video.strip() != row["video"]
                        or media_sha256 != row["media_sha256"]):
                    raise ValueError("请核对已确认的账号和视频，并完成选片页、预览页的两次「下一步」")
                observation = self._observation(slot, observation_id, self._target(row))
                row["armed_screen"] = observation["screen"]
            elif status == "blocked":
                waiting_to_publish = batch["mode"] == "publish" and row["status"] == "ready"
                if row["status"] not in {"running", "preparing_publish", "armed"} and not waiting_to_publish:
                    raise ValueError("这台手机已有核对或提交结果，请保留原结果")
                if row["status"] == "running" and account.strip():
                    row["account"] = account.strip()
                if row["status"] == "running" and video.strip():
                    row["video"] = video.strip()
            elif status in {"published", "reviewing"}:
                if batch["mode"] != "publish" or row["status"] != "submission_unknown":
                    raise ValueError("只能为本批次已提交的视频登记发布结果")
                if not detail.strip():
                    raise ValueError("请记录抖音显示的实际结果")
                self._observation(slot, observation_id, self._target(row))
            else:
                raise ValueError("无效的发布状态")
            self._observations.pop(slot, None)
            row.update(status=status, detail=detail.strip(), updated=time.time())
            self._save(batch)
            return self._public(batch)

    def submit(self, batch_id, slot, observation_id, x, y, account, video, media_sha256):
        with self._mutex:
            batch, row = self._active(batch_id, slot)
            if batch["mode"] != "publish" or row["status"] != "armed":
                raise ValueError("仅可提交用户确认过且尚未提交的视频")
            if (account.strip() != row["account"] or video.strip() != row["video"]
                    or media_sha256 != row["media_sha256"]):
                raise ValueError("账号或视频与已确认的预检查结果不符")
            self._check_assigned_media(row, video.strip(), media_sha256)
            x, y = self._coordinates(x, y)
            if not (55 <= x <= 97 and 89 <= y <= 97):
                raise ValueError("提交位置不在已检查的底部「发作品」区域")
            target = self._target(row)
            observation = self._observation(slot, observation_id, target)
            if not _matches_screen(row["armed_screen"], observation["screen"]):
                raise ValueError("发布页与最终核对的截图不同，请重新检查")
            duplicate = self._duplicate(self.distribution._history(), row)
            if duplicate:
                self._observations.pop(slot, None)
                detail = self._block_duplicate(row, duplicate)
                self._save(batch)
                raise ValueError(detail)
            if _read_video(row["uid"], row["filename"])["media_sha256"] != media_sha256:
                raise ValueError("手机视频已变更，请重新检查")
            target = self._target(row)
            self._observation(slot, observation_id, target)
            latest = self._capture(target[0])
            if not _red_action_at(latest, x, y) or not _matches_screen(row["armed_screen"], _screen_regions(latest)):
                raise ValueError("点击前发现发布页已变化，请重新检查")
            self._observations.pop(slot)
            row.update(status="submission_unknown", submitted=time.time(), updated=time.time(),
                       detail="已尝试提交，请核对抖音结果。切勿重复点击")
            self._save(batch)
            request("pointerEvent", devices=target[0], data={"type": "10", "x": str(x), "y": str(y)})
            return {"status": "submission_unknown", "detail": "请截图核对抖音实际发布状态"}

    def _cleanup_candidates(self, slots, online):
        historic = sorted(self._batches(), key=lambda batch: batch["created"], reverse=True)
        attempted, aliases = {}, {}
        for batch in historic:
            for row in batch["rows"]:
                identity = _media_identity(row)
                if not identity:
                    continue
                cleanup = row.get("cleanup_status")
                if cleanup in {"deleted", "deletion_unknown"}:
                    attempted.setdefault(identity, cleanup)
                if batch["mode"] in {"prepare", "publish"} and row.get("album_filename"):
                    try:
                        alias = _album_name(row["album_filename"])
                    except ValueError:
                        continue
                    if alias:
                        aliases.setdefault(identity, {})[alias] = row["album_filename"]

        candidates, seen = {}, set()
        for batch in historic:
            if batch["mode"] != "publish":
                continue
            for row in batch["rows"]:
                identity = _media_identity(row)
                if row["status"] != "published" or not identity or identity in seen:
                    continue
                slot, uid = row["slot"], row["uid"]
                candidate_id = f'{batch["id"]}:{slot}'
                album_filename = row.get("album_filename")
                known_aliases = aliases.get(identity, {})
                if not album_filename:
                    album_filename = next(iter(known_aliases.values())) if len(known_aliases) == 1 else row["filename"]
                if attempted.get(identity) == "deletion_unknown":
                    reason = "此前已发送删除，结果待核对"
                elif attempted.get(identity) == "deleted":
                    reason = "此前已确认从相册删除"
                elif slots.get(slot) != uid:
                    reason = "原手机绑定已变化"
                elif uid not in online:
                    reason = "手机离线"
                elif any(peer["slot"] != slot and peer.get("media_sha256") == row["media_sha256"]
                         and peer["status"] != "published" and peer.get("duplicate_reason") not in {"same_batch", "published"}
                         for peer in batch["rows"]):
                    reason = "原发布批次中其他手机尚未核验此视频，保留相册可能同步的原片"
                else:
                    reason = ""
                seen.add(identity)
                candidates[candidate_id] = ({
                    "id": candidate_id, "slot": slot, "account": row.get("account") or "",
                    "video": row.get("video") or row["filename"], "source": row.get("source") or "",
                    "album_filename": album_filename,
                    "published_at": row.get("updated") or row.get("submitted") or batch["created"],
                    "online": slots.get(slot) == uid and uid in online,
                    "available": not reason, "reason": reason,
                }, row)
        return candidates

    def begin_cleanup(self, selection_ids):
        if (not isinstance(selection_ids, list) or not selection_ids
                or not all(isinstance(identifier, str) for identifier in selection_ids)
                or len(set(selection_ids)) != len(selection_ids)):
            raise ValueError("请选择需要清理的已发布原片")
        return self._begin_cleanup(selection_ids)

    def begin_auto_cleanup(self):
        return self._begin_cleanup(None)

    def _begin_cleanup(self, selection_ids):
        automatic = selection_ids is None
        with self._mutex:
            self._acquire()
            try:
                self._recover_abandoned()
                slots, online, error = self._device_state()
                if error:
                    raise ValueError(error)
                records = self._cleanup_candidates(slots, online)
                if automatic:
                    selection_ids = sorted((identifier for identifier, (candidate, _) in records.items()
                                            if candidate["available"]), key=lambda identifier: records[identifier][0]["slot"])
                    if not selection_ids:
                        raise ValueError("没有当前可安全清理的已发布原片")
                rows = []
                selected_slots = set()
                for identifier in selection_ids:
                    if identifier not in records:
                        raise ValueError("选中的原片没有可核验的发布记录，请刷新清单")
                    candidate, published = records[identifier]
                    if not candidate["available"]:
                        raise ValueError(f'{candidate["slot"]} 号手机：{candidate["reason"]}')
                    if not automatic and candidate["slot"] in selected_slots:
                        raise ValueError("同一手机每批只能清理一条原片，请分批选择")
                    selected_slots.add(candidate["slot"])
                    row = {key: published[key] for key in ("uid", "filename", "media_sha256")}
                    row.update({key: candidate[key] for key in ("slot", "account", "video", "source",
                                                                 "album_filename", "published_at")})
                    row.update(candidate_id=identifier, status="published", detail="对应作品已核验发布",
                               cleanup_status="waiting", cleanup_detail="等待核对手机相册原片", cleanup_opened_at=None)
                    rows.append(row)
                for row in rows:
                    try:
                        self._target(row)
                        self._recheck_cleanup_media(row)
                    except Exception:
                        row.update(cleanup_status="blocked", cleanup_detail="无法通过 USB 核验原片，已保留")
                batch = {"id": uuid.uuid4().hex, "mode": "cleanup", "status": "running", "created": time.time(),
                         "automatic": automatic, "slots": sorted(selected_slots), "rows": rows}
                self._save(batch)
                self._active_id = batch["id"]
                return self._public(batch)
            except BaseException:
                self._release()
                raise

    @staticmethod
    def _cleanup_allowed(batch, row):
        return (batch["mode"] == "cleanup" and row["status"] == "published"
                and row.get("cleanup_status") in CLEANUP_ACTIVE)

    def _cleanup(self, batch_id, slot, *statuses):
        batch, row = self._active(batch_id, slot)
        if not self._cleanup_allowed(batch, row) or statuses and row["cleanup_status"] not in statuses:
            raise ValueError("仅可处理独立清理批次中选定的已发布原片")
        return batch, row

    def cleanup_context(self, batch_id, slot, candidate_id=None):
        with self._mutex:
            batch = self._active_batch(batch_id)
            if batch["mode"] != "cleanup":
                return None
            if batch.get("automatic"):
                rows = [row for row in batch["rows"] if row["slot"] == slot]
                row = next((row for row in rows if row["candidate_id"] == candidate_id), None)
                if row is None:
                    raise ValueError("清理项不在本台手机的计划中")
                if not self._cleanup_allowed(batch, row):
                    return None
                if any(other["candidate_id"] != candidate_id and other["cleanup_status"] == "deletion_unknown"
                       for other in rows):
                    row.update(cleanup_status="blocked", cleanup_detail="同一手机此前删除结果待核对，本项已保留")
                    self._save(batch)
                    return None
                previous = next((other for other in rows if other["candidate_id"] == self._cleanup_current.get(slot)), None)
                if previous is not None and previous["candidate_id"] != candidate_id:
                    if previous["cleanup_status"] in CLEANUP_ACTIVE:
                        previous.update(cleanup_status="blocked", cleanup_detail="任务未完成核验，已保留原片")
                        self._save(batch)
                    self._observations.pop(slot, None)
                self._cleanup_current[slot] = candidate_id
            else:
                _, row = self._active(batch_id, slot)
                if candidate_id is not None and row.get("candidate_id") != candidate_id:
                    raise ValueError("清理项不在本台手机的计划中")
            if not self._cleanup_allowed(batch, row):
                return None
            return {key: row.get(key) for key in
                    ("slot", "candidate_id", "album_filename", "filename", "media_sha256", "source", "account", "cleanup_status")}

    def cleanup_open(self, batch_id, slot):
        with self._mutex:
            batch, row = self._cleanup(batch_id, slot, "waiting")
            if not row.get("album_filename") or not row.get("filename") or not row.get("media_sha256"):
                raise ValueError("缺少本次提交的原始相册文件名或哈希，已保留原片")
            target = self._target(row)
            self._observations.pop(slot, None)
            row.update(cleanup_status="checking", cleanup_detail="正在相册核对发布原片",
                       cleanup_opened_at=time.time())
            self._save(batch)
            request("startApp", devices=target[0], data={"app": APPS["photos"]})
            return {"cleanup_status": row["cleanup_status"], **self._cleanup_preview(row)}

    def _cleanup_preview(self, row):
        digest = row["media_sha256"]
        jobs = self.distribution.jobs()
        original_job = jobs.get(f'{row["uid"]}:{digest}')
        if not original_job or not original_job.get("path"):
            return {}
        path = Path(original_job["path"])
        try:
            if not path.is_file() or file_digest(path) != digest:
                return {}
            duration, images = self._media_previews(path, row["filename"], settings())
        except (OSError, ValueError, ProviderError):
            return {}
        return {"duration_seconds": duration, "images": images}

    @staticmethod
    def _recheck_cleanup_media(row):
        file = _read_video(row["uid"], row["filename"])
        if file["media_sha256"] != row["media_sha256"]:
            raise ValueError("USB 视频与本次发布时的哈希不同，已保留原片")

    def cleanup_verify(self, batch_id, slot, filename, observation_id):
        with self._mutex:
            batch, row = self._cleanup(batch_id, slot, "checking")
            original = _album_name(row["album_filename"])
            if not original or _album_name(filename) != original:
                raise ValueError("相册文件名与本次发布的原片不同，请勿删除其他视频")
            target = self._target(row)
            observation = self._observation(slot, observation_id, target)
            self._recheck_cleanup_media(row)
            self._observations.pop(slot)
            info_screen = {**observation["screen"],
                           "photo_info": base64.b64encode(_photo_info_pixels(observation["image"])).decode()}
            row.update(cleanup_status="matched", cleanup_detail="已核对相册原始名称和视频 SHA-256",
                       cleanup_target=list(target), cleanup_info_screen=info_screen,
                       cleanup_info_dismissed=False)
            self._save(batch)
            return {"cleanup_status": row["cleanup_status"], "album_filename": row["album_filename"]}

    def _cleanup_view(self, slot, observation_id, row):
        target = self._target(row)
        if tuple(row["cleanup_target"]) != target:
            raise ValueError("手机连接已变化，请保留原片")
        observation = self._observation(slot, observation_id, target)
        self._recheck_cleanup_media(row)
        if self._target(row) != target:
            raise ValueError("复核时手机连接发生变化，请保留原片")
        latest = self._capture(target[0])
        if not _matches_screen(observation["screen"], _screen_regions(latest)):
            self._observations.pop(slot, None)
            raise ValueError("相册画面已变化，请重新查看当前原片")
        return target, latest

    def cleanup_trash(self, batch_id, slot, observation_id, x, y):
        with self._mutex:
            batch, row = self._cleanup(batch_id, slot, "matched")
            x, y = self._coordinates(x, y)
            if not (70 <= x <= 99 and 83 <= y <= 98) or not row.get("cleanup_info_dismissed"):
                raise ValueError("请先关闭已核对的信息页，再核对单个视频底部的垃圾桶")
            target, latest = self._cleanup_view(slot, observation_id, row)
            fullscreen = _screen_regions(latest)
            if (_matches_photo_info(row["cleanup_info_screen"], latest)
                    or _matches_screen(row["cleanup_info_screen"], fullscreen, ("settings", "actions"))):
                raise ValueError("原片信息页尚未关闭，请核对全屏单个视频")
            self._observations.pop(slot)
            row.update(cleanup_status="confirming", cleanup_detail="等待核对单个视频的系统删除确认",
                       cleanup_fullscreen=fullscreen)
            self._save(batch)
            request("pointerEvent", devices=target[0], data={"type": "10", "x": str(x), "y": str(y)})
            return {"cleanup_status": row["cleanup_status"]}

    def cleanup_delete(self, batch_id, slot, observation_id, x, y):
        with self._mutex:
            batch, row = self._cleanup(batch_id, slot, "confirming")
            x, y = self._coordinates(x, y)
            center_sheet = 38 <= x <= 62 and 65 <= y <= 95
            right_popover = 62 < x <= 82 and 86 <= y <= 95
            if not (center_sheet or right_popover):
                raise ValueError("请选择系统对当前单个视频显示的红色删除确认")
            target, latest = self._cleanup_view(slot, observation_id, row)
            dialog = _screen_regions(latest)
            if _red_fraction(latest, x, y) < .02 or _matches_screen(row["cleanup_fullscreen"], dialog):
                raise ValueError("未检出单个视频的红色删除确认，请保留原片")
            self._observations.pop(slot)
            row.update(cleanup_status="deletion_unknown", cleanup_detail="已尝试移入最近删除，等待相册画面核验",
                       cleanup_dialog_screen=dialog, cleanup_submitted=time.time())
            self._save(batch)
            request("pointerEvent", devices=target[0], data={"type": "10", "x": str(x), "y": str(y)})
            return {"cleanup_status": row["cleanup_status"]}

    def cleanup_record(self, batch_id, slot, status, detail, observation_id=""):
        with self._mutex:
            batch, row = self._cleanup(batch_id, slot)
            detail = detail.strip()
            if not detail:
                raise ValueError("请注明相册清理的核对结果")
            if status == "deleted":
                if row["cleanup_status"] != "deletion_unknown":
                    raise ValueError("只能核验本批次已发送的删除操作")
                target = self._target(row)
                if tuple(row["cleanup_target"]) != target:
                    raise ValueError("连接已变化，原片删除结果待核对")
                observation = self._observation(slot, observation_id, target)
                if any(_matches_screen(row[key], observation["screen"]) for key in
                       ("cleanup_dialog_screen", "cleanup_fullscreen")):
                    raise ValueError("仍显示删除前画面，请核对原片是否已移出图库")
                row.update(cleanup_status="deleted", cleanup_detail=detail)
            elif status == "blocked":
                if row["cleanup_status"] == "deletion_unknown":
                    row["cleanup_detail"] = "删除结果待核对，切勿重复点击。" + detail
                else:
                    row.update(cleanup_status="blocked", cleanup_detail=detail)
            else:
                raise ValueError("无效的原片清理状态")
            self._observations.pop(slot, None)
            self._save(batch)
            return {"cleanup_status": row["cleanup_status"], "cleanup_detail": row["cleanup_detail"]}
