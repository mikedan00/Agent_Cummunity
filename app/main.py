from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import agents, db

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    yield


app = FastAPI(title="Agent Community", lifespan=lifespan)


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"posts": db.list_posts(), "agents": agents.load_agents()}
    )


@app.post("/posts")
def create_post(
    background: BackgroundTasks,
    title: str = Form(...),
    body: str = Form(...),
    author: str = Form(...),
):
    post_id = db.create_post(title, body, author)
    background.add_task(agents.reply, post_id, list(agents.load_agents()))
    return RedirectResponse(f"/posts/{post_id}", status_code=303)


@app.get("/posts/{post_id}")
def show_post(request: Request, post_id: int):
    post = db.get_post(post_id)
    if not post:
        raise HTTPException(404, "Post not found")
    return templates.TemplateResponse(
        request,
        "post.html",
        {"post": post, "comments": db.list_comments(post_id), "agents": agents.load_agents()},
    )


@app.post("/posts/{post_id}/comments")
def create_comment(
    background: BackgroundTasks,
    post_id: int,
    body: str = Form(...),
    author: str = Form(...),
):
    if not db.get_post(post_id):
        raise HTTPException(404, "Post not found")
    db.add_comment(post_id, body, author, is_agent=False)
    mentioned = agents.mentioned_agents(body, agents.load_agents())
    if mentioned:
        background.add_task(agents.reply, post_id, mentioned)
    return RedirectResponse(f"/posts/{post_id}", status_code=303)
