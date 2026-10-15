from app.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.models.user import User
from app.repositories.rbac_repo import RbacRepository
from app.repositories.user_repo import UserRepository
from app.security.passwords import hash_password
from app.services.audit_service import AuditService

VALID_ROLE_NAMES = {"ADMIN", "DBA", "OPERATOR", "VIEWER", "AUDITOR"}


class UserService:
    def __init__(self, user_repo: UserRepository, rbac_repo: RbacRepository, audit: AuditService):
        self.user_repo = user_repo
        self.rbac_repo = rbac_repo
        self.audit = audit

    async def list_users(self) -> list[dict]:
        users = await self.user_repo.list_all()
        results = []
        for user in users:
            roles = await self.rbac_repo.get_user_role_names(user.id)
            results.append({"user": user, "roles": roles})
        return results

    async def create_user(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        role_names: list[str],
        actor_user_id: str,
        ip_address: str | None,
    ) -> User:
        if await self.user_repo.get_by_email(email):
            raise ConflictError("A user with this email already exists.")

        invalid = set(role_names) - VALID_ROLE_NAMES
        if invalid:
            raise ValidationAppError(f"Unknown role(s): {', '.join(sorted(invalid))}")

        user = await self.user_repo.create(
            email=email, password_hash=hash_password(password), display_name=display_name
        )
        for role_name in role_names:
            role = await self.rbac_repo.get_role_by_name(role_name)
            if role:
                await self.rbac_repo.assign_role_to_user(user.id, role.id)

        await self.audit.record(
            action="user.created",
            success=True,
            actor_user_id=actor_user_id,
            target_type="user",
            target_id=user.id,
            ip_address=ip_address,
            metadata={"email": user.email, "roles": role_names},
        )
        return user

    async def set_active(
        self, user_id: str, is_active: bool, *, actor_user_id: str, ip_address: str | None
    ) -> User:
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        await self.user_repo.set_active(user, is_active)
        await self.audit.record(
            action="user.status_changed",
            success=True,
            actor_user_id=actor_user_id,
            target_type="user",
            target_id=user.id,
            ip_address=ip_address,
            metadata={"is_active": is_active},
        )
        return user

    async def set_roles(
        self, user_id: str, role_names: list[str], *, actor_user_id: str, ip_address: str | None
    ) -> User:
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")

        invalid = set(role_names) - VALID_ROLE_NAMES
        if invalid:
            raise ValidationAppError(f"Unknown role(s): {', '.join(sorted(invalid))}")

        current = set(await self.rbac_repo.get_user_role_names(user_id))
        desired = set(role_names)

        for role_name in current - desired:
            role = await self.rbac_repo.get_role_by_name(role_name)
            if role:
                await self.rbac_repo.remove_role_from_user(user_id, role.id)
        for role_name in desired - current:
            role = await self.rbac_repo.get_role_by_name(role_name)
            if role:
                await self.rbac_repo.assign_role_to_user(user_id, role.id)

        await self.audit.record(
            action="user.roles_changed",
            success=True,
            actor_user_id=actor_user_id,
            target_type="user",
            target_id=user.id,
            ip_address=ip_address,
            metadata={"added": sorted(desired - current), "removed": sorted(current - desired)},
        )
        return user
