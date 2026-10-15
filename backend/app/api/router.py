from fastapi import APIRouter

from app.api.routes import (
    activity,
    ai,
    audit,
    auth,
    database_groups,
    databases,
    health,
    incidents,
    integrations,
    notifications,
    reports,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(databases.router)
api_router.include_router(database_groups.router)
api_router.include_router(audit.router)
api_router.include_router(incidents.router)
api_router.include_router(activity.router)
api_router.include_router(notifications.router)
api_router.include_router(integrations.router)
api_router.include_router(ai.router)
api_router.include_router(reports.router)
