"""Standalone postproduction worker; continues when the workbench window closes."""
import copy
import json
from pathlib import Path
import sys
import threading
import time


def main():
    request_file = Path(sys.argv[1]).resolve()
    request = json.loads(request_file.read_text(encoding='utf-8'))
    runtime = Path(request['runtime'])
    sys.path.insert(0, str(runtime))
    import postprocess as p
    from media_pipeline import save_json, sha256, parse_srt
    state, state_file = request['state'], request_file.with_name('state.json')
    guard = threading.RLock()
    done = threading.Event()
    def update(**values):
        with guard:
            state.update(values, heartbeat=time.time())
            save_json(state_file, state)
    def heartbeat():
        while not done.wait(10):
            update()
    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        with p.locked(runtime / 'workbench-worker.lock'):
            cfg = p.configured(request_file.with_name('config.json'))
            prior = []
            for old in (runtime / 'batches').glob('*/manifest.json'):
                prior.extend(p.load(old).get('jobs', []))
            edit = request.get('caption_edit')
            if edit:
                cfg = copy.deepcopy(cfg)
                cfg['profileVersion'] += '-captions-' + state['id'][:8]
            scopes = {}
            for entry in request['items']:
                if entry.get('source_sha256') and sha256(entry['source']) != entry['source_sha256']:
                    raise ValueError('所选原片在启动后被修改，请重新核对')
                path = p.plan(entry['source'], cfg)
                with p.locked(path.parent / 'batch.lock'):
                    manifest = p.checked_manifest(path)
                    job = next(j for j in manifest['jobs'] if j['source'] == entry['source'])
                    scopes.setdefault(path, set()).add(job['id'])
                    exact = [j for j in prior if j['source'] == job['source'] and j['sourceSha256'] == job['sourceSha256']]
                    exact.sort(key=lambda j: j.get('updatedAt', j.get('createdAt', 0)), reverse=True)
                    if not job.get('captions'):
                        previous = next((j for j in exact if j.get('captions')), None)
                        if previous:
                            cap = previous['captions']
                            if sha256(cap['path']) != cap['sha256']:
                                raise ValueError('已有字幕被修改，请核对原记录')
                            # Rebuild automatic timing groups from cached words when
                            # the grouping rule changes; never reuse obsolete cuts.
                            regroup = (cap.get('provider') == 'Aliyun' and not cap.get('userEdited') and
                                       cap.get('segmentation') != cfg['captions'].get('segmentation') and
                                       previous.get('aliyun', {}).get('status') == 'complete')
                            if not regroup:
                                job['captions'] = copy.deepcopy(cap)
                            job['chatcut'] = copy.deepcopy(previous.get('chatcut', {}))
                            job['aliyun'] = copy.deepcopy(previous.get('aliyun', {}))
                        elif exact:
                            job['chatcut'] = copy.deepcopy(exact[0].get('chatcut', {}))
                            if not job.get('aliyun') and not job.get('aliyun_attempts'):
                                previous_asr = next((j.get('aliyun') for j in exact if j.get('aliyun')), None)
                                if previous_asr:
                                    job['aliyun'] = copy.deepcopy(previous_asr)
                    job['workbench'] = {k: entry.get(k) for k in ('project_id', 'group_id', 'group_ids', 'item_id')}
                    if edit:
                        subtitle = request_file.with_name('edited.srt')
                        subtitle.write_text(edit['text'], encoding='utf-8')
                        cues = parse_srt(subtitle, job['media']['duration'])
                        job['captions'] = {'path': str(subtitle), 'sha256': sha256(subtitle), 'provider': (job.get('captions') or {}).get('provider', cfg['captions']['provider']),
                                           'projectId': job.get('chatcut', {}).get('projectId'), 'cueCount': len(cues),
                                           'userEdited': True}
                    p.save_json(path, manifest)
            update(manifests=[str(v) for v in scopes], message='正在处理字幕、空间效果和背景音乐')
            reports = []
            for path, selected in scopes.items():
                with p.locked(path.parent / 'batch.lock'):
                    manifest = p.checked_manifest(path)
                    manifest['selectedJobIds'] = sorted(selected)
                    p.save_json(path, manifest)
                reports.append(p.run_batch(path, recognize=request['recognize'], job_ids=sorted(selected)))
            counts = {}
            for report in reports:
                for name, count in report['counts'].items():
                    counts[name] = counts.get(name, 0) + count
            result = 'complete' if set(counts) == {'complete'} else 'failed' if counts.get('failed') else 'waiting'
            update(status=result, counts=counts, message=f"已导出 {counts.get('complete', 0)}/{state['count']} 条", finished_at=time.time())
    except Exception as exc:
        update(status='failed', message=str(exc), finished_at=time.time())
    finally:
        done.set()


if __name__ == '__main__':
    main()
