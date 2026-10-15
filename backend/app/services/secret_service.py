from abc import ABC, abstractmethod

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.repositories.secret_repo import SecretRepository


class SecretProvider(ABC):
    """Abstraction over where encrypted secrets actually live.

    LocalEncryptedSecretProvider is the development/self-hosted default. The interface is
    deliberately narrow so a HashiCorp Vault / Azure Key Vault / AWS Secrets Manager / GCP
    Secret Manager implementation can be substituted later without touching any call site.
    Nothing outside an implementation of this class ever sees plaintext secret material.
    """

    @abstractmethod
    async def set_secret(self, plaintext: str, created_by_user_id: str | None) -> str:
        """Store a new secret, returning an opaque reference to it."""

    @abstractmethod
    async def get_secret(self, ref: str) -> str:
        """Resolve a reference back to its plaintext. Callers must never log the result."""

    @abstractmethod
    async def rotate_secret(self, ref: str, plaintext: str) -> None:
        """Replace the value behind an existing reference."""

    @abstractmethod
    async def delete_secret(self, ref: str) -> None:
        """Permanently remove a secret."""


class SecretNotFoundError(Exception):
    pass


class LocalEncryptedSecretProvider(SecretProvider):
    """AES (Fernet) envelope encryption backed by the `secrets` table.

    The Fernet key comes from SECRET_ENCRYPTION_KEY and must never be committed. This is a
    development/single-node default, not a production HSM/KMS - swap it for a real vault
    integration before handling real customer credentials at scale.
    """

    def __init__(self, repo: SecretRepository):
        self.repo = repo
        settings = get_settings()
        self._fernet = Fernet(settings.secret_encryption_key.encode())

    async def set_secret(self, plaintext: str, created_by_user_id: str | None) -> str:
        ciphertext = self._fernet.encrypt(plaintext.encode()).decode()
        secret = await self.repo.create(ciphertext, created_by_user_id)
        return secret.id

    async def get_secret(self, ref: str) -> str:
        secret = await self.repo.get(ref)
        if secret is None:
            raise SecretNotFoundError(f"secret {ref} not found")
        try:
            return self._fernet.decrypt(secret.ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise SecretNotFoundError(f"secret {ref} could not be decrypted") from exc

    async def rotate_secret(self, ref: str, plaintext: str) -> None:
        ciphertext = self._fernet.encrypt(plaintext.encode()).decode()
        await self.repo.update(ref, ciphertext)

    async def delete_secret(self, ref: str) -> None:
        await self.repo.delete(ref)
