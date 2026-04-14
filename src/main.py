from __future__ import annotations

import uvicorn

from src.app import create_app
from src.settings import settings

app = create_app()

if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,  # important: reload=True breaks multiprocessing
        log_level=settings.log_level.lower(),
    )
