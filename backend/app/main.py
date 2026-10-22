from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, agents, badcases, casesets, config, judge, notifications, reports, runs, stats, testcases
from app.core.config import get_settings
from app.services.scheduler import dispatcher

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    dispatcher.start()
    yield
    dispatcher.stop()


app = FastAPI(title=settings.app_name, version="1.0.0", docs_url="/api/docs", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents.router)
app.include_router(testcases.router)
app.include_router(casesets.router)
app.include_router(config.router)
app.include_router(runs.router)
app.include_router(runs.exec_router)
app.include_router(runs.attr_router)
app.include_router(reports.router)
app.include_router(notifications.router)
app.include_router(admin.router)
app.include_router(stats.router)
app.include_router(judge.router)
app.include_router(badcases.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": "1.0.0"}
