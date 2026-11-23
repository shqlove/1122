"""Two bound iPhones. Upload through Xiaowei and verify the original bytes over USB."""
import hashlib
import json
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from .project_run import AnalysisBusy, analysis_lock
from .projects import file_digest
from .publication_history import PublicationHistory, publication_message
from .workbench import read_json, save_json


def request(action, **params):
    from websocket import create_connection

    with closing(create_connection("ws://127.0.0.1:33333/", timeout=12,
                                   http_no_proxy=["127.0.0.1"])) as ws:
        ws.send(json.dumps({"action": action, **params}))
        result = json.loads(ws.recv())
    if result.get("code") != 10000:
        raise ValueError(result.get("message") or "效卫接口错误")
    return result.get("data")


def devices():
    found = {}
    for device in request("list"):
        uid = device.get("onlySerial")
        if uid and device.get("serial"):
            if uid in found:
                raise ValueError("效卫返回了重复的设备标识，请检查连接")
            found[uid] = device
    return found


def find_on_phone(uid, size, expected):
    import tidevice

    # Only the paired USB device with this UDID may attest to delivery.
    with closing(tidevice.Device(uid).sync) as afc:
        afc.get_socket().settimeout(12)
        if not afc.stat("/DCIM").is_dir():
            raise ValueError("无法读取手机相册，请解锁并检查 USB 信任状态")
        for folder in sorted(afc.listdir("/DCIM"), reverse=True):
            if not folder.isprintable():
                raise ValueError("读取手机相册目录失败")
            if not folder.upper().endswith("APPLE"):
                continue
            directory = f"/DCIM/{folder}"
            for item in afc.listdir_info(directory):
                if item.st_size != size or Path(item.st_name).suffix.lower() not in {".mp4", ".mov", ".m4v"}:
                    continue
                path = f"{directory}/{item.st_name}"
                digest = hashlib.sha256()
                for chunk in afc.iter_content(path):
                    if not chunk:
                        raise OSError("USB 文件回读中断")
                    digest.update(chunk)
                if digest.hexdigest() == expected:
                    return path
    return None


