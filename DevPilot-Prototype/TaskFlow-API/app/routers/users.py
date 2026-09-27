from fastapi import APIRouter
from app.database import get_connection
from app.models import User, UserCreate

router = APIRouter()

@router.post("/")
def create_user(user: UserCreate):
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO users (username, role) VALUES (?, ?)",
        (user.username, user.role),
    )
    conn.commit()
    user_id = cur.lastrowid
    conn.close()
    return {"id": user_id, "username": user.username, "role": user.role}

@router.get("/")
def list_users():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM users").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@router.get("/{user_id}")
def get_user(user_id: int):
    # BUG: same unchecked-lookup shape as tasks.get_task -- fetchone() with
    # no None check before dict(row).
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row)

@router.put("/{user_id}/role")
def set_role(user_id: int, role: str):
    conn = get_connection()
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
    conn.commit()
    conn.close()
    return {"id": user_id, "role": role}
