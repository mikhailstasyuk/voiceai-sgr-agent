from app.agent.session import AgentSession


def _session() -> AgentSession:
    return AgentSession.__new__(AgentSession)


def test_flush_ready_segments_keeps_partial_word_in_remainder():
    s = _session()
    ready, remainder = s._flush_ready_segments("I can help book an appointment. Pleas", char_budget=250)
    assert ready == ["I can help book an appointment."]
    assert remainder == "Pleas"


def test_flush_ready_segments_splits_long_text_on_whitespace():
    s = _session()
    ready, remainder = s._flush_ready_segments("one two three four", char_budget=9)
    assert ready == ["one two", "three"]
    assert remainder == "four"
