"""Idempotent seed: roles, permissions, role->permission grants, and one bootstrap ADMIN user.

Run with: python -m app.seed
"""

import asyncio
import os

from app.db import SessionLocal
from app.repositories.rbac_repo import RbacRepository
from app.repositories.user_repo import UserRepository
from app.security.passwords import hash_password

PERMISSIONS = [
    ("database:view", "View monitored databases"),
    ("database:add", "Register a new monitored database"),
    ("database:edit", "Edit a monitored database's registration or credential"),
    ("database:delete", "Remove a monitored database"),
    ("incident:view", "View incidents"),
    ("incident:manage", "Acknowledge, assign, and resolve incidents"),
    ("report:create", "Generate reports"),
    ("notification:configure", "Configure alerting/notification settings"),
    ("admin:users", "Manage users and role assignments"),
    ("audit:view", "View the audit log"),
    ("ai:query", "Use the AI Copilot and request incident root-cause analysis"),
]

ROLE_PERMISSIONS = {
    "ADMIN": [code for code, _ in PERMISSIONS],
    "DBA": [
        "database:view",
        "database:add",
        "database:edit",
        "database:delete",
        "incident:view",
        "incident:manage",
        "report:create",
        "notification:configure",
        "ai:query",
    ],
    "OPERATOR": ["database:view", "incident:view", "incident:manage", "report:create", "ai:query"],
    "VIEWER": ["database:view", "incident:view", "ai:query"],
    "AUDITOR": ["audit:view", "database:view", "incident:view"],
}

ROLE_DESCRIPTIONS = {
    "ADMIN": "Full platform administration",
    "DBA": "Manages monitored databases and responds to incidents",
    "OPERATOR": "Day-to-day monitoring and reporting",
    "VIEWER": "Read-only access",
    "AUDITOR": "Compliance and audit-log review",
}


async def seed() -> None:
    async with SessionLocal() as db:
        rbac = RbacRepository(db)
        users = UserRepository(db)

        permission_ids: dict[str, str] = {}
        for code, description in PERMISSIONS:
            existing = await rbac.get_permission_by_code(code)
            if existing is None:
                existing = await rbac.create_permission(code, description)
            permission_ids[code] = existing.id

        for role_name, description in ROLE_DESCRIPTIONS.items():
            role = await rbac.get_role_by_name(role_name)
            if role is None:
                role = await rbac.create_role(role_name, description)
            for perm_code in ROLE_PERMISSIONS[role_name]:
                await rbac.grant_permission_to_role(role.id, permission_ids[perm_code])

        admin_email = os.environ.get("ADMIN_BOOTSTRAP_EMAIL")
        admin_password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD")
        if admin_email and admin_password:
            existing_user = await users.get_by_email(admin_email)
            if existing_user is None:
                admin_role = await rbac.get_role_by_name("ADMIN")
                user = await users.create(
                    email=admin_email,
                    password_hash=hash_password(admin_password),
                    display_name="Platform Administrator",
                )
                if admin_role:
                    await rbac.assign_role_to_user(user.id, admin_role.id)
                print(f"Created bootstrap admin user: {admin_email}")
            else:
                print(f"Bootstrap admin user already exists: {admin_email}")

        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
