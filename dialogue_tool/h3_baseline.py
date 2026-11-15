"""Versioned, platform-verified H3 inputs; never rewrite submitted history."""
import json
import shutil
from pathlib import Path

from .generation import CanvasError, _sha
from .projects import character_filter

ROOT = Path(__file__).parent / 'resources' / 'h3-first-v1'
BASELINE = json.loads((ROOT / 'baseline.json').read_text(encoding='utf-8'))
BASELINE_ID = BASELINE['id']
NO_SUBTITLES = '画面中不要生成任何字幕、对白文字、标题或文字叠加。保留人物对白音轨。'


def uses_haoge(project):
    name = character_filter(project).get('required_character', '')
    return '豪哥' in name or '张世豪' in name


def apply_baseline(project):
    cfg = project.setdefault('generation', {})
    if cfg.get('tasks'):
        raise CanvasError('本轮已有草稿或任务，请先处理原任务并新建轮次，再应用首次任务基准')
    source = ROOT / 'voice.mp3'
    if _sha(source) != BASELINE['audio_sha256']:
        raise CanvasError('首次任务参考音频校验失败，请恢复基准文件')
    target = Path(project['directory']) / 'voice-references' / (BASELINE['audio_sha256'] + '.mp3')
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or _sha(target) != BASELINE['audio_sha256']:
        shutil.copy2(source, target)
    previous_prompt = cfg.get('global_prompt')
    preserve_dialogue = cfg.get('dialogue_policy') == 'dialogue-two-v1'
    cfg.update(BASELINE['parameters'], baseline_id=BASELINE_ID,
               global_prompt=BASELINE['global_prompt'], speech_emotion='', baseline_voice_path=str(target),
               dialogue_policy='dialogue-two-v1')
    cfg.pop('dialogue_plans', None)
    for key in ('mute_audio', 'aigc_watermark', 'skill_id', 'verbatim_prompt'):
        cfg.pop(key, None)
    if preserve_dialogue and previous_prompt:
        from .dialogue_segments import template
        try:
            if template(previous_prompt) == template(BASELINE['global_prompt']):
                cfg['global_prompt'] = previous_prompt
        except CanvasError:
            pass
    cfg.pop('quote', None)
    cfg.pop('pending_approval', None)
    return project


def validate_baseline(cfg):
    if cfg.get('baseline_id') != BASELINE_ID:
        return
    expected = {**BASELINE['parameters'], 'global_prompt': BASELINE['global_prompt']}
    changed = [key for key, value in expected.items() if cfg.get(key) != value]
    if 'global_prompt' in changed and cfg.get('dialogue_policy') == 'dialogue-two-v1':
        from .dialogue_segments import template
        try:
            if template(cfg['global_prompt']) == template(BASELINE['global_prompt']):
                changed.remove('global_prompt')
        except CanvasError:
            pass
    if changed:
        raise CanvasError('首次任务基准已锁定，配置发生偏移：' + '、'.join(changed) +
                          '。仅用户明确要求新方案时，才切换自定义配置')


def review_valid(group, ref):
    review = group.get('reference_reviews', {}).get(ref.get('side'), {})
    return review.get('sha256') == ref['sha256'] and review.get('clean') is True
