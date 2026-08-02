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

    def is_current(
        self,
        task_id: str,
        *,
        expected_status: str,
        expected_updated_at: datetime,
    ) -> bool:
        return (
            self.db.query(Task.id)
            .filter(
                Task.id == task_id,
                Task.status == expected_status,
                Task.updated_at == expected_updated_at,
            )
            .first()
            is not None
        )

    def mark_done(
        self,
        task_id: str,
        *,
        expected_status: str | None = None,
        expected_updated_at: datetime | None = None,
    ) -> bool:
        return self.update_status(
            task_id,
            "done",
            expected_status=expected_status,
            expected_updated_at=expected_updated_at,
        )

    def update_status(
        self,
        task_id: str,
        status: str,
        *,
        expected_status: str | None = None,
        expected_updated_at: datetime | None = None,
    ) -> bool:
        query = self.db.query(Task).filter(Task.id == task_id)
        if expected_status is not None:
            query = query.filter(Task.status == expected_status)
        if expected_updated_at is not None:
            query = query.filter(Task.updated_at == expected_updated_at)
        updated = query.update(
            {Task.status: status, Task.updated_at: datetime.utcnow()},
            synchronize_session=False,
        )
        self.db.commit()
        self.db.expire_all()
        return bool(updated)
