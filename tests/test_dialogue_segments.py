import pytest

from dialogue_tool.dialogue_segments import split_prompts, dialogue_span, template
from dialogue_tool.generation import CanvasError


@pytest.mark.parametrize('speech', ['字' * 181, '字' * 100, '字' * 91 + '。' + '字' * 20])
def test_no_unsafe_character_cut_for_overlong_or_unbroken_dialogue(speech):
    with pytest.raises(CanvasError, match='完整语意'):
        split_prompts('台词：“' + speech + '”原有镜头要求')


def test_semantic_boundary_preserves_dependent_clause_and_every_character():
    speech = '甲' * 35 + '。\n不是为了' + '乙' * 15 + '，\n而是为了' + '丙' * 15 + '。\n' + '丁' * 30
    prompt = '音色@音色，台词：“' + speech + '”，原有镜头要求。'
    parts = split_prompts(prompt)
    text = [p[slice(*dialogue_span(p))] for p in parts]
    assert len(parts) == 2 and ''.join(text) == speech
    assert not text[1].lstrip().startswith(('而是', '不是为了'))
    assert all(template(p) == template(prompt) for p in parts)
