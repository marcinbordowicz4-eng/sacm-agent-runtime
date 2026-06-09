import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from sacm.infrastructure.db.models import Agent
from sacm.infrastructure.db.session import get_db
from sacm.schemas.agent import AgentCreate, AgentRead, AgentUpdate

router = APIRouter()


@router.post("/register", response_model=AgentRead)
def register_agent(payload: AgentCreate, db: Session = Depends(get_db)) -> AgentRead:
    agent = Agent(
        id=str(uuid.uuid4()),
        name=payload.name,
        role=payload.role,
        provider=payload.provider,
        model_name=payload.model_name,
        cost_weight=payload.cost_weight,
        quality_score=payload.quality_score,
        latency_score=payload.latency_score,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return AgentRead.model_validate(agent)


@router.get("", response_model=list[AgentRead])
def list_agents(db: Session = Depends(get_db)) -> list[AgentRead]:
    return [AgentRead.model_validate(agent) for agent in db.query(Agent).order_by(Agent.name).all()]


@router.get("/{agent_id}", response_model=AgentRead)
def get_agent(agent_id: str, db: Session = Depends(get_db)) -> AgentRead:
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return AgentRead.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentRead)
def update_agent(agent_id: str, payload: AgentUpdate, db: Session = Depends(get_db)) -> AgentRead:
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(agent, key, value)
    agent.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(agent)
    return AgentRead.model_validate(agent)
