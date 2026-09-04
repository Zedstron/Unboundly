import json
from typing import Any
from pathlib import Path

class PersonaStore:
    def __init__(self, root="personas") -> None:
        self.path = Path(__file__).resolve().parents[2] / root

    def available_personas(self):
        personas = []

        for x in self.path.glob("*.json"):
            detail = json.load(open(self.path / f"{x.stem}.json", 'r'))
            personas.append({
                "id": detail["id"],
                "name": detail["profile"]["name"],
                "dp": detail["profile"]["dp"]
            })

        return personas

    def get_persona(self, persona: str) -> dict[str, Any]:
        return json.load(open(self.path / f"{persona}.json", 'r'))