import json

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from wagtailmedia.models import (
    Media,
    MediaTranscodingJob,
    MediaType,
    TranscodingJobStatus,
)


@override_settings(ROOT_URLCONF="testapp.urls_aws_webhook")
class AWSTranscodingWebhookAuthenticationTests(TestCase):
    """Tests for AWS webhook authentication logic."""

    def setUp(self):
        """Set up test fixtures."""

        media = Media(
            title="Test media file",
            file=ContentFile("Test video content", name="test.mp4"),
            type=MediaType.VIDEO,
        )
        media.save()

        MediaTranscodingJob.objects.create(
            media=media,
            job_id="test-job",
            status=TranscodingJobStatus.PENDING,
            backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
        )

        self.webhook_url = "/aws-transcoding-test/"

        # Valid minimal EventBridge payload
        self.valid_payload = {
            "version": "0",
            "id": "test-uuid",
            "detail-type": "MediaConvert Job State Change",
            "source": "aws.mediaconvert",
            "detail": {
                "jobId": "test-job",
                "status": "PROGRESSING",
            },
        }

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_valid_api_key_in_x_api_key_header(self):
        """Test successful authentication with valid API key in X-API-Key header."""
        response = self.client.post(
            self.webhook_url,
            data=json.dumps(self.valid_payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )
        content = json.loads(response.content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(content, {"job_id": "test-job", "job_status": "PROGRESSING"})

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "invalid-api-key"})
    def test_invalid_api_key_returns_401(self):
        response = self.client.post(
            self.webhook_url,
            data=json.dumps(self.valid_payload),
            content_type="application/json",
            HTTP_X_API_KEY="wrong-api-key",
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn("Unauthorized", response.json()["error"])

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_missing_api_key_header_return_401(self):
        response = self.client.post(
            self.webhook_url,
            data=json.dumps(self.valid_payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn("Unauthorized", response.json()["error"])

    @override_settings(WAGTAILMEDIA={})
    def test_no_api_key_configured_in_settings_returns_401(self):
        """Test that webhook fails when no API key is configured in settings."""
        response = self.client.post(
            self.webhook_url,
            data=json.dumps(self.valid_payload),
            content_type="application/json",
            HTTP_X_API_KEY="any-key",
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn("Unauthorized", response.json()["error"])


@override_settings(ROOT_URLCONF="testapp.urls_aws_webhook")
class AWSTranscodingWebhookRequestParsingTests(TestCase):
    """Tests for AWS webhook request parsing logic."""

    def setUp(self):
        """Set up test fixtures."""

        media = Media(
            title="Test media file",
            file=ContentFile("Test video content", name="test.mp4"),
            type=MediaType.VIDEO,
        )
        media.save()

        self.job = MediaTranscodingJob.objects.create(
            media=media,
            job_id="test-job",
            status=TranscodingJobStatus.PENDING,
            backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
        )

        self.webhook_url = "/aws-transcoding-test/"

        # Valid minimal EventBridge payload
        self.valid_payload = {
            "version": "0",
            "id": "test-uuid",
            "detail-type": "MediaConvert Job State Change",
            "source": "aws.mediaconvert",
            "detail": {
                "jobId": "test-job",
                "status": "PROGRESSING",
            },
        }

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_invalid_json_in_request_body(self):
        """Test that malformed JSON returns 400."""
        response = self.client.post(
            self.webhook_url,
            data="{ invalid json }",
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid JSON", response.json()["error"])

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_missing_detail_key_in_payload(self):
        """Test that missing 'detail' key returns 400."""
        payload = {
            "version": "0",
            "id": "test-uuid",
            # Missing 'detail' key
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        self.assertEqual(response.status_code, 400)

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_missing_job_id_in_detail(self):
        """Test that missing 'jobId' in detail returns 400."""
        payload = {
            "version": "0",
            "id": "test-uuid",
            "detail": {
                # Missing 'jobId'
                "status": "PROGRESSING",
            },
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing required fields", response.json()["error"])

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_missing_status_in_detail(self):
        """Test that missing 'status' in detail returns 400."""
        payload = {
            "version": "0",
            "id": "test-uuid",
            "detail": {
                "jobId": "test-job",
                # Missing 'status'
            },
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing required fields", response.json()["error"])


@override_settings(ROOT_URLCONF="testapp.urls_aws_webhook")
class AWSTranscodingWebhookStatusMappingTests(TestCase):
    """Tests for AWS webhook status mapping logic."""

    def setUp(self):
        """Set up test fixtures."""

        self.media = Media(
            title="Test media file",
            file=ContentFile("Test video content", name="test.mp4"),
            type=MediaType.VIDEO,
        )
        self.media.save()

        self.job = MediaTranscodingJob.objects.create(
            media=self.media,
            job_id="test-job",
            status=TranscodingJobStatus.PENDING,
            backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
        )

        self.webhook_url = "/aws-transcoding-test/"

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_invalid_status_returns_400(self):
        """Test that invalid status string returns 400."""
        payload = {
            "version": "0",
            "id": "test-uuid",
            "detail": {
                "jobId": "test-job",
                "status": "UNKNOWN_STATUS",
            },
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid status", response.json()["error"])
