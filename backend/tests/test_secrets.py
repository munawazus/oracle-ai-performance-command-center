import pytest

from app.db import SessionLocal
from app.repositories.secret_repo import SecretRepository
from app.security.jwt import DecodedTokenError, create_access_token, create_refresh_token, decode_token
from app.security.passwords import hash_password, verify_password
from app.services.secret_service import LocalEncryptedSecretProvider, SecretNotFoundError


def test_password_hash_and_verify_roundtrip():
    hashed = hash_password("CorrectHorseBatteryStaple!")
    assert hashed != "CorrectHorseBatteryStaple!"
    assert verify_password("CorrectHorseBatteryStaple!", hashed)
    assert not verify_password("wrong-password", hashed)


def test_access_token_roundtrip():
    token, expires_at = create_access_token("user-123")
    payload = decode_token(token, "access")
    assert payload.sub == "user-123"
    assert payload.type == "access"


def test_refresh_token_rejected_as_access_token():
    token, _jti, _expires = create_refresh_token("user-123")
    with pytest.raises(DecodedTokenError):
        decode_token(token, "access")


def test_garbage_token_rejected():
    with pytest.raises(DecodedTokenError):
        decode_token("not-a-real-token", "access")


async def test_secret_provider_encrypts_and_roundtrips():
    async with SessionLocal() as db:
        provider = LocalEncryptedSecretProvider(SecretRepository(db))
        ref = await provider.set_secret("hunter2-oracle-password", created_by_user_id=None)
        await db.commit()

        stored = await SecretRepository(db).get(ref)
        assert stored is not None
        assert "hunter2" not in stored.ciphertext

        plaintext = await provider.get_secret(ref)
        assert plaintext == "hunter2-oracle-password"


async def test_secret_provider_rotate_and_delete():
    async with SessionLocal() as db:
        provider = LocalEncryptedSecretProvider(SecretRepository(db))
        ref = await provider.set_secret("original-password", created_by_user_id=None)
        await db.commit()

        await provider.rotate_secret(ref, "rotated-password")
        await db.commit()
        assert await provider.get_secret(ref) == "rotated-password"

        await provider.delete_secret(ref)
        await db.commit()
        with pytest.raises(SecretNotFoundError):
            await provider.get_secret(ref)
