from fastapi import APIRouter

from .health import router as health_router
from .proxies import router as proxies_router
from .scrape import router as scrape_router


router = APIRouter(prefix="/api/v1", tags=["api"])


router.include_router(health_router, prefix="/health")
router.include_router(scrape_router, prefix="/scrape")
router.include_router(proxies_router, prefix="/proxies")
