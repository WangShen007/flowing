from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    ai_ppt,
    auth,
    bot_channels,
    chat,
    feishu_auth,
    health,
    lark_setup,
    models,
    scenarios,
    scheduled_tasks,
    templates,
    workflow_memories,
)
from app.config import get_settings
from app.core.scheduled_tasks import scheduled_task_runner
from app.core.bot_runner import bot_runner

ROOT_DIR = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    print(f"{settings.APP_NAME} starting...")
    scheduled_task_runner.start()
    bot_runner.start()
    yield
    await bot_runner.stop()
    await scheduled_task_runner.stop()
    print(f"{settings.APP_NAME} shutting down...")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        description="A standalone web UI and API for Lark/Feishu CLI automation.",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        if request.url.path.startswith(f"{settings.API_PREFIX}/auth/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(health.router, tags=["Health"])
    app.include_router(auth.router, prefix=settings.API_PREFIX, tags=["Auth"])
    app.include_router(bot_channels.router, prefix=settings.API_PREFIX, tags=["Bot Channels"])
    app.include_router(feishu_auth.router, prefix=settings.API_PREFIX, tags=["Feishu Auth"])
    app.include_router(chat.router, prefix=settings.API_PREFIX, tags=["Chat"])
    app.include_router(scenarios.router, prefix=settings.API_PREFIX, tags=["Scenarios"])
    app.include_router(templates.router, prefix=settings.API_PREFIX, tags=["Templates"])
    app.include_router(scheduled_tasks.router, prefix=settings.API_PREFIX, tags=["Scheduled Tasks"])
    app.include_router(workflow_memories.router, prefix=settings.API_PREFIX, tags=["Workflow Memories"])
    app.include_router(lark_setup.router, prefix=settings.API_PREFIX, tags=["Lark CLI Setup"])
    app.include_router(models.router, prefix=settings.API_PREFIX, tags=["Model Config"])
    app.include_router(ai_ppt.router, prefix=settings.API_PREFIX, tags=["AI PPT"])
    _mount_frontend(app)
    return app


def _frontend_dist_candidates() -> list[Path]:
    settings = get_settings()
    candidates: list[Path] = []
    if settings.FRONTEND_DIST_DIR.strip():
        candidates.append(Path(settings.FRONTEND_DIST_DIR).expanduser())
    candidates.extend(
        [
            ROOT_DIR / "frontend" / "dist",
            ROOT_DIR / "backend" / "static",
            Path(__file__).resolve().parent / "static",
        ]
    )
    return candidates


def _mount_frontend(app: FastAPI) -> None:
    dist_dir = next((path.resolve() for path in _frontend_dist_candidates() if (path / "index.html").exists()), None)
    if not dist_dir:
        return

    assets_dir = dist_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        requested = (dist_dir / full_path).resolve()
        if requested.is_file() and dist_dir in requested.parents:
            return FileResponse(requested)
        return FileResponse(dist_dir / "index.html")


app = create_app()
