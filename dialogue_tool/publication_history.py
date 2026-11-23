"""Read durable Douyin submissions and map post-production re-exports to their source."""

import json
import re
from pathlib import Path

from .settings import settings


_ACTIVE_PUBLICATIONS = {"published", "reviewing", "submission_unknown"}
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


def _digest(value):
    return value.lower() if isinstance(value, str) and _SHA256.fullmatch(value) else ""


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def publication_message(record):
    slot = f'{record["slot"]} 号手机' if record.get("slot") else "手机编号未记录"
    account = f'抖音账号「{record["account"]}」' if record.get("account") else "抖音账号未记录"
    if record["status"] == "published":
        return f"已有这条视频的提交记录（{slot}，{account}），已发布，请勿再次分发或发布"
    return f"已有这条视频的提交记录（{slot}，{account}），发布结果待核对，请勿再次发布"


class PublicationHistory:
    """Snapshot of global media identities and their current publishing status."""

    def __init__(self, workspace, *, postprocess_root=None, lineages=None):
        workspace = Path(workspace)
        self._lineages = {}
        if lineages is None:
            post_root = Path(postprocess_root or settings()["postprocess_root"])
            for path in (post_root / "batches").glob("*/manifest.json"):
                try:
                    manifest = _read(path)
                except (OSError, ValueError):
                    # A manifest may be changing; preserved output bytes still deduplicate.
                    continue
                if isinstance(manifest, dict) and isinstance(manifest.get("jobs"), list):
                    for job in manifest["jobs"]:
                        if isinstance(job, dict):
                            self._add_lineage(job.get("outputSha256"), job.get("sourceSha256"))
        else:
            for output, source in lineages.items():
                self._add_lineage(output, source)

        jobs_path = workspace / "phone-distribution" / "jobs.json"
        if jobs_path.is_file():
            try:
                jobs = _read(jobs_path)
            except (OSError, ValueError):
                # Publication records and manifest provenance are independent of the queue.
                pass
            else:
                if isinstance(jobs, dict):
                    for job in jobs.values():
                        if isinstance(job, dict):
                            self._add_lineage(job.get("sha256"), job.get("source_sha256"))

        published_rows = []
        cleanup = {}
        for path in (workspace / "douyin-publish" / "batches").glob("*.json"):
            try:
                batch = _read(path)
            except (OSError, ValueError) as exc:
                raise ValueError("无法读取抖音发布历史，请先核对发布记录") from exc
            if not isinstance(batch, dict) or not isinstance(batch.get("rows"), list):
                raise ValueError("抖音发布历史格式无效，请先核对发布记录")
            for row in batch["rows"]:
                if not isinstance(row, dict):
                    raise ValueError("抖音发布历史格式无效，请先核对发布记录")
                if batch.get("mode") == "publish" and row.get("status") in _ACTIVE_PUBLICATIONS:
                    published_rows.append(row)
                    self._add_lineage(row.get("media_sha256"), row.get("source_sha256"))
                if batch.get("mode") == "cleanup" and row.get("cleanup_status"):
                    key = self._cleanup_identity(row)
                    stamp = batch.get("created") or 0
                    if key is not None and stamp >= cleanup.get(key, (0, ""))[0]:
                        cleanup[key] = stamp, row["cleanup_status"]

        self._records = {}
        for row in published_rows:
            record = {
                "status": "published" if row["status"] == "published" else "pending",
                "slot": row.get("slot") or "",
                "account": row.get("account") or "",
                "published_at": row.get("updated") or row.get("submitted") or None,
            }
            cleanup_status = cleanup.get(self._cleanup_identity(row), (0, row.get("cleanup_status")))[1]
            if cleanup_status:
                record["cleanup_status"] = cleanup_status
            for key in self.identity(row.get("media_sha256"), row.get("source_sha256")):
                previous = self._records.get(key)
                if previous is None or self._priority(record) > self._priority(previous):
                    self._records[key] = record

    def _add_lineage(self, output, source):
        output, source = _digest(output), _digest(source)
        if output and source:
            self._lineages.setdefault(output, set()).add(source)

    @staticmethod
    def _cleanup_identity(row):
        uid, filename, digest = row.get("uid"), row.get("filename"), _digest(row.get("media_sha256"))
        if isinstance(uid, str) and uid and isinstance(filename, str) and filename and digest:
            return uid, filename.casefold(), digest
        return None

    @staticmethod
    def _priority(record):
        stamp = record["published_at"]
        return record["status"] == "published", stamp if isinstance(stamp, (int, float)) else 0

    def source_for(self, output_sha256):
        sources = self._lineages.get(_digest(output_sha256), ())
        return next(iter(sources)) if len(sources) == 1 else None

    def identity(self, output_sha256, source_sha256=None):
        output, source = _digest(output_sha256), _digest(source_sha256)
        keys = {("output", output)} if output else set()
        keys.update(("source", item) for item in self._lineages.get(output, ()))
        if source:
            keys.add(("source", source))
        return keys

    def find(self, output_sha256, source_sha256=None, *, published_only=False):
        matches = (self._records[key] for key in self.identity(output_sha256, source_sha256) if key in self._records)
        record = max(matches, key=self._priority, default=None)
        if record is None or (published_only and record["status"] != "published"):
            return None
        return dict(record)
