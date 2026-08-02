from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile

from django.conf import settings


class StorageError(Exception):
    pass


def validate_storage_path(storage_path, expected_prefix=None):
    if (
        not isinstance(storage_path, str)
        or not storage_path
        or "\\" in storage_path
        or "\x00" in storage_path
        or Path(storage_path).is_absolute()
    ):
        raise ValueError("Invalid storage path")
    relative = PurePosixPath(storage_path)
    if ".." in relative.parts:
        raise ValueError("Invalid storage path")
    if expected_prefix is not None:
        prefix = PurePosixPath(expected_prefix)
        if relative.parts[: len(prefix.parts)] != prefix.parts:
            raise ValueError("Invalid storage path")
    return str(relative)


class LocalObjectStorage:
    def __init__(self, root=None):
        self.root = Path(root or settings.MEDIA_ROOT)

    def _path(self, storage_path):
        storage_path = validate_storage_path(storage_path)
        root = self.root.resolve()
        target = (root / Path(*PurePosixPath(storage_path).parts)).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Invalid storage path")
        return target

    def save(self, storage_path, data):
        target = self._path(storage_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(data))

    def read(self, storage_path):
        target = self._path(storage_path)
        if not target.is_file():
            raise FileNotFoundError(storage_path)
        return target.read_bytes()

    def size(self, storage_path):
        target = self._path(storage_path)
        if not target.is_file():
            raise FileNotFoundError(storage_path)
        return target.stat().st_size

    def delete(self, storage_path):
        try:
            self._path(storage_path).unlink(missing_ok=True)
        except OSError:
            pass

    @contextmanager
    def local_path(self, storage_path):
        target = self._path(storage_path)
        if not target.is_file():
            raise FileNotFoundError(storage_path)
        yield target


def _oss_bucket():
    try:
        import oss2
    except ImportError as exc:
        raise StorageError("oss2 is required when STORAGE_BACKEND=oss") from exc
    if not all((settings.OSS_ENDPOINT, settings.OSS_BUCKET, settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)):
        raise StorageError("OSS storage is not configured")
    auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
    return oss2.Bucket(auth, settings.OSS_ENDPOINT, settings.OSS_BUCKET)


class OssObjectStorage:
    def __init__(self, bucket=None, prefix=None):
        self.bucket = bucket or _oss_bucket()
        self.prefix = str(prefix if prefix is not None else settings.OSS_PREFIX).strip("/")

    def _key(self, storage_path):
        storage_path = validate_storage_path(storage_path)
        return f"{self.prefix}/{storage_path}" if self.prefix else storage_path

    def save(self, storage_path, data):
        self.bucket.put_object(self._key(storage_path), bytes(data))

    def _raise_file_not_found(self, storage_path, exc):
        status = getattr(exc, "status", None)
        name = exc.__class__.__name__
        if isinstance(exc, KeyError) or status == 404 or name in {"NoSuchKey", "NoSuchObject", "NotFound"}:
            raise FileNotFoundError(storage_path) from exc
        raise exc

    def read(self, storage_path):
        try:
            return self.bucket.get_object(self._key(storage_path)).read()
        except Exception as exc:
            self._raise_file_not_found(storage_path, exc)

    def size(self, storage_path):
        try:
            return int(self.bucket.get_object_meta(self._key(storage_path)).content_length)
        except Exception as exc:
            self._raise_file_not_found(storage_path, exc)

    def delete(self, storage_path):
        self.bucket.delete_object(self._key(storage_path))

    @contextmanager
    def local_path(self, storage_path):
        suffix = Path(storage_path).suffix
        temporary = NamedTemporaryFile(delete=False, suffix=suffix)
        path = Path(temporary.name)
        try:
            temporary.write(self.read(storage_path))
            temporary.close()
            yield path
        finally:
            try:
                temporary.close()
            except OSError:
                pass
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def get_object_storage(root=None):
    if root is not None:
        return LocalObjectStorage(root)
    if str(settings.STORAGE_BACKEND).lower() == "oss":
        return OssObjectStorage()
    return LocalObjectStorage()
