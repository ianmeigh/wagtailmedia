from __future__ import annotations

import json
import logging
import tempfile

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse


if TYPE_CHECKING:
    import boto3

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files import File
from django.core.files.storage import Storage, default_storage
from django_tasks import task

from wagtailmedia.models import (
    MediaRendition,
    MediaTranscodingJob,
    TranscodingJobStatus,
)
from wagtailmedia.settings import wagtailmedia_settings
from wagtailmedia.transcoding_backends.aws.settings import (
    AWS_MEDIACONVERT_ACCESS_KEY_ID,
    AWS_MEDIACONVERT_QUEUE_NAME,
    AWS_MEDIACONVERT_REGION_NAME,
    AWS_MEDIACONVERT_ROLE_NAME,
    AWS_MEDIACONVERT_SECRET_ACCESS_KEY,
    AWS_MEDIACONVERT_STORAGE_BUCKET_NAME,
)
from wagtailmedia.transcoding_backends.base import (
    AbstractTranscodingBackend,
    TranscodingError,
)


logger = logging.getLogger(__name__)


class S3UploadError(TranscodingError):
    """Failed to upload file to S3."""

    pass


class IAMGetRoleError(TranscodingError):
    """Failed to get IAM role."""

    pass


class MediaConvertJobError(TranscodingError):
    """Failed to create or manage MediaConvert job."""

    pass


class DataValidationError(Exception):
    pass


class TranscodingJobNotFound(Exception):
    pass


@dataclass
class JobDetail:
    """Normalized job details."""

    job_id: str
    status: str
    raw_detail: dict  # Full AWS response for metadata storage

    @classmethod
    def from_eventbridge_webhook(cls, payload: dict) -> JobDetail:
        try:
            detail = payload["detail"]
            return cls(
                job_id=detail["jobId"],
                status=detail["status"],
                raw_detail=detail,
            )
        except KeyError as err:
            raise DataValidationError(f"Missing required field: {err}") from err

    @classmethod
    def from_get_job_response(cls, response: dict) -> JobDetail:
        try:
            job = response["Job"]
            return cls(
                job_id=job["Id"],
                status=job["Status"],
                raw_detail=job,
            )
        except KeyError as err:
            raise DataValidationError(f"Missing required field: {err}") from err

    @property
    def output_detail(self) -> OutputDetail | None:
        """
        Extract output details from completed jobs.

        Returns None for jobs that are not in COMPLETE status.
        """
        if self.status != "COMPLETE":
            return None
        return OutputDetail.from_detail_dict(self.raw_detail)


@dataclass
class OutputDetail:
    """Represents the first outputDetails item from AWS MediaConvert webhook with a status of COMPLETE."""

    output_file_path: str
    duration_ms: int
    width_px: int
    height_px: int
    average_bitrate: int | None

    @classmethod
    def from_detail_dict(cls, detail: dict) -> OutputDetail:
        """
        Parse and validate the first outputDetails item from AWS webhook detail.

        Expects detail to have structure:
        {
            'timestamp': 0,
            'accountId': 'ACCOUNT_ID',
            'queue': 'arn:aws:mediaconvert:AWS_REGION:AWS_ACCOUNT_ID:queues/AWS_MEDIACONVERT_QUEUE_NAME',
            'jobId': 'JOB_ID',
            'status': 'PROGRESSING/COMPLETE/ERROR',
            'userMetadata': {}
            'outputGroupDetails': [{
                'outputDetails': [{
                    'outputFilePaths': ['s3://FILE_PATH'],
                    'durationInMs': 0,
                    'videoDetails': {
                        'widthInPx': 0,
                        'heightInPx': 0,
                        'averageBitrate': 0
                    }
                }],
                'type': 'FILE_GROUP'
            }],
            'paddingInserted': 0,
            'blackVideoDetected': 0
        }

        Raises:
            DataValidationError: If structure is invalid or missing required fields
        """
        try:
            output_item = detail["outputGroupDetails"][0]["outputDetails"][0]
            output_paths = output_item["outputFilePaths"]

            if not output_paths or not isinstance(output_paths, list):
                raise DataValidationError("outputFilePaths must be a non-empty list")

            video_details = output_item["videoDetails"]

            return cls(
                output_file_path=output_paths[0],
                duration_ms=output_item["durationInMs"],
                width_px=video_details["widthInPx"],
                height_px=video_details["heightInPx"],
                average_bitrate=video_details.get("averageBitrate", None),
            )
        except (KeyError, IndexError, TypeError) as e:
            raise DataValidationError(f"Invalid COMPLETE webhook structure: {e}") from e


