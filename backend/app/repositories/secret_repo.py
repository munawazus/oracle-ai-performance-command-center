from sqlalchemy.ext.asyncio import AsyncSession

from app.models.secret import Secret


class SecretRepository:
    """Pure storage for ciphertext. Encryption/decryption happens in SecretProvider, not here."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, ciphertext: str, created_by_user_id: str | None) -> Secret:
        secret = Secret(ciphertext=ciphertext, created_by_user_id=created_by_user_id)
        self.db.add(secret)
        await self.db.flush()
        return secret

    async def get(self, secret_id: str) -> Secret | None:
        return await self.db.get(Secret, secret_id)

    async def update(self, secret_id: str, ciphertext: str) -> None:
        secret = await self.get(secret_id)
        if secret is None:
            raise LookupError(f"secret {secret_id} not found")
        secret.ciphertext = ciphertext
        await self.db.flush()

    async def delete(self, secret_id: str) -> None:
        secret = await self.get(secret_id)
        if secret:
            await self.db.delete(secret)
            await self.db.flush()
