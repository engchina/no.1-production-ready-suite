"""業務ルーターの集約（/api 配下に include される）。"""

from fastapi import APIRouter

from app.features.agent.router import router as agent_router

api_router = APIRouter()
api_router.include_router(agent_router)
