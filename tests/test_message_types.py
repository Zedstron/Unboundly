"""Tests for the structured (text/reply/reaction) message agent output."""
import pytest

from app.domain.models import AgentResponse
from app.infrastructure.ai import parse_agent_response
from app.agents.nodes.postprocessing import postprocess_response_node
from app.services.bridges.models import SocialMessage
from app.services.conversation import ConversationService


def test_text_response_requires_text():
    response = AgentResponse(type="text", text="hello")
    assert response.text == "hello"
    assert response.reaction is None

    with pytest.raises(Exception):
        AgentResponse(type="text", text="   ")


def test_reply_response_requires_text():
    response = AgentResponse(type="reply", text="answering that")
    assert response.text == "answering that"

    with pytest.raises(Exception):
        AgentResponse(type="reply", text="")


def test_reaction_response_requires_emoji_and_drops_text():
    response = AgentResponse(type="reaction", text="ignored", reaction="😂")
    assert response.reaction == "😂"
    assert response.text is None

    with pytest.raises(Exception):
        AgentResponse(type="reaction", reaction="")


@pytest.mark.parametrize("kind", ["voice", "image"])
def test_unimplemented_types_are_rejected(kind):
    with pytest.raises(Exception):
        AgentResponse(type=kind, text="hi")


def test_parse_agent_response_json_envelope():
    parsed = parse_agent_response('{"type": "reply", "text": "yes", "reaction": null}')
    assert parsed.type == "reply"
    assert parsed.text == "yes"


def test_parse_agent_response_tolerates_bare_text():
    parsed = parse_agent_response("just some words")
    assert parsed.type == "text"
    assert parsed.text == "just some words"


def test_postprocess_allows_empty_text_for_reaction():
    state = {"agent_response": AgentResponse(type="reaction", reaction="❤️")}
    assert postprocess_response_node(state) == {"reply": ""}


def test_postprocess_rejects_empty_text_for_text():
    # Constructing an empty text response is itself invalid, so simulate a
    # model that slipped through with whitespace only.
    obj = AgentResponse.model_construct(type="text", text="   ", reaction=None)
    with pytest.raises(ValueError):
        postprocess_response_node({"agent_response": obj})


def _inbound_reply() -> SocialMessage:
    return SocialMessage(
        session="persona",
        message_id="m-2",
        message_type="chat",
        chat_id="conv-1",
        item_id="i-2",
        sender_id="conv-1",
        sender_name="Tester",
        text="what did you mean?",
        provider="whatsapp",
        reply_to_message_id="m-1",
        reply_to_text="see you tomorrow",
    )


def test_social_message_is_reply():
    assert _inbound_reply().is_reply is True
    plain = _inbound_reply()
    plain.reply_to_message_id = None
    plain.reply_to_text = None
    assert plain.is_reply is False


def test_reply_task_payload_carries_reply_context():
    payload = ConversationService._reply_task_payload(_inbound_reply(), 5)
    assert payload["reply_to_message_id"] == "m-1"
    assert payload["reply_to_text"] == "see you tomorrow"
    assert payload["reply_to_external_id"] == "m-2"
    assert "text" not in payload