def map_aws_status_to_internal(status: str) -> TranscodingJobStatus:
    """
    Map AWS MediaConvert status to internal TranscodingJobStatus.

    When MediaConvert reports COMPLETE, transcoding is done but we still need to
    finalize the job (download file if needed, create rendition record), so we
    map to FINALISING. The background task will update to COMPLETE when finished.
    """
    status_map = {
        AWSMediaConvertStatuses.PROGRESSING: TranscodingJobStatus.PROGRESSING,
        AWSMediaConvertStatuses.COMPLETE: TranscodingJobStatus.FINALISING,
        AWSMediaConvertStatuses.ERROR: TranscodingJobStatus.FAILED,
    }

    aws_status = AWSMediaConvertStatuses(status.upper())
    return status_map[aws_status]


def get_boto3_session() -> boto3.Session:
    """
    Get boto3 session for transcoding operations.

    Uses MediaConvert-specific credentials if configured, otherwise falls back to
    default boto3 credential chain.
    """
    import boto3

    if AWS_MEDIACONVERT_ACCESS_KEY_ID and AWS_MEDIACONVERT_SECRET_ACCESS_KEY:
        return boto3.Session(
            aws_access_key_id=AWS_MEDIACONVERT_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_MEDIACONVERT_SECRET_ACCESS_KEY,
            region_name=AWS_MEDIACONVERT_REGION_NAME,
        )

    return boto3.Session()


def get_s3_client():
    """Get S3 client with appropriate credentials."""
    session = get_boto3_session()
    return session.client("s3")


def get_mediaconvert_client():
    """Get MediaConvert client with transcoding credentials."""
    session = get_boto3_session()
    return session.client("mediaconvert")


def get_iam_client():
    """Get IAM client with transcoding credentials."""
    session = get_boto3_session()
    return session.client("iam")


@task()
def download_and_create_rendition(
    transcoding_job_id: int,
    s3_output_url: str,
    duration_ms: int,
    width: int,
    height: int,
    bitrate: int,
):
    """
    Finalise transcoding job: download file (if needed) and create rendition.

    Args:
        transcoding_job_id: MediaTranscodingJob primary key
        s3_output_url: S3 URL of transcoded file (s3://bucket/path/file.webm)
        duration_ms: Video duration in milliseconds
        width: Video width in pixels
        height: Video height in pixels
        bitrate: Average bitrate
    """
    try:
        transcoding_job = MediaTranscodingJob.objects.get(pk=transcoding_job_id)

        downloader = S3Downloader()

        # Download file from S3 if needed
        if not downloader.should_skip_download():
            logger.info("Downloading transcoded file from S3 to storage backend")
            file_path = downloader.download_from_s3_to_storage(
                s3_url=s3_output_url,
                destination_name=Path(urlparse(s3_output_url).path).name,
            )
        else:
            logger.info("File already in S3 storage backend, skipping download")
            # Extract path from S3 URL (path without bucket)
            o = urlparse(s3_output_url)
            file_path = o.path.lstrip("/")

        duration = duration_ms / 1000

        rendition = MediaRendition.objects.create(
            media=transcoding_job.media,
            transcoding_job=transcoding_job,
            file=file_path,
            width=width,
            height=height,
            duration=duration,
            bitrate=bitrate,
        )

        logger.info("Created rendition (%s) for %s", rendition, rendition.media)

        # Mark job as complete
        transcoding_job.update_status(TranscodingJobStatus.COMPLETE)
    except Exception as e:
        logger.exception(
            "Failed to finalise transcoding job %s: %s", transcoding_job_id, e
        )
        raise


