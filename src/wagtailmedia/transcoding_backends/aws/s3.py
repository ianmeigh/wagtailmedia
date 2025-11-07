from pathlib import Path
from urllib.parse import urlparse

from wagtailmedia.transcoding_backends.aws.config import AWSTranscodingConfig
from wagtailmedia.transcoding_backends.aws.exceptions import S3UploadError
from wagtailmedia.transcoding_backends.aws.utils import import_boto3


class S3Service:
    """
    Handles AWS S3 service operations.

    Provides methods to upload files to S3 and ensure files are accessible
    for transcoding, either by returning existing web URLs or uploading
    local files.
    """

    def __init__(self, config: AWSTranscodingConfig):
        """Initialise S3 service with configuration."""
        self.config = config

    def upload_file(self, file, bucket_name: str, object_name: str):
        """
        Upload a file to S3.

        Args:
            file: File object to upload (must be readable)
            bucket_name: Target S3 bucket name
            object_name: Object key/path in S3

        Returns:
            dict: S3 put_object response
        """

        boto3, self.botocore_exceptions = import_boto3()
        s3 = boto3.client("s3")

        try:
            return s3.put_object(Body=file, Bucket=bucket_name, Key=object_name)
        except self.botocore_exceptions.ClientError as err:
            raise S3UploadError(f"Failed to upload file to S3: {err}") from err

    def ensure_file_is_available(self, source_file, bucket_name: str) -> str:
        """
        Ensure file is accessible for transcoding, uploading to S3 if needed.

        If the source file has a web-accessible URL (contains a domain), it is
        returned as-is. Otherwise, the file is uploaded to the specified S3
        bucket and an S3 URL is returned.

        Args:
            source_file: Django file object with 'name' and optional 'url' attributes
            bucket_name: S3 bucket name for upload destination

        Returns:
            str: Publicly accessible URL (web URL or s3:// URL format)
        """

        file_url = getattr(source_file, "url", None)
        is_domain_in_url = bool(urlparse(file_url).netloc)

        if is_domain_in_url:
            # Assume file is already accessible publicly
            return file_url

        # Upload local file to S3
        try:
            file_name = Path(source_file.name).name
            self.upload_file(source_file, bucket_name, file_name)
            return f"s3://{bucket_name}/{file_name}"
        except AttributeError as err:
            raise ValueError(
                f"source_file must be a Django file object with 'name' attribute: {err}"
            ) from err
