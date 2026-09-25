"""業務ルーターの集約（/api 配下に include される）。"""

from fastapi import APIRouter, Depends

from app.api.health import router as health_router
from app.features.nl2sql.ontology_router import router as nl2sql_ontology_router
from app.features.nl2sql.router import persistence_router as nl2sql_persistence_router
from app.features.nl2sql.router import router as nl2sql_router
from app.features.schema.router import router as schema_router
from app.features.settings.router import router as settings_router
from app.security.dependencies import authorize_api_request
from app.security.router import router as security_router

api_router = APIRouter(dependencies=[Depends(authorize_api_request)])
api_router.include_router(health_router)
api_router.include_router(security_router)
api_router.include_router(nl2sql_persistence_router)
api_router.include_router(nl2sql_router)
api_router.include_router(nl2sql_ontology_router)
api_router.include_router(schema_router)
api_router.include_router(settings_router)