class PhoneDistribution:
    def __init__(self, workspace, *, postprocess_root=None):
        self.root = Path(workspace) / "phone-distribution"
        self.root.mkdir(parents=True, exist_ok=True)
        self.queue = self.root / "jobs.json"
        self.postprocess_root = postprocess_root

    def bindings(self):
        slots = read_json(self.root / "phones.json")
        if set(slots) != {"01", "02"} or len(set(slots.values())) != 2 or not all(slots.values()):
            raise ValueError("本机的两台效卫手机绑定记录无效")
        return slots

    def jobs(self):
        return read_json(self.queue) if self.queue.exists() else {}

    def batches(self):
        return sorted((read_json(path) for path in (self.root / "batches").glob("*.json")),
                      key=lambda batch: batch["created"], reverse=True)

    def _history(self):
        return PublicationHistory(self.root.parent, postprocess_root=self.postprocess_root)

    @staticmethod
    def _published(history, job):
        return history.find(job["sha256"], job.get("source_sha256"), published_only=True)

    def state(self):
        slots = self.bindings()
        error = ""
        try:
            online = devices()
        except Exception as exc:
            online = {}
            error = f"无法读取效卫设备，请检查苹果投屏客户端和 API。{exc}"
        history = self._history()
        return {"phones": [{"slot": slot, "suffix": uid[-6:], "online": uid in online}
                           for slot, uid in sorted(slots.items())],
                "error": error, "jobs": [dict(job, publication=self._published(history, job))
                                         for job in reversed(self.jobs().values())],
                "batches": self.batches()}

    @contextmanager
    def writer(self):
        try:
            with analysis_lock(self.root):
                yield
        except AnalysisBusy as exc:
            raise ValueError("另一项手机分发正在执行，请稍后刷新记录") from exc

    def start(self, assignments, library):
        slots = self.bindings()
        if not isinstance(assignments, dict) or not assignments or not assignments.keys() <= slots.keys():
            raise ValueError("请选择 01 或 02 号手机以及对应成片")
        with self.writer():
            jobs = self.jobs()
            history = self._history()
            rows = []
            selected = []
            for slot, ids in assignments.items():
                if isinstance(ids, str):  # Existing desktop clients send one item per phone.
                    ids = [ids]
                if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                    raise ValueError("每台目标手机至少需要选择一条成片")
                selected.extend((slot, media_id) for media_id in dict.fromkeys(ids))
            for slot, media_id in selected:
                item = library.get(media_id, {})
                path = Path(item.get("path") or "")
                if (item.get("kind") != "post" or item.get("trashed") or not item.get("available")
                        or path.suffix.lower() != ".mp4" or not path.is_file()):
                    raise ValueError("所选成片已移动或已移入回收站，请重新选择")
                digest = file_digest(path)
                recorded_output = item.get("output_sha256")
                source_sha256 = (item.get("source_sha256")
                                 if isinstance(recorded_output, str) and recorded_output.lower() == digest else None)
                source_sha256 = source_sha256 or history.source_for(digest)
                publication = history.find(digest, source_sha256, published_only=True)
                if publication:
                    raise ValueError(f"{path.name}。{publication_message(publication)}")
                key = f"{slots[slot]}:{digest}"
                rows.append({"job_id": key, "slot": slot, "suffix": slots[slot][-6:],
                             "media_id": media_id, "project": item.get("project_name", ""),
                             "file": path.name, "source": str(path.resolve()), "result": "registered"})
                if key not in jobs:
                    jobs[key] = {"id": key, "slot": slot, "uid": slots[slot], "sha256": digest,
                                 "size": path.stat().st_size, "file": path.name, "path": str(path.resolve()),
                                 "phase": "queued", "detail": "已排队", "attempts": 0, "sent": 0}
                else:
                    jobs[key]["path"] = str(path.resolve())
                if source_sha256:
                    jobs[key]["source_sha256"] = source_sha256
            save_json(self.queue, jobs)
            batch_id = self._run_batch(jobs, rows, "分发", history=history)
            return [dict(jobs[row["job_id"]], batch_id=batch_id) for row in rows]

    def resume(self, key, resend=False):
        with self.writer():
            jobs = self.jobs()
            if key not in jobs:
                raise ValueError("分发记录不存在")
            job = jobs[key]
            history = self._history()
            publication = self._published(history, job)
            if publication and (resend or job["phase"] == "queued"):
                raise ValueError(publication_message(publication))
            row = {"job_id": key, "slot": job["slot"], "suffix": job["uid"][-6:],
                   "file": job["file"], "source": job["path"], "result": "registered"}
            action = "复核后补传" if resend else ("继续分发" if job["phase"] == "queued" else "复核手机")
            batch_id = self._run_batch(jobs, [row], action, resend, history=history)
            return dict(job, batch_id=batch_id)

    def _run_batch(self, jobs, rows, action, resend=False, *, history):
        batch = {"id": uuid.uuid4().hex, "created": time.time(), "action": action, "rows": rows}
        path = self.root / "batches" / (batch["id"] + ".json")
        save_json(path, batch)
        for row in rows:
            job = jobs[row["job_id"]]
            already_done = job["phase"] == "done"
            before = job["attempts"]
            self._process(jobs, job["id"], resend, history=history)
            result = job["phase"]
            if already_done:
                result = "skipped_record"
            elif result == "done":
                if job["attempts"] > before:
                    result = "uploaded"
                else:
                    result = "found"
            row.update(result=result, detail=job["detail"], remote=job.get("remote"),
                       submitted=job["attempts"] > before)
            save_json(path, batch)
        return batch["id"]

    def _process(self, jobs, key, resend=False, *, history):
        job = jobs[key]
        if job["phase"] == "done":
            return

        def mark(phase, detail):
            job.update(phase=phase, detail=detail, updated=time.time())
            save_json(self.queue, jobs)

        def verified(path):
            job["remote"] = path
            mark("done", "手机已有相同成片，已跳过上传" if not job["attempts"] else "已回读手机文件，SHA-256 一致")

        try:
            uid = self.bindings()[job["slot"]]
            if uid != job["uid"]:
                raise ValueError("设备绑定已变化，请核对原手机")
            target = devices().get(uid)
            if not target:
                raise ValueError("手机离线，请连接原手机后继续")
            match = find_on_phone(uid, job["size"], job["sha256"])
            if match:
                verified(match)
                return
            publication = self._published(history, job)
            if publication:
                mark(job["phase"], "手机暂未找到匹配文件。" + publication_message(publication))
                return
            if job["attempts"]:
                ready = time.time() - job["sent"] >= 120
                if not (resend and ready and job["phase"] == "missing"):
                    mark("missing" if ready else "verify", "手机暂未找到匹配文件。请检查相册权限后复核")
                    return
            source = Path(job["path"])
            if not source.is_file() or file_digest(source) != job["sha256"]:
                raise ValueError("源文件已移动或内容已更改，请从成片库重新选择")
            job["attempts"] += 1
            job["sent"] = time.time()
            # An interruption from this point on must resume with verification first.
            mark("verify", "已开始提交，等待手机端核验")
            request("uploadFile", devices=target["serial"], data={"filePath": str(source), "fileType": 2})
            for delay in (1, 2, 4, 6):
                time.sleep(delay)
                match = find_on_phone(uid, job["size"], job["sha256"])
                if match:
                    verified(match)
                    return
            mark("verify", "效卫已受理，手机暂未检出成片。请检查 Panda Helper 照片权限后复核")
        except Exception as exc:
            mark("verify" if job["attempts"] else "queued", str(exc) or type(exc).__name__)
