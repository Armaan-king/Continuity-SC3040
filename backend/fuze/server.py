"""One public origin: /api serves FastAPI; every other route serves the React app."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from .api.app import app as api
from . import env


class SPAFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            # Missing assets must be 404s, not HTML disguised as JavaScript.
            if exc.status_code == 404 and not Path(path).suffix and scope["method"] in {"GET", "HEAD"}:
                return FileResponse(Path(self.directory) / "index.html", headers={"Cache-Control": "no-cache"})
            raise


@asynccontextmanager
async def lifespan(app):
    async with api.router.lifespan_context(api):
        yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/api", api)
frontend = Path(env.get("FUZE_FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if frontend.is_dir():
    app.mount("/", SPAFiles(directory=frontend, html=True))
else:
    @app.get("/{path:path}")
    async def missing_frontend(path: str):
        return JSONResponse({"detail": "Build the frontend with npm run build, or use the Vite development server."}, 503)
