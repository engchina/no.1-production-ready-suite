"""業務ルーターの集約（/api 配下に include される）。"""

from fastapi import APIRouter

from app.features.nl2sql.router import router as nl2sql_router

api_router = APIRouter()
api_router.include_router(nl2sql_router)
