from fastapi import APIRouter

from app.api.v1 import admin, auth, cfo, claims, erp, messaging, risk, scheduler

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(cfo.router, prefix="/cfo", tags=["cfo"])
api_router.include_router(erp.router, prefix="/erp", tags=["erp-gantt"])
api_router.include_router(scheduler.router, prefix="/scheduler", tags=["scheduler"])
api_router.include_router(risk.router, prefix="/risk", tags=["risk-engine"])
api_router.include_router(messaging.router, prefix="/messages", tags=["messaging"])
api_router.include_router(claims.router, prefix="/claims", tags=["claims"])
