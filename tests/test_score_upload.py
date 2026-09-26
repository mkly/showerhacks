import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from showerhacks.score_upload import S3_SCORE_KEY, upload_scores


class ScoreUploadTest(unittest.TestCase):
    def test_uploads_exact_csv_to_public_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "showerhacks_scores.csv"
            contents = b"initials,score\nABC,42\n"
            path.write_bytes(contents)
            with patch("showerhacks.score_upload.boto3.client") as client_factory:
                upload_scores(path, "example-bucket")

        client_factory.return_value.put_object.assert_called_once_with(
            Bucket="example-bucket",
            Key=S3_SCORE_KEY,
            Body=contents,
            ContentType="text/csv; charset=utf-8",
            CacheControl="no-store",
        )


if __name__ == "__main__":
    unittest.main()
