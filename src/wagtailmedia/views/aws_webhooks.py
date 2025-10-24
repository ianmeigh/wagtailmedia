from __future__ import annotations

import hmac
import json
import logging

from dataclasses import dataclass

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from wagtailmedia.models import (
    MediaRendition,
    MediaTranscodingJob,
    TranscodingJobStatus,
)
from wagtailmedia.settings import wagtailmedia_settings


logger = logging.getLogger(__name__)


@dataclass
class VideoDetails:
    width_px: int | None = None
    height_px: int | None = None
    average_bitrate: int | None = None

    @classmethod
    def from_dict(cls, data: dict):
        """Create from AWS videoDetails dict."""
        return cls(
            width_px=data.get("widthInPx"),
            height_px=data.get("heightInPx"),
            average_bitrate=data.get("averageBitrate"),
        )


@dataclass
class OutputDetail:
    output_file_paths: list[str]
    duration_ms: int | None = None
    video_details: VideoDetails | None = None

    @classmethod
    def from_dict(cls, data: dict):
        """Create from AWS outputDetails dict."""
        video_details_data = data.get("videoDetails", {})
        return cls(
            output_file_paths=data.get("outputFilePaths", []),
            duration_ms=data.get("durationInMs", 0),
            video_details=VideoDetails.from_dict(video_details_data)
            if video_details_data
            else None,
        )


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

    EventBridge Payload Format:
        {
            'version': '0',
            'id': 'UUID',
            'detail-type': 'MediaConvert Job State Change',
            'source': 'aws.mediaconvert',
            'account': 'ACCOUNT_ID',
            'time': '1970-01-01T00:00:00Z',
            'region': 'eu-west-2',
            'resources': ['arn:aws:mediaconvert:eu-west-2:ACCOUNT_ID:jobs/JOB_ID'],
            'detail': {
                'timestamp': 0,
                'accountId': 'ACCOUNT_ID',
                'queue': 'arn:aws:mediaconvert:eu-west-2:182186043439:queues/Default',
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

        # Parse request body
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            logger.error("Webhook received with invalid JSON")
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        # extract job data
        try:
            detail = payload["detail"]
        except KeyError:
            logger.error("Webhook received with missing 'detail' field")
            return JsonResponse({"error": "Missing required field: detail"}, status=400)

        job_id = detail.get("jobId")
        job_status = detail.get("status")
        job_metadata = {}

        if not job_id or not job_status:
            logger.error(
                "Webhook received with missing required fields",
            )
            return JsonResponse(
                {"error": "Missing required fields: job_id and status"}, status=400
            )

        try:
            media_transcoding_job = MediaTranscodingJob.objects.get(job_id=job_id)
        except MediaTranscodingJob.DoesNotExist:
            logger.warning(
                "Webhook received for unknown job_id: %s",
                job_id,
            )
            return JsonResponse({"error": f"Job not found: {job_id}"}, status=404)

        # Map external status to internal status
        try:
            status = self._map_status(job_status)
        except KeyError:
            logger.error(
                "Webhook received with invalid status: %s",
                job_status,
            )
            return JsonResponse({"error": f"Invalid status: {job_status}"}, status=400)

        if status is TranscodingJobStatus.COMPLETE:
            try:
                job_metadata = detail["outputGroupDetails"][0]["outputDetails"]
                output_details = [OutputDetail.from_dict(item) for item in job_metadata]
            except (KeyError, IndexError, TypeError) as e:
                logger.error("COMPLETE status but missing outputGroupDetails: %s", e)
                return JsonResponse(
                    {"error": "COMPLETE status requires outputGroupDetails"}, status=400
                )

        logger.debug(
            "Webhook received for Job ID: %s, status: %s, with metadata: %s",
            job_id,
            job_status,
            job_metadata,
        )

        # If the transcoding job object is already complete, skip updating
        if media_transcoding_job.status != TranscodingJobStatus.COMPLETE:
            self._update_transcoding_job(job_id, status, job_metadata)

            # If the response status will mark the transcoding as complete, also create the media renditions
            if status is TranscodingJobStatus.COMPLETE and output_details:
                self._create_rendition(job_id, output_details[0])

        return JsonResponse({"job_id": job_id, "job_status": job_status}, status=200)

    def _update_transcoding_job(self, job_id, status, job_metadata):
        media_transcoding_job = MediaTranscodingJob.objects.get(job_id=job_id)

        old_status = media_transcoding_job.status
        media_transcoding_job.status = status
        media_transcoding_job.metadata = job_metadata
        media_transcoding_job.save()

        logger.info(
            "Updated job %s status from %s to %s",
            job_id,
            old_status,
            media_transcoding_job.status,
        )

    def _create_rendition(self, job_id, output_detail: OutputDetail):
        # TODO: If storage backend not S3 (or same bucket) copy the file to the default storage backend
        # 1. Get backend (from django.core.files.storage import default_storage)
        # 2. Save file content to file like object
        # 3. Create model instance with file like object
        # 4. Remove from S3?
        try:
            s3_full_path = output_detail.output_file_paths[0]
            s3_key = s3_full_path.split("/", 3)[3]
        except (IndexError, TypeError) as e:
            logger.error("Failed to parse rendition data for job %s: %s", job_id, e)
            return

        media_transcoding_job = MediaTranscodingJob.objects.get(job_id=job_id)

        # Extract output detail
        width = (
            output_detail.video_details.width_px
            if output_detail.video_details
            else None
        )
        height = (
            output_detail.video_details.height_px
            if output_detail.video_details
            else None
        )
        bitrate = (
            output_detail.video_details.average_bitrate
            if output_detail.video_details
            else None
        )
        # Convert duration from milliseconds to seconds
        duration = output_detail.duration_ms / 1000 if output_detail.duration_ms else 0

        # Create the MediaRendition linked to the media from the job
        MediaRendition.objects.create(
            media=media_transcoding_job.media,
            transcoding_job=media_transcoding_job,
            file=s3_key,  # Just the S3 key, not full s3:// URL
            width=width,
            height=height,
            duration=duration,
            bitrate=bitrate,
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
