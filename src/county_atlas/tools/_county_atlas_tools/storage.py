"""Backend-aware storage for the County Atlas.

The county OSM extracts live in the shared object store (MinIO/S3) at
``<bucket>/north-america/us/<state>/<county>-latest.osm.pbf`` — produced by the
``osm.planet`` pipeline. Atlas output (per-county ``index.html`` + ``manifest.json``
and the master index) is written back to the same store under ``county-atlas/`` so
any runner on any host resolves it and the dashboard can serve it.

Configured by the standard fleet env: ``FW_S3_ENDPOINT`` (default
``http://afl-minio:9000``) + ``FW_S3_ACCESS_KEY`` / ``FW_S3_SECRET_KEY`` (default
``minioadmin``). ``FW_ATLAS_BUCKET`` overrides the bucket (default ``osm-extracts``).
"""
from __future__ import annotations

import os

BUCKET = os.environ.get("FW_ATLAS_BUCKET", "osm-extracts")
OUTPUT_PREFIX = "county-atlas"


def s3_client():
    import boto3
    import botocore
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("FW_S3_ENDPOINT", "http://afl-minio:9000"),
        aws_access_key_id=os.environ.get("FW_S3_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("FW_S3_SECRET_KEY", "minioadmin"),
        config=botocore.config.Config(read_timeout=300, retries={"max_attempts": 5}),
    )


def pbf_key(county_key: str) -> str:
    """`north-america/us/oregon/coos` -> the latest county PBF object key."""
    return f"{county_key.strip('/')}-latest.osm.pbf"


def download_pbf(s3, county_key: str, dest: str, bucket: str = BUCKET) -> None:
    s3.download_file(bucket, pbf_key(county_key), dest)


def put_text(s3, key: str, text: str, content_type: str, bucket: str = BUCKET) -> str:
    s3.put_object(Bucket=bucket, Key=key, Body=text.encode("utf-8"),
                  ContentType=content_type)
    return f"s3://{bucket}/{key}"


def atlas_html_key(county_key: str) -> str:
    return f"{OUTPUT_PREFIX}/{county_key.split('north-america/us/')[-1].strip('/')}/index.html"


def atlas_manifest_key(county_key: str) -> str:
    return f"{OUTPUT_PREFIX}/{county_key.split('north-america/us/')[-1].strip('/')}/manifest.json"
