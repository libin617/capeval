"""评测用例库 API(FR-02)。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import require_writer
from app.core.database import get_db
from app.models import CaseSetItem, CaseSetVersion, TestCase, User
from app.schemas import TestCaseCreate, TestCaseOut, TestCaseUpdate
from app.services import build_detector, self_check

router = APIRouter(prefix="/api/testcases", tags=["testcases"])

TARGET_MODES = {"host", "docker", "any"}


def _check_execution_env(env_def: dict | None) -> None:
    """用例声明目标执行方式后校验:docker/any 必须自带镜像,host 不要求。"""
    env_def = env_def or {}
    target = str(env_def.get("target_isolation") or "any")
    if target not in TARGET_MODES:
        raise HTTPException(422, f"target_isolation 取值无效:{target}(应为 host/docker/any)")
    if target != "host" and not str(env_def.get("docker_image") or "").strip():
        raise HTTPException(422, "执行方式为 docker/any 的评测用例必须填写镜像(如 node:20-slim)")


@router.get("", response_model=list[TestCaseOut])
def list_testcases(
    status: str = Query("active"), tag: str | None = Query(None),
    difficulty: str | None = Query(None), q: str | None = Query(None),
    db: Session = Depends(get_db),
) -> list[TestCase]:
    query = db.query(TestCase).filter(TestCase.status == status)
    if difficulty:
        query = query.filter(TestCase.difficulty == difficulty)
    if q:
        query = query.filter(TestCase.title.like(f"%{q.strip()}%"))
    cases = query.order_by(TestCase.id.desc()).all()
    if tag:
        cases = [c for c in cases if tag in (c.tags or [])]
    return cases


@router.post("", response_model=TestCaseOut, status_code=201)
def create_testcase(payload: TestCaseCreate, db: Session = Depends(get_db),
                    user: User = Depends(require_writer)) -> TestCase:
    data = payload.model_dump()
    _check_execution_env(data.get("env_def"))
    case = TestCase(**data)
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


@router.get("/{case_id}", response_model=TestCaseOut)
def get_testcase(case_id: int, db: Session = Depends(get_db)) -> TestCase:
    case = db.get(TestCase, case_id)
    if case is None:
        raise HTTPException(404, "用例不存在")
    return case


@router.patch("/{case_id}", response_model=TestCaseOut)
def update_testcase(case_id: int, payload: TestCaseUpdate, db: Session = Depends(get_db),
                    user: User = Depends(require_writer)) -> TestCase:
    case = db.get(TestCase, case_id)
    if case is None:
        raise HTTPException(404, "用例不存在")
    data = payload.model_dump(exclude_unset=True)
    _check_execution_env(data.get("env_def", case.env_def))
    if "task_script" in data and data["task_script"] is not None:
        # 复用创建校验:轮次连续 + 多轮意图标签
        TestCaseCreate(
            title=data.get("title", case.title), task_script=data["task_script"],
            acceptance=data.get("acceptance", case.acceptance),
            tags=data.get("tags", case.tags),
        )
    for k, v in data.items():
        setattr(case, k, v)
    db.commit()
    db.refresh(case)
    return case


@router.delete("/{case_id}", status_code=204)
def delete_testcase(case_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_writer)) -> None:
    """被已发布用例集引用的用例禁止物理删除,仅可归档(FR-02 异常规则)。"""
    case = db.get(TestCase, case_id)
    if case is None:
        raise HTTPException(404, "用例不存在")
    published_ref = (
        db.query(CaseSetItem).join(CaseSetVersion, CaseSetItem.version_id == CaseSetVersion.id)
        .filter(CaseSetItem.testcase_id == case_id, CaseSetVersion.status == "published").first()
    )
    if published_ref:
        case.status = "archived"
        db.commit()
        return
    db.delete(case)
    db.commit()


@router.post("/{case_id}/self-check")
def self_check_case(case_id: int, db: Session = Depends(get_db),
                    user: User = Depends(require_writer)) -> dict:
    """用例自检:还原仓库快照并执行环境准备 + 自动化验收命令,验证用例本身可跑通。"""
    case = db.get(TestCase, case_id)
    if case is None:
        raise HTTPException(404, "用例不存在")
    return self_check.run_self_check(case)


@router.post("/detect-build")
def detect_build(body: dict, db: Session = Depends(get_db),
                 user: User = Depends(require_writer)) -> dict:
    """根据仓库快照文件检测构建系统,返回推荐的安装命令与验收命令示例。

    body: {"repo_snapshot": {"files": {"pom.xml": "...", ...}}} 或直接 {"files": {...}}
    """
    repo = body.get("repo_snapshot", body)
    files = (repo or {}).get("files") or {}
    return build_detector.detect_build_system(files)
