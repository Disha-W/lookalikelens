"""LookalikeLens web app - the pages the business owner sees.

Scaffold only: a working FastAPI + Jinja2 entry point.
The case list, evidence views and owner verdict actions are added
feature by feature on top of this structure.
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.routes.cases import router as cases_router

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="LookalikeLens")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(cases_router)
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/healthz")
def healthz():
    return JSONResponse({"status": "ok"})


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")
