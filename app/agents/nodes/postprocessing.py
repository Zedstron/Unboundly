from app.agents.state import PersonaGraphState


def postprocess_response_node(state: PersonaGraphState) -> dict[str, str]:
    reply = state["agent_response"].response.strip()

    if not reply:
        raise ValueError("Persona model returned an empty response")

    return { "reply": reply }
