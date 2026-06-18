"""A small mock web app exercising a wide variety of UI features.

Everything is held in memory, so a restart resets the state. The point of this
app is to give a browser-automation agent a realistic-feeling surface to play
with: forms, uploads, text blocks, modals/popups, toasts, tables and multiple
pages that can be opened side by side in separate tabs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Acme Mock Console")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# --------------------------------------------------------------------------- #
# In-memory "database"
# --------------------------------------------------------------------------- #
@dataclass
class Project:
    id: str
    name: str
    owner: str
    status: str  # active | paused | archived
    progress: int  # 0-100
    created: str


@dataclass
class Upload:
    id: str
    filename: str
    size: int
    content_type: str
    uploaded: str


@dataclass
class Message:
    id: str
    name: str
    email: str
    topic: str
    body: str
    created: str


@dataclass
class Store:
    projects: list[Project] = field(default_factory=list)
    uploads: list[Upload] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _seed() -> Store:
    store = Store()
    store.projects = [
        Project("p-1001", "Orion Dashboard", "Ada Lovelace", "active", 72, "2026-05-02"),
        Project("p-1002", "Helios Billing", "Alan Turing", "paused", 30, "2026-05-11"),
        Project("p-1003", "Nimbus Search", "Grace Hopper", "active", 95, "2026-04-21"),
        Project("p-1004", "Vega Analytics", "Katherine Johnson", "archived", 100, "2026-03-15"),
    ]
    store.messages = [
        Message(
            "m-1",
            "Margaret Hamilton",
            "margaret@example.com",
            "billing",
            "Could you double-check the invoice for the Helios project? The totals look off.",
            "2026-06-15 09:14:02",
        ),
    ]
    return store


STORE = _seed()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _stats() -> dict[str, int]:
    return {
        "projects": len(STORE.projects),
        "active": sum(1 for p in STORE.projects if p.status == "active"),
        "uploads": len(STORE.uploads),
        "messages": len(STORE.messages),
    }


def _ctx(request: Request, **extra) -> dict:
    ctx = {"request": request, "stats": _stats(), "now": _now()}
    ctx.update(extra)
    return ctx


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        "dashboard.html", _ctx(request, projects=STORE.projects)
    )


@app.get("/projects", response_class=HTMLResponse)
def projects(request: Request):
    return templates.TemplateResponse(
        "projects.html", _ctx(request, projects=STORE.projects)
    )


@app.post("/projects/new")
def create_project(
    name: str = Form(...),
    owner: str = Form(...),
    status: str = Form("active"),
    progress: int = Form(0),
):
    STORE.projects.append(
        Project(
            id=f"p-{uuid.uuid4().hex[:4]}",
            name=name,
            owner=owner,
            status=status,
            progress=max(0, min(100, progress)),
            created=datetime.now().strftime("%Y-%m-%d"),
        )
    )
    return RedirectResponse("/projects?created=1", status_code=303)


@app.post("/projects/{project_id}/delete")
def delete_project(project_id: str):
    STORE.projects = [p for p in STORE.projects if p.id != project_id]
    return RedirectResponse("/projects?deleted=1", status_code=303)


@app.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request):
    return templates.TemplateResponse(
        "upload.html", _ctx(request, uploads=list(reversed(STORE.uploads)))
    )


@app.post("/upload")
async def do_upload(file: UploadFile, label: str = Form("")):
    contents = await file.read()
    STORE.uploads.append(
        Upload(
            id=f"u-{uuid.uuid4().hex[:6]}",
            filename=label or file.filename or "untitled",
            size=len(contents),
            content_type=file.content_type or "application/octet-stream",
            uploaded=_now(),
        )
    )
    return RedirectResponse("/upload?uploaded=1", status_code=303)


@app.get("/contact", response_class=HTMLResponse)
def contact_page(request: Request):
    return templates.TemplateResponse(
        "contact.html", _ctx(request, messages=list(reversed(STORE.messages)))
    )


@app.post("/contact")
def submit_contact(
    name: str = Form(...),
    email: str = Form(...),
    topic: str = Form("general"),
    body: str = Form(...),
    subscribe: str = Form("off"),
):
    STORE.messages.append(
        Message(
            id=f"m-{uuid.uuid4().hex[:6]}",
            name=name,
            email=email,
            topic=topic,
            body=body,
            created=_now(),
        )
    )
    return RedirectResponse("/contact?sent=1", status_code=303)


@app.get("/components", response_class=HTMLResponse)
def components(request: Request):
    return templates.TemplateResponse("components.html", _ctx(request))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
