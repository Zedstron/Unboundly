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
        with open(self.path / f"{persona}.json", 'r', encoding='utf-8') as file:
            return json.load(file)

    def save_persona(self, persona: str, detail: dict[str, Any]) -> dict[str, Any]:
        if detail.get("id") != persona:
            raise ValueError("Persona id cannot be changed")

        path = self.path / f"{persona}.json"
        with open(path, 'w', encoding='utf-8') as file:
            json.dump(detail, file, indent=2, ensure_ascii=False)
            file.write('\n')
        return detail