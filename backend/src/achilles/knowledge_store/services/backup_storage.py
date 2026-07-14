"""Snapshot storage port (lifecycle.html#backup): `file://` volume + `s3://`.

S3 credentials arrive as decrypted JSON (backup_settings.destination_creds_enc,
write-only through the Admin screen); an empty credential means the ambient
IAM role of the deployment.
"""

import asyncio
import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

import aioboto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, ValidationError

from achilles.api.problems import ApiError
from achilles.knowledge_store.constants import CODE_BACKUP_NOT_CONFIGURED


class BackupStorage(Protocol):
    async def store(self, path: Path, name: str) -> str:
        """Persist a local file under `name`; returns the location (restore pointer)."""
        ...

    async def delete(self, location: str) -> None: ...

    async def fetch(self, location: str) -> Path:
        """Make the snapshot available as a local file for pg_restore."""
        ...


class FileBackupStorage:
    def __init__(self, root: Path) -> None:
        self._root = root

    async def store(self, path: Path, name: str) -> str:
        target = self._root / name
        await asyncio.to_thread(self._root.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copyfile, path, target)
        return target.as_uri()

    async def delete(self, location: str) -> None:
        await asyncio.to_thread(self._path_of(location).unlink, missing_ok=True)

    async def fetch(self, location: str) -> Path:
        return self._path_of(location)

    @staticmethod
    def _path_of(location: str) -> Path:
        # Path.as_uri() percent-encodes (space → %20); decode on the way back
        # or a destination with a space/non-ASCII char breaks fetch and delete.
        return Path(unquote(urlparse(location).path))


class S3Credentials(BaseModel):
    """The decrypted shape of destination_creds_enc (data-model.html#backup-settings)."""

    access_key: str
    secret_key: str
    endpoint_url: str | None = None  # S3-compatible stores (MinIO); None = AWS
    region: str | None = None


@asynccontextmanager
async def _s3_errors_as_os() -> AsyncGenerator[None]:
    """Botocore failures → OSError: the backup job's except tuple already covers I/O."""
    try:
        yield
    except (BotoCoreError, ClientError) as exc:
        raise OSError(str(exc)) from exc


class S3BackupStorage:
    """aioboto3-backed store: upload_file/download_file give multipart + retries."""

    def __init__(self, bucket: str, prefix: str, creds: S3Credentials | None) -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._creds = creds

    def _client(self):  # noqa: ANN202 — aioboto3 client factories are untyped context managers
        creds = self._creds
        return aioboto3.Session().client(
            "s3",
            aws_access_key_id=creds.access_key if creds else None,
            aws_secret_access_key=creds.secret_key if creds else None,
            endpoint_url=creds.endpoint_url if creds else None,
            region_name=creds.region if creds else None,
        )

    def _key_of(self, name: str) -> str:
        return f"{self._prefix}/{name}" if self._prefix else name

    async def store(self, path: Path, name: str) -> str:
        key = self._key_of(name)
        async with _s3_errors_as_os(), self._client() as s3:
            await s3.upload_file(str(path), self._bucket, key)
        return f"s3://{self._bucket}/{key}"

    async def delete(self, location: str) -> None:
        parsed = urlparse(location)
        async with _s3_errors_as_os(), self._client() as s3:
            await s3.delete_object(Bucket=parsed.netloc, Key=parsed.path.lstrip("/"))

    async def fetch(self, location: str) -> Path:
        parsed = urlparse(location)
        # The dump lands in a temp file the restore job reads once; the worker
        # process is short-lived, so the OS temp dir is the cleanup boundary.
        target = Path(tempfile.mkdtemp(prefix="achilles-restore-")) / Path(parsed.path).name
        async with _s3_errors_as_os(), self._client() as s3:
            await s3.download_file(parsed.netloc, parsed.path.lstrip("/"), str(target))
        return target


def not_configured(detail: str | None = None) -> ApiError:
    return ApiError(
        409,
        CODE_BACKUP_NOT_CONFIGURED,
        "Backup destination not configured",
        detail or "Configure the external storage destination in the backup settings first.",
    )


def resolve_storage(destination_url: str | None, *, creds_json: str | None = None) -> BackupStorage:
    """Storage for the configured destination; unconfigured/unsupported → 409."""
    if destination_url is None:
        raise not_configured()
    parsed = urlparse(destination_url)
    if parsed.scheme == "file":
        return FileBackupStorage(Path(unquote(parsed.path)))
    if parsed.scheme == "s3":
        if not parsed.netloc:
            raise not_configured("The s3:// destination is missing a bucket name.")
        creds = None
        if creds_json:
            try:
                creds = S3Credentials.model_validate_json(creds_json)
            except ValidationError as exc:
                raise not_configured("The stored S3 credentials are malformed.") from exc
        return S3BackupStorage(parsed.netloc, parsed.path, creds)
    raise not_configured()
