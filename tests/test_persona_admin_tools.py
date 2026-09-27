from scripts.persona_tools import filter_keys_for_persona, format_conversation_rows


def test_filter_keys_for_persona_keeps_only_matching_persona_keys():
    keys = [
        "persona:memory:short:munazzaabc",
        "persona:memory:short:ali123",
        "persona:memory:short:munazzaxyz",
        "persona:memory:long:index:munazzaabc",
        "persona:memory:long:index:ali123",
    ]

    matched = filter_keys_for_persona(keys, "munazza")

    assert matched == [
        "persona:memory:short:munazzaabc",
        "persona:memory:short:munazzaxyz",
        "persona:memory:long:index:munazzaabc",
    ]


def test_format_conversation_rows_returns_human_readable_summary():
    rows = [
        {"conversation_id": "alice", "message_count": 3, "last_message": "Hi there"},
        {"conversation_id": "bob", "message_count": 1, "last_message": "Later"},
    ]

    formatted = format_conversation_rows(rows)

    assert formatted[0].startswith("alice")
    assert "3" in formatted[0]
    assert formatted[1].startswith("bob")
