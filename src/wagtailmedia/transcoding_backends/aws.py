from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from wagtailmedia.transcoding_backends.aws_utils import (
    import_boto3,
)
from wagtailmedia.transcoding_backends.base import (
    AbstractTranscodingBackend,
    TranscodingError,
)


class S3UploadError(TranscodingError):
    """Failed to upload file to S3."""

    pass


class MediaConvertJobError(TranscodingError):
    """Failed to create or manage MediaConvert job."""

    pass


class AWSTranscodingConfig:
    """
    Configuration management for AWS transcoding backend.

    Loads and validates required Django settings for AWS MediaConvert
    transcoding operations.

    Required Django settings:
        AWS_STORAGE_BUCKET_NAME: S3 bucket for transcoded files
        AWS_MEDIACONVERT_ROLE_NAME: IAM role name for MediaConvert (default: 'MediaConvert_Default_Role')
    """

    def __init__(self):
        self.destination_bucket = self._get_required_setting("AWS_STORAGE_BUCKET_NAME")
        self.mediaconvert_role = self._get_required_setting(
            "AWS_MEDIACONVERT_ROLE_NAME", "MediaConvert_Default_Role"
        )

    def _get_required_setting(self, setting_name: str, default=None):
        """
        Get a Django setting with optional default value.

        Args:
            setting_name: Name of the Django setting to retrieve
            default: Default value if setting is not found (None means required)

        Returns:
            Setting value from Django settings or default

        Raises:
            ImproperlyConfigured: If setting is None and no default provided
        """
        value = getattr(settings, setting_name, default)
        if value is None:
            # FIXME: Handle this in a system check rather than at runtime
            raise ImproperlyConfigured(
                f"{setting_name} is required for AWS transcoding. "
                f"Please add it to your Django settings."
            )
        return value


