import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import Task
from sacm.schemas.task import TaskCreate


class TaskService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, data: TaskCreate) -> Task:
        task = Task(
            id=str(uuid.uuid4()),
            title=data.title,
            description=data.description,
            target_repo_path=data.target_repo_path,
            status="pending",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def get(self, task_id: str) -> Task | None:
        return self.db.query(Task).filter(Task.id == task_id).first()

    def mark_done(self, task_id: str) -> None:
        self.update_status(task_id, "done")

    def update_status(self, task_id: str, status: str) -> None:
        task = self.get(task_id)
        if task:
            task.status = status
            task.updated_at = datetime.utcnow()
            self.db.commit()