def process_aws_job_status_update(
    job_id: str, aws_status: str, detail: dict, output_detail=None
):
    """
    Process AWS MediaConvert job status update.
    """
    job = get_transcoding_job(job_id)

    # Skip if already complete or finalising
    if job.status in (TranscodingJobStatus.COMPLETE, TranscodingJobStatus.FINALISING):
        logger.debug(
            "Job %s already in terminal/finalising state (%s), skipping update",
            job_id,
            job.status,
        )
        return job

    # Map and update status
    internal_status = map_aws_status_to_internal(aws_status)
    job.update_status(internal_status, detail)

    if internal_status == TranscodingJobStatus.FINALISING and output_detail:
        download_and_create_rendition.enqueue(
            transcoding_job_id=job.pk,
            s3_output_url=output_detail.output_file_path,
            duration_ms=output_detail.duration_ms,
            width=output_detail.width_px,
            height=output_detail.height_px,
            bitrate=output_detail.average_bitrate,
        )

    return job


def get_transcoding_job(job_id: str) -> MediaTranscodingJob:
    try:
        media_transcoding_job = MediaTranscodingJob.objects.get(job_id=job_id)
    except MediaTranscodingJob.DoesNotExist as err:
        logger.warning("Unknown job: %s", job_id)
        raise TranscodingJobNotFound from err

    return media_transcoding_job


class AWSMediaConvertStatuses(str, Enum):
    """
    AWS MediaConvert job status values.

    These map to AWS MediaConvert's job status values received from webhooks. AWS also
    provides SUBMITTED and CANCELED statuses which are not currently mapped.
    """

    PROGRESSING = "PROGRESSING"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"


class AWSMediaConvertBackend(AbstractTranscodingBackend):
    """
    AWS MediaConvert transcoding backend orchestrator.
    """

    def __init__(self):
        """Initialise the AWS transcoding backend."""

        self.s3_service = S3Service()
        self.mediaconvert_service = MediaConvertService(
            role=AWS_MEDIACONVERT_ROLE_NAME, queue=AWS_MEDIACONVERT_QUEUE_NAME
        )
        self.job_settings = MediaConvertJobSettings()

    def start_transcode(self, source_file) -> dict:
        """
        Start transcoding a media file using AWS MediaConvert.

        Uses the transcoding profile configured in WAGTAILMEDIA['TRANSCODING_PROFILE'].

        Args:
            source_file: Django file object to transcode (must have 'name' attribute
                        and optionally 'url' for web-accessible files)

        Returns:
            dict: MediaConvert CreateJob API response
        """

        # Ensure file is publicly accessible
        source_url = self.s3_service.ensure_file_is_available(
            source_file, AWS_MEDIACONVERT_STORAGE_BUCKET_NAME
        )

        # Build job settings from configured profile
        destination_url = f"s3://{AWS_MEDIACONVERT_STORAGE_BUCKET_NAME}/"
        job_settings = self.job_settings.get_settings(source_url, destination_url)

        # Create transcode job
        response = self.mediaconvert_service.create_transcode_job(
            source_url, destination_url, job_settings
        )

        return response

    def stop_transcode(self, task_id: str):
        """Stop a running MediaConvert transcode job."""

        raise NotImplementedError("Stop transcode is not yet implemented")


class S3Service:
    """
    Handles AWS S3 service operations.

    Provides methods to upload files to S3 and ensure files are accessible
    for transcoding, either by returning existing web URLs or uploading
    local files.
    """

    def upload_file(self, file: File, bucket_name: str, object_name: str) -> dict:
        """
        Upload a file to S3.

        Args:
            file: File object to upload (must be readable)
            bucket_name: Target S3 bucket name
            object_name: Object key/path in S3

        Returns:
            dict: S3 put_object response
        """
        import botocore.exceptions as botocore_exceptions

        s3_client = get_s3_client()

        try:
            return s3_client.put_object(Body=file, Bucket=bucket_name, Key=object_name)
        except botocore_exceptions.ClientError as err:
            raise S3UploadError(f"Failed to upload file to S3: {err}") from err

    def ensure_file_is_available(self, source_file: File, bucket_name: str) -> str:
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


