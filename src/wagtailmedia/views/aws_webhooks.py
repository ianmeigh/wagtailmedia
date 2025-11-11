from __future__ import annotations

import hmac
import json
import logging

from dataclasses import dataclass

from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from wagtailmedia.transcoding_backends.aws.exceptions import (
    DataValidationError,
    TranscodingJobNotFound,
)
from wagtailmedia.transcoding_backends.aws.parsers import JobDetail
from wagtailmedia.transcoding_backends.aws.services import process_aws_job_status_update


logger = logging.getLogger(__name__)


AWS_STATUS_COMPLETE = "COMPLETE"


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

    job_detail: JobDetail

    @classmethod
    def from_request_body(cls, body: bytes):
        """Parse and validate entire webhook payload."""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as err:
            raise DataValidationError(f"Invalid JSON: {err}") from err

        job_detail = JobDetail.from_eventbridge_webhook(payload)

        return cls(job_detail=job_detail)


@method_decorator(csrf_exempt, name="dispatch")
class AWSTranscodingWebhookView(View):
    """
    Webhook endpoint for receiving transcoding job status updates.

    This view handles POST requests from the AWS EventBridge API Destination to update job status.

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
                'queue': 'arn:aws:mediaconvert:eu-west-2:182186043439:queues/AWS_MEDIACONVERT_QUEUE_NAME',
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
            logger.warning("Webhook request with invalid authentication")
            return JsonResponse({"error": "Unauthorized"}, status=401)

        # Parse and validate webhook payload
        try:
            validated_payload = WebhookPayload.from_request_body(request.body)
        except DataValidationError as err:
            logger.error("Invalid webhook payload: %s", err)
            return JsonResponse({"error": str(err)}, status=400)

        job_detail = validated_payload.job_detail

        logger.debug(
            "Webhook received for Job ID: %s, status: %s",
            job_detail.job_id,
            job_detail.status,
        )

        try:
            job = process_aws_job_status_update(
                job_id=job_detail.job_id,
                aws_status=job_detail.status,
                detail=job_detail.raw_detail,
                output_detail=job_detail.get_output_detail(),
            )
        except TranscodingJobNotFound as err:
            logger.warning("Webhook for unknown job: %s", job_detail.job_id)
            return JsonResponse({"error": str(err)}, status=404)
        except DataValidationError as err:
            logger.error("Invalid job data: %s", err)
            return JsonResponse({"error": str(err)}, status=400)
        except KeyError as err:
            logger.error("Invalid status: %s", err)
            return JsonResponse({"error": str(err)}, status=400)

        return JsonResponse(
            {
                "job_id": job.job_id,
                "job_status": job_detail.status,
            },
            status=200,
        )

    def _verify_api_key(self, request):
        """
        Verify API Key authentication.

        Expects API key in X-API-Key header.
        """
        expected_key = getattr(settings, "AWS_WEBHOOK_API_KEY", None)

        # URL pattern shouldn't have been included but just in case fail the verification
        if not expected_key:
            logger.error("Webhook received but missing AWS_WEBHOOK_API_KEY in settings")

            return False

        provided_key = request.headers.get("X-API-Key") or request.headers.get(
            "X-Api-Key"
        )
        if not provided_key:
            return False

        # Constant-time comparison
        return hmac.compare_digest(provided_key, expected_key)
