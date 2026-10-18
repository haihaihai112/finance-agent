"""应用工厂。仅在 lifespan 启动时连接存储，import 无业务副作用。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from finance_agents.api.routes import router
from finance_agents.core.settings import PROJECT_ROOT, Settings
from finance_agents.workflow.service import WorkflowService


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        application.state.service = WorkflowService(settings or Settings.from_env())
        yield
        application.state.service.engine.dispose()

    application = FastAPI(title="金融多智能体个人项目", version="1.0.0", lifespan=lifespan)

    @application.exception_handler(PermissionError)
    async def forbidden(request: Request, error):
        return JSONResponse(status_code=403, content={"detail": str(error)})

    @application.exception_handler(LookupError)
    async def missing(request: Request, error):
        return JSONResponse(status_code=404, content={"detail": str(error)})

    @application.exception_handler(ValueError)
    async def conflict(request: Request, error):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @application.exception_handler(TypeError)
    @application.exception_handler(ValidationError)
    @application.exception_handler(RequestValidationError)
    async def invalid(request: Request, error):
        detail = "请求或表单格式不正确，请按字段示例修正后重新提交；本次未保存修改。"
        if isinstance(error, (ValidationError, RequestValidationError)):
            from finance_agents.contracts.form_hints import LABELS, form_hints
            from finance_agents.contracts.requests import (
                QueryRequest,
                ResumeRequest,
                ConsentRequest,
                CopyRunRequest,
            )
            from finance_agents.workflow.service import FORMS

            model = next(
                (
                    model
                    for _, model in FORMS.values()
                    if model.__name__ == getattr(error, "title", None)
                ),
                None,
            )
            known_names = set(LABELS) | {"body", "query", "path", "amount", "currency"}
            for request_model in (QueryRequest, ResumeRequest, ConsentRequest, CopyRunRequest):
                known_names.update(request_model.model_fields)
            return JSONResponse(
                status_code=422,
                content={
                    "detail": detail,
                    "errors": [
                        {
                            "field": ".".join(
                                str(part)
                                if isinstance(part, int) or part in known_names
                                else "unknown"
                                for part in item["loc"]
                            ),
                            "type": item["type"],
                        }
                        for item in error.errors()
                    ],
                    "field_hints": form_hints(model) if model else {},
                },
            )
        return JSONResponse(
            status_code=422, content={"detail": detail, "errors": [], "field_hints": {}}
        )

    application.include_router(router)
    web = PROJECT_ROOT / "web"
    if web.exists():
        application.mount("/web", StaticFiles(directory=web), name="web")

        @application.get("/", include_in_schema=False)
        async def home():
            return FileResponse(web / "index.html")

    return application


app = create_app()
