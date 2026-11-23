from queue import Queue

import pytest

from dialogue_tool import douyin_agent


@pytest.mark.parametrize('mode', ['prepare', 'publish', 'cleanup'])
def test_phone_tasks_request_sol_with_medium_reasoning(mode):
    class Client:
        def __init__(self):
            self.events = Queue()
            self.events.put({'method': 'turn/completed', 'params': {'threadId': 'thread', 'turn': {'id': 'turn'}}})
            self.calls = []

        def request(self, method, params):
            self.calls.append((method, params))
            if method == 'thread/start':
                return {'thread': {'id': 'thread'}}
            assert method == 'turn/start'
            return {'turn': {'id': 'turn'}}

    client = Client()
    douyin_agent._run_phone(None, {'mode': mode}, {'slot': '01'}, client, 'test skill')
    (_, thread), (_, turn) = client.calls
    assert thread['model'] == turn['model'] == 'gpt-6.2-sol'
    assert thread['config']['model_reasoning_effort'] == turn['effort'] == 'medium'


def test_publication_stays_independent_of_cleanup_even_for_a_legacy_batch(monkeypatch):
    calls = []

    class Client:
        def close(self):
            pass

    class Batch:
        ended = False

        def cleanup_context(self, batch_id, slot):
            raise AssertionError("a publish batch must not check cleanup")

        def end(self, batch_id, error):
            assert batch_id == "batch"
            assert not error
            self.ended = True

    def run_phone(publish, batch, row, client, instructions):
        calls.append((batch["mode"], row["slot"]))
        assert "phone_cleanup_delete" not in instructions

    monkeypatch.setattr(douyin_agent, "_run_phone", run_phone)
    publish = Batch()
    batch = {"id": "batch", "mode": "publish", "delete_after_publish": True,
             "rows": [{"slot": "01", "status": "ready"}]}
    douyin_agent.run_batch(publish, batch, "fixture", client_factory=lambda *args: Client())
    assert publish.ended
    assert calls == [("publish", "01")]


def test_independent_cleanup_loads_its_own_skill_and_only_uses_selected_sources(monkeypatch):
    cleaned = []

    class Client:
        def close(self):
            pass

    class Batch:
        ended = False

        def cleanup_context(self, batch_id, slot):
            assert batch_id == "cleanup"
            if slot == "01":
                return {"slot": slot, "filename": "IMG_0017.MOV"}
            return None

        def end(self, batch_id, error):
            assert batch_id == "cleanup"
            assert not error
            self.ended = True

    def run_phone(publish, batch, row, client, instructions):
        assert batch["mode"] == "cleanup"
        assert "phone_cleanup_delete" in instructions
        assert "phone_submit" not in instructions
        cleaned.append((row["slot"], row["filename"]))

    monkeypatch.setattr(douyin_agent, "_run_phone", run_phone)
    publish = Batch()
    batch = {"id": "cleanup", "mode": "cleanup", "rows": [
        {"slot": "01", "status": "published"}, {"slot": "02", "status": "published"}]}
    douyin_agent.run_batch(publish, batch, "fixture", client_factory=lambda *args: Client())
    assert publish.ended
    assert cleaned == [("01", "IMG_0017.MOV")]
