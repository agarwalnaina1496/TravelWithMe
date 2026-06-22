from fastapi import APIRouter


router = APIRouter(tags=["Health"])


@router.get("/")
async def root():
    return {"service": "travelwithme", "status": "ok"}


@router.get("/health")
async def health():
    return {"status": "ok"}