class S3Downloader:
    """
    Downloads transcoded files from S3 to the storage backend configured in Django
    settings.

    Skips download when storage backend uses the same S3 bucket.
    """

    def __init__(self):
        self._download_progress = 0
        self._download_total = 0
        self._last_log_time = 0

    def log_download_progress(self, num_bytes: int):
        """
        Progress callback for S3 downloads.

        Logs progress (at most once per second).
        """
        import time

        self._download_progress += num_bytes
        current_time = time.time()

        if current_time - self._last_log_time >= 1.0:
            if self._download_total > 0:
                percent = (self._download_progress / self._download_total) * 100
                mb_progress = self._download_progress / (1024 * 1024)
                mb_total = self._download_total / (1024 * 1024)
                logger.info(
                    f"Download progress: {percent:.1f}% ({mb_progress:.1f}MB / {mb_total:.1f}MB)"
                )
            else:
                mb_progress = self._download_progress / (1024 * 1024)
                logger.info(f"Download progress: {mb_progress:.1f}MB downloaded")

            self._last_log_time = current_time

    def download_from_s3_to_storage(
        self,
        s3_url: str,
        destination_name: str,
        storage_backend: Storage | None = None,
    ) -> str:
        """
        Download file from S3 and save to the storage backend configured in Django
        settings.

        Uses temporary file and streaming to handle large files without loading entire
        file into memory.

        Args:
            s3_url: S3 URL (s3://bucket/key format)
            destination_name: Desired filename/path in storage
            storage_backend: Storage backend to use (defaults to default_storage)

        Returns:
            str: Actual saved path in storage
        """
        storage = storage_backend or default_storage

        # Parse S3 URL
        parsed_url = urlparse(s3_url)
        bucket = parsed_url.netloc
        key = parsed_url.path.lstrip("/")

        logger.info(f"Downloading {s3_url} to storage as {destination_name}")

        s3_client = get_s3_client()

        # Get file size for logging
        try:
            head_response = s3_client.head_object(Bucket=bucket, Key=key)
            self._download_total = head_response["ContentLength"]
            self._download_progress = 0
            logger.info(f"File size: {self._download_total / (1024 * 1024):.1f}MB")
        except Exception as e:
            logger.warning(f"Could not get file size: {e}")
            self._download_total = 0

        # Use suffix to preserve extension
        file_ext = Path(key).suffix

        with tempfile.NamedTemporaryFile(suffix=file_ext, delete=False) as temp_file:
            temp_path = Path(temp_file.name)

            try:
                # Download from S3 to temp file
                logger.info(f"Downloading to temp file: {temp_file.name}")
                s3_client.download_file(
                    Bucket=bucket,
                    Key=key,
                    Filename=temp_file.name,
                    Callback=self.log_download_progress,
                )

                temp_file.close()

                # Get file size for logging
                file_size = temp_path.stat().st_size
                logger.info(f"Downloaded {file_size / 1024 / 1024:.2f} MB from S3")

                # Open temp file and wrap in Django File
                # This allows streaming without loading into memory
                with open(temp_path, "rb") as f:
                    django_file = File(f, name=Path(destination_name).name)

                    # Save to storage backend
                    saved_path = storage.save(destination_name, django_file)

                logger.info(f"Saved to storage at: {saved_path}")

                return saved_path
            finally:
                # Clean up temp file
                if temp_path.exists():
                    temp_path.unlink()

    def should_skip_download(self) -> bool:
        """
        Determine if download can be skipped.

        When storage backend is S3 and uses the same bucket as MediaConvert output,
        the transcoded file is already in its final location, so no download needed.

        Uses duck typing to detect S3 storage by checking for S3-specific attributes
        rather than relying on class naming conventions.

        Returns:
            bool: True if download can be skipped, False otherwise
        """
        # Check if storage has S3-specific attributes
        has_bucket_attr = hasattr(default_storage, "bucket_name")
        has_bucket_method = hasattr(default_storage, "bucket")

        if not (has_bucket_attr or has_bucket_method):
            logger.debug(
                f"Storage backend ({default_storage.__class__.__name__}) does not have S3 bucket attributes, download required"
            )
            return False

        # Get bucket name from storage
        storage_bucket = None
        if has_bucket_attr:
            storage_bucket = default_storage.bucket_name
        elif has_bucket_method:
            # Some implementations use bucket() method
            bucket = default_storage.bucket()
            storage_bucket = getattr(bucket, "name", None)

        # Fallback to settings if we can't get it from storage
        if not storage_bucket:
            storage_bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", None)

        if not storage_bucket:
            logger.debug("Cannot determine storage bucket name")
            return False

        # Check if using same bucket
        if storage_bucket == AWS_MEDIACONVERT_STORAGE_BUCKET_NAME:
            logger.info(
                f"Storage uses same S3 bucket ({storage_bucket}), skipping download"
            )
            return True

        logger.debug(
            f"Different S3 buckets (storage: {storage_bucket}, "
            f"mediaconvert: {AWS_MEDIACONVERT_STORAGE_BUCKET_NAME}), download required"
        )
        return False


