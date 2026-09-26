"""Publish the local score CSV for the website to read."""

from pathlib import Path

import boto3
from botocore.config import Config


S3_SCORE_KEY = "public/showerhacks_scores.csv"


def upload_scores(path: Path, bucket: str) -> None:
    """Replace the public S3 copy with the latest complete local CSV."""
    client = boto3.client(
        "s3",
        config=Config(connect_timeout=3, read_timeout=5,
                      retries={"total_max_attempts": 2}),
    )
    client.put_object(
        Bucket=bucket,
        Key=S3_SCORE_KEY,
        Body=path.read_bytes(),
        ContentType="text/csv; charset=utf-8",
        CacheControl="no-store",
    )
