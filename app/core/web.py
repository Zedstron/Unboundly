from pathlib import Path
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parents[2]
UI_DIR = BASE_DIR / "ui"

templates = Jinja2Templates(
    directory=str(UI_DIR / "templates")
)