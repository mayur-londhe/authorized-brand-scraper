"""Private S3 storage layer used by the Streamlit file dashboard."""

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import mimetypes
import os
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import boto3
from botocore.exceptions import ClientError


@dataclass(frozen=True)
class S3File:
    key: str
    size: int
    last_modified: datetime | None = None
    etag: str = ""

    @property
    def name(self) -> str:
        return PurePosixPath(self.key).name


class S3Storage:
    """Manage scraper exports using boto3's standard credential chain."""

    def __init__(self, bucket_name: str, region: str | None = None):
        self.bucket_name = bucket_name.strip()
        self.region = (region or os.getenv("AWS_REGION") or "us-east-2").strip()
        if not self.bucket_name:
            raise ValueError("BUCKET_NAME is not configured.")
        self.client = boto3.client("s3", region_name=self.region)

    def bucket_exists(self) -> bool:
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
            return True
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchBucket", "NotFound"}:
                return False
            raise

    def create_bucket(self) -> None:
        args = {"Bucket": self.bucket_name}
        if self.region != "us-east-1":
            args["CreateBucketConfiguration"] = {
                "LocationConstraint": self.region
            }
        self.client.create_bucket(**args)
        self.client.put_public_access_block(
            Bucket=self.bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        self.client.put_bucket_encryption(
            Bucket=self.bucket_name,
            ServerSideEncryptionConfiguration={
                "Rules": [{
                    "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}
                }]
            },
        )

    def list_files(self, prefix: str = "exports/") -> list[S3File]:
        paginator = self.client.get_paginator("list_objects_v2")
        files = []
        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
            for item in page.get("Contents", []):
                key = str(item.get("Key", ""))
                if key and not key.endswith("/"):
                    files.append(S3File(
                        key=key,
                        size=int(item.get("Size", 0)),
                        last_modified=item.get("LastModified"),
                        etag=str(item.get("ETag", "")).strip('"'),
                    ))
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        return sorted(files, key=lambda item: item.last_modified or epoch, reverse=True)

    def upload_path(self, path: Path, key: str | None = None) -> str:
        path = Path(path)
        object_key = self._safe_key(key or f"exports/{path.name}")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.client.upload_file(
            str(path), self.bucket_name, object_key,
            ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256"},
        )
        return object_key

    def upload_fileobj(self, fileobj: BinaryIO, filename: str) -> str:
        object_key = self._safe_key(f"exports/{Path(filename).name}")
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.client.upload_fileobj(
            fileobj, self.bucket_name, object_key,
            ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256"},
        )
        return object_key

    def download_bytes(self, key: str) -> bytes:
        buffer = BytesIO()
        self.client.download_fileobj(self.bucket_name, self._safe_key(key), buffer)
        return buffer.getvalue()

    def delete_file(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket_name, Key=self._safe_key(key))

    @staticmethod
    def _safe_key(key: str) -> str:
        normalized = str(PurePosixPath(key.replace("\\", "/"))).lstrip("/")
        if not normalized or normalized == "." or ".." in PurePosixPath(normalized).parts:
            raise ValueError("Invalid S3 object key.")
        return normalized
