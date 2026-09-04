from fastapi import APIRouter, Request
from app.core.web import templates

router = APIRouter()

@router.get("/")
async def chat_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={
            "title": "Persona Chat",
        },
    )