from __future__ import annotations

import logging

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import boto3
import botocore.exceptions as botocore_exceptions

from django.core.files import File
from django_tasks import task

from wagtailmedia.models import (
    MediaRendition,
    MediaTranscodingJob,
    TranscodingJobStatus,
)
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
    map to FINALIZING. The background task will update to COMPLETE when finished.
    """
    status_map = {
        "COMPLETE": TranscodingJobStatus.COMPLETE,
        "ERROR": TranscodingJobStatus.FAILED,
        "PROGRESSING": TranscodingJobStatus.PROGRESSING,
    }

    return status_map[status.upper()]


def get_boto3_session() -> boto3.Session:
    """
    Get boto3 session for transcoding operations.

    Uses MediaConvert-specific credentials if configured, otherwise falls back to
    default boto3 credential chain.
    """

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
def create_rendition(
    transcoding_job_id: int,
    output_file_path: str,
    duration_ms: int,
    width: int,
    height: int,
    bitrate: int,
):
    # TODO: If storage backend not S3 (or same bucket) copy the file to the default storage backend
    # 1. Get backend (from django.core.files.storage import default_storage)
    # 2. Save file content to file like object
    # 3. Create model instance with file like object
    # 4. Remove from S3?
    transcoding_job = MediaTranscodingJob.objects.get(pk=transcoding_job_id)

    o = urlparse(output_file_path)
    s3_key = o.path.lstrip("/")
    duration = duration_ms / 1000

    # Create the MediaRendition linked to the media from the job
    rendition = MediaRendition.objects.create(
        media=transcoding_job.media,
        transcoding_job=transcoding_job,
        file=s3_key,
        width=width,
        height=height,
        duration=duration,
        bitrate=bitrate,
    )

    logger.info("Created rendition (%s) for %s", rendition, rendition.media)


def process_aws_job_status_update(
    job_id: str, aws_status: str, detail: dict, output_detail=None
):
    """
    Process AWS MediaConvert job status update.
    """
    job = get_transcoding_job(job_id)

    # Skip if already complete
    if job.status == TranscodingJobStatus.COMPLETE:
        logger.debug("Job %s already complete, skipping update", job_id)
        return job

    # Map and update status
    internal_status = map_aws_status_to_internal(aws_status)
    job.update_status(internal_status, detail)

    # Create rendition if complete
    if internal_status == TranscodingJobStatus.COMPLETE and output_detail:
        # Enqueue as background task
        create_rendition.enqueue(
            job.pk,
            output_file_path=output_detail.output_file_path,
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

        # Build job settings
        destination_url = f"s3://{AWS_MEDIACONVERT_STORAGE_BUCKET_NAME}/"
        job_settings = self.job_settings.webm_vp8_settings(source_url, destination_url)

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
