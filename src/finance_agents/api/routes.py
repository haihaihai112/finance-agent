"""同源工作台 API，身份仅来自服务端签发的会话凭据。"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from finance_agents.contracts.requests import (
    CopyRunRequest,
    ConsentRequest,
    QueryRequest,
    ResumeRequest,
)
from finance_agents.contracts.results import Intent, WorkflowResponse
from finance_agents.workflow.topology import AGENTS, BRANCH_STEPS, MCP_SERVICES

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


def service(request: Request):
    return request.app.state.service


def authenticated(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)
):
    if credentials is None:
        raise HTTPException(401, "请先创建会话")
    try:
        identity = service(request).store.authenticate(credentials.credentials)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    return identity, credentials.credentials


@router.get("/health")
async def health(request: Request):
    settings = service(request).settings
    return {
        "status": "ok",
        "stage": "personal_project",
        "mcp_transport": settings.mcp_transport,
        "model_provider": "deepseek",
        "model_configured": bool(settings.deepseek_api_key and settings.llm_enabled),
        "credit_mode": "mock",
        "external_services_verified": False,
    }


@router.get("/api/v1/architecture")
async def architecture():
    return {
        "intents": [intent.value for intent in Intent],
        "agents": list(AGENTS),
        "mcp_services": list(MCP_SERVICES),
        "branches": {intent.value: list(steps) for intent, steps in BRANCH_STEPS.items()},
    }


@router.post("/api/v1/sessions")
def sessions(request: Request):
    return service(request).store.create_session()


@router.post("/api/v1/queries", response_model=WorkflowResponse)
async def query(body: QueryRequest, request: Request, auth=Depends(authenticated)):
    return await service(request).start(body, *auth)


@router.post("/api/v1/runs/{run_id}/resume", response_model=WorkflowResponse)
async def resume(run_id: str, body: ResumeRequest, request: Request, auth=Depends(authenticated)):
    return await service(request).resume(run_id, body, *auth)


@router.post("/api/v1/runs/{run_id}/copy", response_model=WorkflowResponse)
async def copy_run(
    run_id: str, body: CopyRunRequest, request: Request, auth=Depends(authenticated)
):
    return await service(request).copy_loan(run_id, body, auth[0])


@router.get("/api/v1/requests/{request_id}")
def request_status(request_id: str, request: Request, auth=Depends(authenticated)):
    return service(request).store.get_request(auth[0]["user_id"], request_id)


@router.post("/api/v1/requests/{request_id}/interrupt")
def interrupt_request(request_id: str, request: Request, auth=Depends(authenticated)):
    return service(request).store.interrupt_request(auth[0]["user_id"], request_id)


@router.get("/api/v1/runs")
def list_runs(request: Request, auth=Depends(authenticated)):
    from finance_agents.workflow.state import SessionState

    instance = service(request)
    return [
        instance.response(SessionState.model_validate(r))
        for r in instance.store.list_runs(auth[0]["user_id"])
    ]


@router.get("/api/v1/runs/{run_id}", response_model=WorkflowResponse)
def run(run_id: str, request: Request, auth=Depends(authenticated)):
    instance = service(request)
    return instance.response(instance.store.get_run(run_id, auth[0]["user_id"]))


@router.get("/api/v1/runs/{run_id}/events")
def events(run_id: str, request: Request, auth=Depends(authenticated)):
    return service(request).store.get_events(run_id, auth[0]["user_id"])


@router.post("/api/v1/runs/{run_id}/cancel")
async def cancel(run_id: str, request: Request, auth=Depends(authenticated)):
    return await service(request).cancel(run_id, auth[0])


@router.post("/api/v1/runs/{run_id}/consent")
def consent(run_id: str, body: ConsentRequest, request: Request, auth=Depends(authenticated)):
    instance = service(request)
    user_id = auth[0]["user_id"]
    state = instance.store.get_run(run_id, user_id)
    if body.granted and state.status != "waiting_user":
        raise ValueError("仅等待用户确认的运行可以获得新授权")
    # Revocation covers all previously issued references for this user/run.
    instance.store.revoke_run_consents(user_id, run_id)
    reference = instance.store.grant_consent(user_id, run_id) if body.granted else None
    instance.store.append_event(run_id, "consent", {"granted": body.granted})
    return {"consent_ref": reference, "granted": body.granted, "data_mode": "mock"}


@router.post("/api/v1/documents")
async def upload(file: UploadFile, request: Request, auth=Depends(authenticated)):
    instance = service(request)
    content = await file.read(instance.settings.max_upload_bytes + 1)
    if len(content) > instance.settings.max_upload_bytes:
        raise HTTPException(413, "文件超出大小限制")
    return await asyncio.to_thread(
        instance.documents.save, auth[0]["user_id"], file.filename, content, file.content_type
    )


@router.get("/api/v1/documents/{document_id}")
def document(document_id: str, request: Request, auth=Depends(authenticated)):
    info = service(request).documents.get_info(auth[0]["user_id"], document_id)
    info.pop("extraction_json", None)
    return info


@router.get("/api/v1/documents/{document_id}/download")
def download(document_id: str, request: Request, auth=Depends(authenticated)):
    instance = service(request)
    info = instance.documents.get_info(auth[0]["user_id"], document_id)
    return Response(
        instance.documents.read(auth[0]["user_id"], document_id), media_type=info["media_type"]
    )


@router.post("/api/v1/documents/{document_id}/extract")
@router.get("/api/v1/documents/{document_id}/extraction")
async def extract(document_id: str, request: Request, auth=Depends(authenticated)):
    return await service(request).document_service.extract(auth[0]["user_id"], document_id)


@router.delete("/api/v1/consents/{consent_ref}")
def revoke(consent_ref: str, request: Request, auth=Depends(authenticated)):
    service(request).store.revoke_consent(consent_ref, auth[0]["user_id"])
    return {"granted": False, "consent_ref": None}


@router.post("/api/v1/demo")
async def demo(request: Request, auth=Depends(authenticated)):
    instance = service(request)
    result = await asyncio.to_thread(instance.demo, auth[0]["user_id"])
    await instance.market.load_verified_snapshot()
    result["market_data_mode"] = "real"
    result["market_frequency"] = "daily_published_nav"
    return result
