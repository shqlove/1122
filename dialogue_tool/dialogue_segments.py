"""Deterministic dialogue-only planning; never add creative prompt instructions."""
import re

from .generation import CanvasError

POLICY = 'dialogue-two-v1'
SINGLE_CHARS = 75
PART_LIMIT = 90


def dialogue_span(prompt):
    match = re.search(r'(?:说出台词|台词|说出对白)\s*[：:]?\s*[“「](.*?)[”」]', prompt, re.S)
    if not match or not match.group(1).strip():
        raise CanvasError('请用 台词：“完整台词” 标明对白，才能只拆台词而保留其余提示词')
    return match.span(1)


def template(prompt):
    a, b = dialogue_span(prompt)
    return prompt[:a] + '{对白}' + prompt[b:]


def count(text):
    return len(re.findall(r'[\u4e00-\u9fffA-Za-z0-9]', text))


def split_prompts(prompt):
    a, b = dialogue_span(prompt)
    speech = prompt[a:b]
    if count(speech) <= SINGLE_CHARS:
        return [prompt]
    # Prefer sentence/line boundaries, and keep obvious dependent clauses together.
    candidates = []
    for m in re.finditer(r'[。！？!?；;]+[\s]*|\n+', speech):
        left, right = speech[:m.end()], speech[m.end():]
        if not count(left) or not count(right) or max(count(left), count(right)) > PART_LIMIT:
            continue
        if re.match(r'\s*(那就|而是|只要|就比|会在|让你|不是为了)', right):
            continue
        score = abs(count(left)-count(right))
        candidates.append((score, m.end()))
    if not candidates:
        raise CanvasError('台词无法在两个15秒段内按完整语意拆分，请调整台词或明确新的分段方案；未准备付费任务')
    _, cut = min(candidates)
    # Keep every original character, including punctuation and whitespace.
    return [prompt[:a] + s + prompt[b:] for s in (speech[:cut], speech[cut:])]


def enabled(cfg):
    return cfg.get('dialogue_policy') == POLICY and not cfg.get('segment_plan')


def source_id(gid):
    return gid.rsplit('__dialogue_part_', 1)[0]


def has_tasks(project, gid):
    return any((task.get('source_group_id') or key) == gid
               for key, task in project.get('generation', {}).get('tasks', {}).items())


def expanded_groups(project, ids):
    cfg = project.get('generation', {})
    known = {g['id']: g for g in project['groups']}
    if not ids or len(set(ids)) != len(ids):
        raise CanvasError('生成检查范围为空或重复')
    if not enabled(cfg):
        if not set(ids).issubset(known):
            raise CanvasError('图片组不存在')
        return [known[gid] for gid in ids]
    if cfg.get('duration') != 15 or cfg.get('audio_first') or not cfg.get('preserve_prompt', True) or cfg.get('prompt_mode') != 'shared':
        raise CanvasError('自动分段使用15秒、共用原文与音色参考；其他实验请显式关闭自动分段')
    prompts = split_prompts(cfg['global_prompt'])
    result = {}
    for gid in ids:
        source = gid if gid in known else source_id(gid)
        if source not in known:
            raise CanvasError('图片组不存在')
        for index, prompt in enumerate(prompts, 1):
            key = source if len(prompts) == 1 else f'{source}__dialogue_part_{index}'
            if gid != source and gid != key:
                continue
            result[key] = {**known[source], 'id': key, '_dialogue_part': {
                'source_group_id': source, 'index': index, 'count': len(prompts), 'prompt': prompt}}
        if gid != source and gid not in result:
            raise CanvasError('台词分段已变化，请重新准备和报价')
    return list(result.values())


def assembly_plans(cfg):
    legacy = cfg.get('segment_plan')
    return ([legacy] if legacy else []) + list(cfg.get('dialogue_plans', {}).values())
