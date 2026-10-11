from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import analysis, catalog as catalog_router
from app.services import live
from app.services.catalog import NotFoundError


@asynccontextmanager
async def lifespan(_: FastAPI):
    live.warm()  # background fetch so live rates are ready soon after a cold start
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Should-Costing API",
    description=(
        "Estimate part cost from a drawing (PDF) or CAD model: material cost and "
        "labour cost for a selected location, plus the machines needed to make it."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(NotFoundError)
async def not_found_handler(_: Request, exc: NotFoundError):
    return JSONResponse(status_code=404, content={"detail": exc.args[0]})


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}


app.include_router(catalog_router.router)
app.include_router(analysis.router)
