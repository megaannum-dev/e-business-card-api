import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_v1_router
from app.core.config import get_settings
from app.core.firebase import init_firebase
from app.db.mongodb import close_motor_client, get_motor_client
from app.web.privacy import router as privacy_router
from app.web.routes import router as share_web_router
from app.web.terms import router as terms_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    get_motor_client(settings)
    init_firebase(settings)
    logger.info("MongoDB client initialized")
    yield
    await close_motor_client()
    logger.info("MongoDB client closed")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.enable_docs else None,
        redoc_url="/redoc" if settings.enable_docs else None,
        openapi_url="/openapi.json" if settings.enable_docs else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_v1_router, prefix=settings.api_v1_prefix)
    app.include_router(share_web_router)
    app.include_router(privacy_router)
    app.include_router(terms_router)

    return app


app = create_app()
