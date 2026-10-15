from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import Permission, Role, RolePermission, UserRole


class RbacRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_role_by_name(self, name: str) -> Role | None:
        result = await self.db.execute(select(Role).where(Role.name == name))
        return result.scalar_one_or_none()

    async def list_roles(self) -> list[Role]:
        result = await self.db.execute(select(Role).order_by(Role.name))
        return list(result.scalars().all())

    async def list_permissions(self) -> list[Permission]:
        result = await self.db.execute(select(Permission).order_by(Permission.code))
        return list(result.scalars().all())

    async def get_permission_by_code(self, code: str) -> Permission | None:
        result = await self.db.execute(select(Permission).where(Permission.code == code))
        return result.scalar_one_or_none()

    async def assign_role_to_user(self, user_id: str, role_id: str) -> None:
        existing = await self.db.get(UserRole, {"user_id": user_id, "role_id": role_id})
        if existing:
            return
        self.db.add(UserRole(user_id=user_id, role_id=role_id))
        await self.db.flush()

    async def remove_role_from_user(self, user_id: str, role_id: str) -> None:
        existing = await self.db.get(UserRole, {"user_id": user_id, "role_id": role_id})
        if existing:
            await self.db.delete(existing)
            await self.db.flush()

    async def get_user_role_names(self, user_id: str) -> list[str]:
        result = await self.db.execute(
            select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
        )
        return list(result.scalars().all())

    async def get_user_permission_codes(self, user_id: str) -> set[str]:
        result = await self.db.execute(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .where(UserRole.user_id == user_id)
        )
        return set(result.scalars().all())

    async def grant_permission_to_role(self, role_id: str, permission_id: str) -> None:
        existing = await self.db.get(RolePermission, {"role_id": role_id, "permission_id": permission_id})
        if existing:
            return
        self.db.add(RolePermission(role_id=role_id, permission_id=permission_id))
        await self.db.flush()

    async def create_role(self, name: str, description: str = "") -> Role:
        role = Role(name=name, description=description)
        self.db.add(role)
        await self.db.flush()
        return role

    async def create_permission(self, code: str, description: str = "") -> Permission:
        permission = Permission(code=code, description=description)
        self.db.add(permission)
        await self.db.flush()
        return permission
