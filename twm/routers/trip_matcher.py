from fastapi import APIRouter

router = APIRouter(tags=["Trip Matcher"])

@router.post("/scout")
async def scout():
    return {"message": "This is the trip matcher scout endpoint."}

@router.post("/meridian")
async def meridian():
    return {"message": "This is the trip matcher meridian endpoint."}
