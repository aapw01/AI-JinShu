"""FastAPI application entry point."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import novels, chapters, export, presets, generation, longform

app = FastAPI(title="AI-JinShu API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(novels.router, prefix="/api/novels", tags=["novels"])
app.include_router(chapters.router, prefix="/api/novels", tags=["chapters"])
app.include_router(export.router, prefix="/api/novels", tags=["export"])
app.include_router(presets.router, prefix="/api/presets", tags=["presets"])
app.include_router(generation.router, prefix="/api/novels", tags=["generation"])
app.include_router(longform.router, prefix="/api/novels", tags=["longform"])


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}
