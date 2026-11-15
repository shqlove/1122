"""Validate complete per-frame character screening before adaptive expansion."""

from jsonschema import ValidationError, validate

from .providers import ProviderError


PROBE_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'frames': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'frame': {'type': 'integer'},
                    'status': {'type': 'string', 'enum': ['matched', 'not_matched', 'uncertain']},
                    'reason': {'type': 'string'},
                },
                'required': ['frame', 'status', 'reason'],
            },
        },
    },
    'required': ['frames'],
}


def validate_probe(result: dict, count: int) -> dict:
    """Reject incomplete classification so missing frames cannot be silently excluded."""
    try:
        validate(result, PROBE_SCHEMA)
    except ValidationError as exc:
        raise ProviderError(f'角色定位结果格式不符：{exc.message}') from exc
    indices = [item['frame'] for item in result['frames']]
    if any(type(index) is not int for index in indices) or sorted(indices) != list(range(count)):
        raise ProviderError('角色定位结果遗漏、重复或引用不存在的候选帧')
    return result
