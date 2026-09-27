from fastapi import APIRouter
from app.database import get_connection
from app.models import Task, TaskCreate

router = APIRouter()


@router.post("/", response_model=Task)
def create_task(task: TaskCreate):
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO tasks (title, owner) VALUES (?, ?)",
        (task.title, task.owner),
    )
    conn.commit()
    task_id = cur.lastrowid
    conn.close()
    return {"id": task_id, "title": task.title, "owner": task.owner, "done": False}


@router.get("/")
def list_tasks():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM tasks").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/{task_id}")
def get_task(task_id: int):
    # BUG: no check for a missing row -- dict(None) raises an unhandled
    # TypeError instead of returning a 404, so a bad id crashes the request.
    conn = get_connection()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return dict(row)


@router.get("/search/by-owner")
def search_by_owner(owner: str):
    # SECURITY: query built with an f-string instead of a parameter binding.
    # `owner` comes straight from the query string, so this is a classic
    # SQL-injection entry point (e.g. owner=' OR '1'='1).
    conn = get_connection()
    query = f"SELECT * FROM tasks WHERE owner = '{owner}'"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.delete("/{task_id}")
def delete_task(task_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return {"deleted": task_id}
