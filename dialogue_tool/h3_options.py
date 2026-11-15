"""Optional controls exposed by the H3 video-generation API."""
from .generation import CanvasError

DEFAULTS = {'mute_audio': False, 'aigc_watermark': False, 'skill_id': '', 'verbatim_prompt': False}


def validate_options(cfg):
    for key in ('mute_audio', 'aigc_watermark', 'verbatim_prompt'):
        if type(cfg.get(key, False)) is not bool:
            raise CanvasError('声音与水印开关须为布尔值')
    skill = cfg.get('skill_id', '')
    if not isinstance(skill, str) or len(skill) > 128 or skill != skill.strip():
        raise CanvasError('请输入有效的平台 Skill ID')
    if skill and not cfg.get('use_context_ir', True):
        raise CanvasError('平台 Skill 需要开启 Context-IR')
    if cfg.get('mute_audio') and (cfg.get('audio_first') or cfg.get('dialogue_policy') == 'dialogue-two-v1' or cfg.get('segment_plan')):
        raise CanvasError('无声视频请关闭自动台词分段和先配音；对白拼接流程须保留音轨')
    if cfg.get('baseline_id') == 'h3-first-v1' and any(cfg.get(k, v) != v for k, v in DEFAULTS.items()):
        raise CanvasError('实测基准已锁定声音、水印与优化方式，请切换手动配置后调整')
