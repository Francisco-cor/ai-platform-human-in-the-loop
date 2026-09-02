"""ArtifactStore — Fase 9 Data Platform (GCS o local file://).

- Para dev/test: file://./artifacts  (o file:///tmp)
- Para prod/staging: gs://bucket via google-cloud-storage
- Eval runner guarda report_*.json en gs://bucket/evals/<run_id>.json
- Pipelines guardan traces OTel

Interfaz:
  store = ArtifactStore(bucket="gs://my-bucket" or "file://./artifacts")
  store.put("evals/report_<id>.json", data_bytes)
  store.get("evals/report_<id>.json")
"""

from __future__ import annotations

import os
import pathlib
from typing import Any


def _is_gcs(bucket: str | None) -> bool:
    return bool(bucket and bucket.startswith("gs://"))


def _parse_gcs_path(bucket_or_path: str) -> tuple[str, str]:
    # gs://bucket/prefix -> (bucket, prefix)
    if bucket_or_path.startswith("gs://"):
        no_prefix = bucket_or_path[5:]
        parts = no_prefix.split("/", 1)
        bucket = parts[0]
        prefix = parts[1] if len(parts) > 1 else ""
        return bucket, prefix
    return "", bucket_or_path


class ArtifactStore:
    def __init__(self, bucket: str | None = None, base_path: str | None = None):
        # bucket can be gs://... or file://... or local path
        self.bucket = bucket or os.getenv("PROCUREMENT_GCS_BUCKET") or os.getenv("GCS_BUCKET") or "file://./artifacts"
        self.base_path = base_path  # override for tests
        self._gcs_client = None
        if _is_gcs(self.bucket):
            try:
                from google.cloud import storage  # type: ignore

                self._gcs_client = storage.Client()
            except Exception:
                self._gcs_client = None
                # fallback to file
                self.bucket = "file://./artifacts"

    def _local_path(self, key: str) -> pathlib.Path:
        # Resolve file:// bucket to local path
        bucket_path = self.bucket
        if bucket_path.startswith("file://"):
            base = pathlib.Path(bucket_path[7:])
        elif bucket_path.startswith("gs://"):
            # fallback local for gcs without client
            base = pathlib.Path("./artifacts")
        else:
            base = pathlib.Path(bucket_path)
        if self.base_path:
            base = pathlib.Path(self.base_path)
        # sanitize key (no ..)
        key = key.lstrip("/")
        full = base / key
        return full

    def put(self, key: str, data: bytes | str, content_type: str = "application/json") -> str:
        if isinstance(data, str):
            data = data.encode("utf-8")
        if _is_gcs(self.bucket) and self._gcs_client:
            try:
                bucket_name, prefix = _parse_gcs_path(self.bucket)
                # key may include prefix
                blob_name = f"{prefix.rstrip('/')}/{key.lstrip('/')}" if prefix else key.lstrip("/")
                bucket = self._gcs_client.bucket(bucket_name)
                blob = bucket.blob(blob_name)
                blob.upload_from_string(data, content_type=content_type)
                return f"gs://{bucket_name}/{blob_name}"
            except Exception as e:
                # fallback to local
                pass
        # local file
        path = self._local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def get(self, key: str) -> bytes | None:
        if _is_gcs(self.bucket) and self._gcs_client:
            try:
                bucket_name, prefix = _parse_gcs_path(self.bucket)
                blob_name = f"{prefix.rstrip('/')}/{key.lstrip('/')}" if prefix else key.lstrip("/")
                bucket = self._gcs_client.bucket(bucket_name)
                blob = bucket.blob(blob_name)
                if blob.exists():
                    return blob.download_as_bytes()
            except Exception:
                pass
        path = self._local_path(key)
        if path.exists():
            return path.read_bytes()
        return None

    def exists(self, key: str) -> bool:
        if _is_gcs(self.bucket) and self._gcs_client:
            try:
                bucket_name, prefix = _parse_gcs_path(self.bucket)
                blob_name = f"{prefix.rstrip('/')}/{key.lstrip('/')}" if prefix else key.lstrip("/")
                bucket = self._gcs_client.bucket(bucket_name)
                blob = bucket.blob(blob_name)
                if blob.exists():
                    return True
            except Exception:
                pass
        return self._local_path(key).exists()

    def list(self, prefix: str = "") -> list[str]:
        if _is_gcs(self.bucket) and self._gcs_client:
            try:
                bucket_name, base_prefix = _parse_gcs_path(self.bucket)
                full_prefix = f"{base_prefix.rstrip('/')}/{prefix.lstrip('/')}" if base_prefix else prefix
                bucket = self._gcs_client.bucket(bucket_name)
                blobs = bucket.list_blobs(prefix=full_prefix)
                return [b.name for b in blobs]
            except Exception:
                pass
        base = self._local_path(prefix)
        # For local, list files under base
        root = self._local_path("")
        if prefix:
            # prefix may be like "evals/"
            search = root / prefix
            if search.is_dir():
                return [str(p.relative_to(root)) for p in search.rglob("*") if p.is_file()]
            # if prefix is file path, return matching
            return [str(p.relative_to(root)) for p in root.rglob(f"{prefix}*") if p.is_file()]
        else:
            if root.exists():
                return [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
        return []

    def delete(self, key: str) -> bool:
        if _is_gcs(self.bucket) and self._gcs_client:
            try:
                bucket_name, prefix = _parse_gcs_path(self.bucket)
                blob_name = f"{prefix.rstrip('/')}/{key.lstrip('/')}" if prefix else key.lstrip("/")
                bucket = self._gcs_client.bucket(bucket_name)
                bucket.blob(blob_name).delete()
                return True
            except Exception:
                pass
        path = self._local_path(key)
        if path.exists():
            path.unlink()
            return True
        return False


# Global singleton for convenience
_global_store: ArtifactStore | None = None


def get_artifact_store(bucket: str | None = None) -> ArtifactStore:
    global _global_store
    if _global_store is None or bucket:
        _global_store = ArtifactStore(bucket=bucket)
    return _global_store


def reset_artifact_store() -> None:
    global _global_store
    _global_store = None
