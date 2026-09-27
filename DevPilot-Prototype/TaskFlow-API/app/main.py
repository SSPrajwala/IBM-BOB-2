from fastapi import FastAPI
from app.database import init_db
from app.routers import tasks, users

app = FastAPI(title="TaskFlow API")

init_db()

app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
app.include_router(users.router, prefix="/users", tags=["users"])


@app.get("/health")
def health():
    return {"status": "ok"}
