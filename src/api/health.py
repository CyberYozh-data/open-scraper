from fastapi import APIRouter

from src.settings import settings

router = APIRouter()


@router.get("", operation_id="health")
async def health():
    return {
        "status": "ok",
        "workers": settings.workers,
    }
