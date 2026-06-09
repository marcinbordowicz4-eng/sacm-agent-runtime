import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import AgentState

NUM_STATES = 7


class StateService:
    def __init__(self, db: Session):
        self.db = db

    def get_belief_state(self, task_id: str) -> list[float]:
        state = (
            self.db.query(AgentState)
            .filter(AgentState.task_id == task_id)
            .order_by(AgentState.created_at.desc())
            .first()
        )
        if state and state.belief_state:
            return list(state.belief_state)
        return [1.0 / NUM_STATES] * NUM_STATES

    def update_belief_state(self, task_id: str, belief_state: list[float]) -> None:
        entry = AgentState(
            id=str(uuid.uuid4()),
            task_id=task_id,
            belief_state=belief_state,
            created_at=datetime.utcnow(),
        )
        self.db.add(entry)
        self.db.commit()
