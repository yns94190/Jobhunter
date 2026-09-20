import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.api.routes import router
from app.config import settings
from app.db import engine
from app.seed import seed

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger("jobhunter")
# httpx journalise l'URL complète, clés API comprises : on le passe en WARNING
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Les tables sont créées par Alembic, pas ici. On ne fait que semer les données.
    with Session(engine) as session:
        seed(session)
    logger.info("JobHunter %s démarré", settings.app_version)
    yield


app = FastAPI(title="JobHunter", version=settings.app_version, lifespan=lifespan)
app.include_router(router)

@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    """Sert la page unique du tableau de bord."""
    return FileResponse("frontend/index.html")