class S3Service:
    """
    Handles S3 upload operations for media files.

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

        Raises:
            S3UploadError: If upload fails due to permissions or connectivity
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

        Raises:
            ValueError: If source_file lacks required 'name' attribute
            S3UploadError: If upload to S3 fails
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


class MediaConvertJobSettings:
    """
    MediaConvert job settings configurations.

    Provides static methods to generate job settings dictionaries for different
    transcoding profiles and output formats.
    """

    @staticmethod
    def webm_vp8_settings(source_url: str, destination_bucket: str) -> dict:
        """
        Build a standard WEBM/VP8/OPUS transcode job configuration.

        Creates a MediaConvert job that transcodes video to WEBM container
        with VP8 video codec (2.5 Mbps VBR, 24fps) and OPUS audio codec.

        Args:
            source_url: S3 URL of source file (s3://bucket/key format)
            destination_bucket: S3 URL of destination directory (s3://bucket/prefix/)

        Returns:
            dict: Complete MediaConvert job settings dictionary
        """

        return {
            "TimecodeConfig": {"Source": "EMBEDDED"},
            "FollowSource": 1,
            "Inputs": [
                {
                    "AudioSelectors": {
                        "Audio Selector 1": {"DefaultSelection": "DEFAULT"}
                    },
                    "TimecodeSource": "EMBEDDED",
                    "FileInput": source_url,
                }
            ],
            "OutputGroups": [
                {
                    "Name": "File Group",
                    "Outputs": [
                        {
                            "ContainerSettings": {"Container": "WEBM"},
                            "VideoDescription": {
                                "CodecSettings": {
                                    "Codec": "VP8",
                                    "Vp8Settings": {
                                        "RateControlMode": "VBR",
                                        "Bitrate": 2500000,
                                        "FramerateControl": "SPECIFIED",
                                        "FramerateNumerator": 24,
                                        "FramerateDenominator": 1,
                                    },
                                }
                            },
                            "AudioDescriptions": [
                                {
                                    "AudioSourceName": "Audio Selector 1",
                                    "CodecSettings": {
                                        "Codec": "OPUS",
                                        "OpusSettings": {},
                                    },
                                }
                            ],
                        }
                    ],
                    "OutputGroupSettings": {
                        "Type": "FILE_GROUP_SETTINGS",
                        "FileGroupSettings": {"Destination": destination_bucket},
                    },
                }
            ],
        }


class MediaConvertService:
    """
    Handles AWS MediaConvert job operations.

    Manages MediaConvert job creation, IAM role resolution, and client initialization.
    Caches role ARN and clients for performance.
    """

    def __init__(self, config: AWSTranscodingConfig):
        """Initialise MediaConvert service with configuration."""
        self.config = config

    def get_role_arn(self) -> str:
        """
        Get the IAM role ARN for MediaConvert jobs.

        Retrieves and caches the ARN for the configured IAM role that
        MediaConvert will assume when executing transcode jobs.

        Returns:
            str: Full IAM role ARN (arn:aws:iam::account-id:role/role-name)

        Raises:
            ImproperlyConfigured: If role cannot be found or IAM access is denied
        """

        boto3, botocore_exceptions = import_boto3()
        iam = boto3.client("iam")

        try:
            response = iam.get_role(RoleName=self.config.mediaconvert_role)
            return response["Role"]["Arn"]
        except botocore_exceptions.ClientError as err:
            raise ImproperlyConfigured(
                f"Failed to get IAM role '{self.config.mediaconvert_role}': {err}"
            ) from err

    def create_transcode_job(
        self, source_url: str, destination_bucket: str, job_settings: dict
    ) -> dict:
        """
        Create and submit a MediaConvert transcode job.

        Submits a transcode job to AWS MediaConvert with the specified settings.
        The job is executed asynchronously by MediaConvert.

        Args:
            source_url: S3 URL of source file (s3://bucket/key format)
            destination_bucket: S3 URL of destination directory (s3://bucket/prefix/)
            job_settings: Complete MediaConvert job settings dictionary

        Returns:
            dict: MediaConvert CreateJob API response containing job ID and metadata

        Raises:
            ImproperlyConfigured: If IAM role cannot be retrieved
            MediaConvertJobError: If job creation fails (invalid settings, permissions, etc.)
        """

        boto3, botocore_exceptions = import_boto3()
        mediaconvert = boto3.client("mediaconvert")

        role_arn = self.get_role_arn()

        try:
            response = mediaconvert.create_job(Role=role_arn, Settings=job_settings)
            return response
        except botocore_exceptions.ClientError as err:
            raise MediaConvertJobError(
                f"Failed to create MediaConvert job: {err}"
            ) from err


class EMCTranscodingBackend(AbstractTranscodingBackend):
    """
    AWS MediaConvert transcoding backend implementation.

    Orchestrates the transcoding workflow:
    1. Ensures source file is accessible (uploads to S3 if needed)
    2. Generates MediaConvert job settings
    3. Submits transcode job to MediaConvert
    4. Returns job response for tracking

    Configuration via Django settings:
        - AWS_STORAGE_BUCKET_NAME (required)
        - AWS_MEDIACONVERT_ROLE_NAME (defaults to 'MediaConvert_Default_Role')
    """

    def __init__(self):
        """Initialise the AWS transcoding backend."""

        self.config = AWSTranscodingConfig()
        self.s3_service = S3Service(self.config)
        self.mediaconvert_service = MediaConvertService(self.config)
        self.job_settings = MediaConvertJobSettings()

    def start_transcode(self, source_file) -> dict:
        """
        Start transcoding a media file using AWS MediaConvert.

        Args:
            source_file: Django file object to transcode (must have 'name' attribute
                        and optionally 'url' for web-accessible files)

        Returns:
            dict: MediaConvert CreateJob API response containing:
                - Job['Id']: Unique job identifier for tracking
                - Job['Status']: Initial job status (typically 'SUBMITTED')
                - Additional job metadata

        Raises:
            ValueError: If source_file is invalid
            S3UploadError: If file upload to S3 fails
            MediaConvertJobError: If job creation fails
            ImproperlyConfigured: If AWS configuration is invalid
        """

        # Ensure file is publicly accessible
        source_url = self.s3_service.ensure_file_is_available(
            source_file, self.config.destination_bucket
        )

        # Build job settings
        destination_url = f"s3://{self.config.destination_bucket}/"
        job_settings = self.job_settings.webm_vp8_settings(source_url, destination_url)

        # Create transcode job
        response = self.mediaconvert_service.create_transcode_job(
            source_url, destination_url, job_settings
        )

        return response

    def stop_transcode(self, task_id: str):
        """Stop a running MediaConvert transcode job."""

        raise NotImplementedError("Stop transcode is not yet implemented")
