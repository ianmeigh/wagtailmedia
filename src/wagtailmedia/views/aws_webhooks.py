from __future__ import annotations

import hmac
import json
import logging

from dataclasses import dataclass

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django_tasks import task

from wagtailmedia.models import (
    MediaRendition,
    MediaTranscodingJob,
    TranscodingJobStatus,
)
from wagtailmedia.settings import wagtailmedia_settings


logger = logging.getLogger(__name__)


class WebhookValidationError(Exception):
    """Raised when webhook data fails validation."""

    pass


AWS_STATUS_COMPLETE = "COMPLETE"


@dataclass
class OutputDetail:
    """Represents the first outputDetails item from aAWS MediaConvert webhook with a status of COMPLETE."""

    output_file_path: str
    duration_ms: int
    width_px: int
    height_px: int
    average_bitrate: int

    @classmethod
    def from_webhook_detail(cls, detail: dict) -> OutputDetail:
        """
        Parse and validate the first outputDetails item from AWS webhook detail.

        Expects detail to have structure:
        {
            'timestamp': 0,
            'accountId': 'ACCOUNT_ID',
            'queue': 'arn:aws:mediaconvert:AWS_REGION:AWS_ACCOUNT_ID:queues/Default',
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
            WebhookValidationError: If structure is invalid or missing required fields
        """
        try:
            output_item = detail["outputGroupDetails"][0]["outputDetails"][0]
            output_paths = output_item["outputFilePaths"]

            if not output_paths or not isinstance(output_paths, list):
                raise WebhookValidationError("outputFilePaths must be a non-empty list")

            video_details = output_item["videoDetails"]

            return cls(
                output_file_path=output_paths[0],
                duration_ms=output_item["durationInMs"],
                width_px=video_details["widthInPx"],
                height_px=video_details["heightInPx"],
                average_bitrate=video_details["averageBitrate"],
            )
        except (KeyError, IndexError, TypeError) as e:
            raise WebhookValidationError(
                f"Invalid COMPLETE webhook structure: {e}"
            ) from e


@dataclass
class WebhookPayload:
    """
    Parse and validate AWS MediaConvert webhook payload.

    Expects structure:
    {
        'version': '0',
        'id': 'UUID',
        'detail-type': 'MediaConvert Job State Change',
        'source': 'aws.mediaconvert',
        'account': 'ACCOUNT_ID',
        'time': '1970-01-01T00:00:00Z',
        'region': 'AWS_REGION',
        'resources': ['arn:aws:mediaconvert:AWS_REGION:ACCOUNT_ID:jobs/JOB_ID'],
        'detail': {
            'timestamp': 0,
            'jobId': 'JOB_ID',
            'status': 'PROGRESSING/COMPLETE/ERROR'
        }
    }
    """

    job_id: str
    status: str
    detail: dict
    output_detail: OutputDetail | None = None

    @classmethod
    def from_request_body(cls, body: bytes):
        """Parse and validate entire webhook payload."""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise WebhookValidationError(f"Invalid JSON: {e}") from e

        try:
            detail = payload["detail"]
            job_id = detail["jobId"]
            status = detail["status"]
        except KeyError as e:
            raise WebhookValidationError(f"Missing required field: {e}") from e

        # Only validate output details if status is COMPLETE
        output_detail = None
        if status == AWS_STATUS_COMPLETE:
            output_detail = OutputDetail.from_webhook_detail(detail)

        return cls(
            job_id=job_id, status=status, detail=detail, output_detail=output_detail
        )


@task()
def _create_rendition(
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

    s3_key = output_file_path.split("/", 3)[3]
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


@method_decorator(csrf_exempt, name="dispatch")
class AWSTranscodingWebhookView(View):
    """
    Webhook endpoint for receiving transcoding job status updates.

    This view handles POST requests from the AWS EventBridge API Destination to update
    job status.

    Configuration:
        WAGTAILMEDIA = {
            "WEBHOOK_API_KEY": "API_KEY",  # For auth
        }
    """

    def post(self, request):
        """Handle POST requests with transcoding status updates."""

        # Verify authentication
        if not self._verify_api_key(request):
            logger.warning(
                "Webhook request with invalid authentication",
            )
            return JsonResponse({"error": "Unauthorized"}, status=401)

        # Validate webhook
        try:
            validated_payload = WebhookPayload.from_request_body(request.body)
        except WebhookValidationError as e:
            logger.error("Invalid webhook: %s", e)
            return JsonResponse({"error": str(e)}, status=400)

        # Get transcoding job
        try:
            media_transcoding_job = MediaTranscodingJob.objects.get(
                job_id=validated_payload.job_id
            )
        except MediaTranscodingJob.DoesNotExist:
            logger.warning("Webhook for unknown job: %s", validated_payload.job_id)
            return JsonResponse(
                {"error": f"Job not found: {validated_payload.job_id}"}, status=404
            )

        # Map external status to internal status
        try:
            status = self._map_status(validated_payload.status)
        except KeyError:
            logger.error(
                "Webhook received with invalid status: %s",
                validated_payload.status,
            )
            return JsonResponse(
                {"error": f"Invalid status: {validated_payload.status}"}, status=400
            )

        logger.debug(
            "Webhook received for Job ID: %s, status: %s, with metadata: %s",
            validated_payload.job_id,
            validated_payload.status,
            validated_payload.detail,
        )

        # If the transcoding job object is already complete, skip updating
        if media_transcoding_job.status != TranscodingJobStatus.COMPLETE:
            self._update_transcoding_job(
                media_transcoding_job, status, validated_payload.detail
            )

            # If the response status will mark the transcoding as complete, also create the media renditions
            if status is TranscodingJobStatus.COMPLETE:
                _create_rendition.enqueue(
                    media_transcoding_job.pk,
                    output_file_path=validated_payload.output_detail.output_file_path,
                    duration_ms=validated_payload.output_detail.duration_ms,
                    width=validated_payload.output_detail.width_px,
                    height=validated_payload.output_detail.height_px,
                    bitrate=validated_payload.output_detail.average_bitrate,
                )

        return JsonResponse(
            {
                "job_id": validated_payload.job_id,
                "job_status": validated_payload.status,
            },
            status=200,
        )

    def _update_transcoding_job(self, transcoding_job, status, job_metadata):
        old_status = transcoding_job.status
        transcoding_job.status = status
        transcoding_job.metadata = job_metadata
        transcoding_job.save()

        logger.info(
            "Updated job %s status from %s to %s",
            transcoding_job.job_id,
            old_status,
            transcoding_job.status,
        )

    def _verify_api_key(self, request):
        """
        Verify API Key authentication.

        Expects API key in X-API-Key header.
        """
        expected_key = wagtailmedia_settings.WEBHOOK_API_KEY

        # URL pattern shouldn't have been included but just in case fail the verification
        if not expected_key:
            logger.error(
                "Webhook received but missing WEBHOOK_API_KEY in WAGTAIL_MEDIA settings"
            )

            return False

        provided_key = request.headers.get("X-API-Key") or request.headers.get(
            "X-Api-Key"
        )
        if not provided_key:
            return False

        # Constant-time comparison
        return hmac.compare_digest(provided_key, expected_key)

    def _map_status(self, status):
        """
        Map external service status to internal TranscodingJobStatus.

        Args:
            status: Status string from external service

        Returns:
            Internal status value or None if invalid
        """
        status_map = {
            "COMPLETE": TranscodingJobStatus.COMPLETE,
            "ERROR": TranscodingJobStatus.FAILED,
            "PROGRESSING": TranscodingJobStatus.PROGRESSING,
        }

        return status_map[status.upper()]
