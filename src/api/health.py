from fastapi import APIRouter

router = APIRouter()


@router.get("", operation_id="health")
async def health():
    return {"status": "ok"}
