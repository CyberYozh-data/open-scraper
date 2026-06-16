from fastapi import APIRouter

from .health import router as health_router
from .presets import router as presets_router
from .proxies import router as proxies_router
from .scrape import router as scrape_router
from .scrape_preset import router as scrape_preset_router
from .search import router as search_router
from .sessions import router as sessions_router


router = APIRouter(prefix="/api/v1", tags=["api"])


router.include_router(health_router, prefix="/health")
# Mount the more specific /scrape/preset prefix before /scrape so the
# preset routes are never shadowed by /scrape/{job_id}.
router.include_router(scrape_preset_router, prefix="/scrape/preset")
router.include_router(scrape_router, prefix="/scrape")
router.include_router(proxies_router, prefix="/proxies")
router.include_router(presets_router, prefix="/presets")
router.include_router(search_router, prefix="/search")
router.include_router(sessions_router, prefix="/sessions")
