"""Agent 管理 API(FR-01)。权限:建档/编辑=管理员+评测工程师;凭证写入=仅管理员(M6)。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.adapters.registry import ADAPTERS
from app.core.auth import get_current_user, require_admin, require_writer
from app.core.database import get_db
from app.core.security import encrypt_credential, mask_credential
from app.models import Agent, EvaluationRun, User
from app.schemas import AdapterInfo, AgentCreate, AgentOut, AgentUpdate, VerifyResult
from app.services.smoke import run_smoke_check

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/adapters", response_model=list[AdapterInfo])
def list_adapters() -> list[AdapterInfo]:
    return [
        AdapterInfo(key=a.key, name=a.name, access_type=a.access_type,
                    description=a.description, required_config=a.required_config)
        for a in ADAPTERS.values()
    ]


@router.get("", response_model=list[AgentOut])
def list_agents(q: str | None = None, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> list[Agent]:
    query = db.query(Agent)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            (Agent.name.like(like)) | (Agent.version_label.like(like)) | (Agent.adapter_key.like(like))
        )
    return query.order_by(Agent.id.desc()).all()


@router.post("", response_model=AgentOut, status_code=201)
def create_agent(payload: AgentCreate, db: Session = Depends(get_db),
                 user: User = Depends(require_writer)) -> Agent:
    # 凭证写入仅管理员(PRD 7.3);评测工程师可建档但需管理员后续补凭证
    if payload.credential and user.role != "admin":
        raise HTTPException(403, "凭证仅管理员可设置(评测工程师可先建档,由管理员补充凭证)")
    if payload.adapter_key not in ADAPTERS:
        raise HTTPException(400, f"未知适配器: {payload.adapter_key}")
    adapter_cls = ADAPTERS[payload.adapter_key]
    if adapter_cls.access_type != payload.access_type:
        raise HTTPException(400, f"适配器 {payload.adapter_key} 仅支持 {adapter_cls.access_type} 型接入")
    dup = db.query(Agent).filter_by(name=payload.name, version_label=payload.version_label).first()
    if dup:
        raise HTTPException(409, "同名同版本的 Agent 已存在(多版本需分别以版本标识建档)")
    agent = Agent(
        name=payload.name, version_label=payload.version_label, access_type=payload.access_type,
        adapter_key=payload.adapter_key, adapter_config=payload.adapter_config,
        credential_masked=mask_credential(payload.credential),
        credential_encrypted=encrypt_credential(payload.credential),
        resource_limits={"max_case_minutes": 30, "max_turns": 100, **payload.resource_limits},
        cost_params=payload.cost_params, status="unverified",
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent


@router.get("/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent 不存在")
    return agent


@router.patch("/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, payload: AgentUpdate, db: Session = Depends(get_db),
                 user: User = Depends(require_writer)) -> Agent:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent 不存在")
    data = payload.model_dump(exclude_unset=True)
    # 凭证写入仅管理员(PRD 7.3)
    if data.get("credential") and user.role != "admin":
        raise HTTPException(403, "凭证仅管理员可更新")
    if "name" in data or "version_label" in data:
        name = data.get("name", agent.name)
        ver = data.get("version_label", agent.version_label)
        dup = db.query(Agent).filter(Agent.name == name, Agent.version_label == ver, Agent.id != agent_id).first()
        if dup:
            raise HTTPException(409, "同名同版本的 Agent 已存在")
    if "adapter_key" in data and data["adapter_key"] not in ADAPTERS:
        raise HTTPException(400, f"未知适配器: {data['adapter_key']}")
    credential = data.pop("credential", None)
    for k, v in data.items():
        setattr(agent, k, v)
    if credential is not None:
        agent.credential_masked = mask_credential(credential)
        agent.credential_encrypted = encrypt_credential(credential)
    agent.status = "unverified"  # 配置变更后需重新校验
    db.commit()
    db.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=204)
def delete_agent(agent_id: int, db: Session = Depends(get_db),
                 user: User = Depends(require_writer)) -> None:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent 不存在")
    referenced = db.query(EvaluationRun).filter_by(agent_id=agent_id).first()
    if referenced:
        raise HTTPException(409, "该 Agent 已被评测任务引用,禁止删除(可置为不可用)")
    db.delete(agent)
    db.commit()


@router.post("/{agent_id}/verify", response_model=VerifyResult)
def verify_agent(agent_id: int, db: Session = Depends(get_db),
                 user: User = Depends(require_writer)) -> VerifyResult:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent 不存在")
    try:
        passed, message = run_smoke_check(db, agent)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    return VerifyResult(agent_id=agent.id, status=agent.status, passed=passed, message=message)
