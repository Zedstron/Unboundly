from app.infrastructure.persona import PersonaStore
from app.core.prompts import get_prompt


def test_persona_prompt_uses_kwargs_values():
    persona = PersonaStore().get_persona("munazza")
    prompt = get_prompt(
        "persona",
        {
            "name": persona["profile"]["name"],
            "profile": persona["profile"],
            "traits": persona["traits"],
            "mood": "calm and engaged",
            "short_memories": "- likes tea\n- works late",
            "long_memories": "- has a dog",
            "language_style": persona["profile"]["language_style"],
        },
    )

    assert "Name: Munazza" in prompt
    assert "roman_urdu" in prompt


def test_event_prompt_uses_kwargs_values():
    prompt = get_prompt(
        "event_classification",
        {
            "text": "I miss you and want to see you.",
            "allowed_events": ["reassurance", "compliment", "conflict"],
        },
    )

    assert "I miss you and want to see you." in prompt
    assert "reassurance" in prompt
    assert "compliment" in prompt
    assert "conflict" in prompt


def test_unknown_prompt_type_raises_value_error():
    try:
        get_prompt("unknown", {})
        assert False, "Expected ValueError for unknown prompt type"
    except ValueError:
        pass
