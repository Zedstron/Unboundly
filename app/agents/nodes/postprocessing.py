from app.agents.state import PersonaGraphState


def postprocess_response_node(state: PersonaGraphState) -> dict[str, str]:
    agent = state["agent_response"]

    if agent.type == "reaction":
        # A reaction carries no text; the worker sends the emoji instead.
        return { "reply": "" }

    reply = (agent.text or "").strip()

    if not reply:
        raise ValueError("Persona model returned an empty response")

    return { "reply": reply }
