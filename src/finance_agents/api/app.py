"""应用工厂。仅在 lifespan 启动时连接存储，import 无业务副作用。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
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
    async def invalid(request: Request, error):
        return JSONResponse(status_code=422, content={"detail": str(error)})

    application.include_router(router)
    web = PROJECT_ROOT / "web"
    if web.exists():
        application.mount("/web", StaticFiles(directory=web), name="web")

        @application.get("/", include_in_schema=False)
        async def home():
            return FileResponse(web / "index.html")

    return application


app = create_app()
