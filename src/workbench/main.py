from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from workbench.config import settings
from workbench.routers.cases import router as cases_router
from workbench.routers.demo import router as demo_router

app = FastAPI(
    title="Reviewer Workbench API",
    version="1.0.0",
    description=(
        "Read-only API serving the Reviewer Workbench UI. "
        "Section 12 of the system design spec is the source of truth for endpoint semantics."
    ),
)

_origins = [o.strip() for o in (settings.cors_origins or "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False if _origins == ["*"] else True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(cases_router)
app.include_router(demo_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