class MediaConvertJobSettings:
    """
    MediaConvert job settings configurations.

    Provides static methods to generate job settings dictionaries for different
    transcoding profiles and output formats.
    """

    @staticmethod
    def get_settings(source_url: str, destination_bucket: str) -> dict:
        """
        Get job settings for transcoding.

        Loads profile JSON from configured file path, injects source file
        and destination paths, and returns complete job settings.

        Args:
            source_url: S3 URL of source file (s3://bucket/file)
            destination_bucket: S3 URL of destination directory (s3://bucket/dir/)

        Returns:
            dict: Complete MediaConvert job settings dictionary

        Raises:
            ImproperlyConfigured: If TRANSCODING_PROFILE is not configured
            FileNotFoundError: If JSON file doesn't exist
            json.JSONDecodeError: If JSON is invalid
        """
        # Get profile file path from settings
        profile_path = wagtailmedia_settings.TRANSCODING_PROFILE
        if not profile_path:
            raise ImproperlyConfigured(
                "TRANSCODING_PROFILE not configured. "
                "Set WAGTAILMEDIA['TRANSCODING_PROFILE'] to path of profile JSON file."
            )

        if not Path(profile_path).is_absolute():
            profile_path = Path(settings.BASE_DIR) / profile_path

        with open(profile_path, encoding="utf-8") as f:
            profile_data = json.load(f)

        settings_dict = profile_data["Settings"]

        return MediaConvertJobSettings._set_source_and_destination(
            settings_dict, source_url, destination_bucket
        )

    @staticmethod
    def _set_source_and_destination(
        settings_dict: dict, source_url: str, destination_bucket: str
    ) -> dict:
        """
        add source file and destination paths into MediaConvert job settings.
        # Inject source and destination into the loaded settings

        Modifies the settings dict in-place to add FileInput and Destination values.
        Assumes settings_dict has been validated by system checks.

        Args:
            settings_dict: MediaConvert job settings from JSON file
            source_url: S3 URL of source file
            destination_bucket: S3 URL of destination directory

        Returns:
            dict: Modified settings with injected values
        """
        settings_dict["Inputs"][0]["FileInput"] = source_url
        output_group_settings = settings_dict["OutputGroups"][0]["OutputGroupSettings"]
        output_group_settings.setdefault("FileGroupSettings", {})["Destination"] = (
            destination_bucket
        )

        return settings_dict


class MediaConvertService:
    """
    Handles AWS MediaConvert service operations.

    Manages MediaConvert job creation, IAM role resolution, and client initialization.
    """

    def __init__(self, role, queue):
        """Initialise MediaConvert service with configuration."""
        self.role = role
        self.queue = queue

    def get_role_arn(self) -> str:
        """
        Get the IAM role ARN for MediaConvert jobs.

        Retrieves and caches the ARN for the configured IAM role that
        MediaConvert will assume when executing transcode jobs.

        Returns:
            str: Full IAM role ARN (arn:aws:iam::account-id:role/role-name)

        Raises:
            IAMGetRoleError: If role cannot be found or IAM access is denied
        """
        import botocore.exceptions as botocore_exceptions

        iam_client = get_iam_client()

        try:
            response = iam_client.get_role(RoleName=self.role)
            return response["Role"]["Arn"]
        except botocore_exceptions.ClientError as err:
            raise IAMGetRoleError(
                f"Failed to get IAM role '{self.role}': {err}"
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
            MediaConvertJobError: If job creation fails (invalid settings, permissions, etc.)
        """
        import botocore.exceptions as botocore_exceptions

        mediaconvert = get_mediaconvert_client()

        role_arn = self.get_role_arn()

        try:
            response = mediaconvert.create_job(
                Role=role_arn,
                Settings=job_settings,
                Queue=self.queue,
            )
            return response
        except botocore_exceptions.ClientError as err:
            raise MediaConvertJobError(
                f"Failed to create MediaConvert job: {err}"
            ) from err
