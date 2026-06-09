from sqlalchemy.orm import Session

from sacm.core.event_service import EventService


class PostgresEventStore:
    def __init__(self, db: Session):
        self.service = EventService(db)

    def append(self, task_id: str, event_type: str, payload: dict) -> None:
        self.service.save(task_id=task_id, event_type=event_type, payload=payload)
