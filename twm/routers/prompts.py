from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from ..prompts import load_prompt


router = APIRouter(prefix="/prompts", tags=["Prompts"])


@router.get("/{name}", response_class=PlainTextResponse)
async def get_prompt(name: str) -> str:
    try:
        return load_prompt(name.lower())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{name}/json")
async def get_prompt_json(name: str) -> dict[str, str]:
    try:
        return {"prompt": load_prompt(name.lower())}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
