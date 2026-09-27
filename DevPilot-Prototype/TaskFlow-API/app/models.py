from pydantic import BaseModel
from typing import Optional


class TaskCreate(BaseModel):
    title: str
    owner: Optional[str] = None


class Task(TaskCreate):
    id: int
    done: bool = False


class UserCreate(BaseModel):
    username: str
    role: str = "member"


class User(UserCreate):
    id: int
