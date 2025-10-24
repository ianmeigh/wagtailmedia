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
    def test_valid_status_mappings_return_200(self):
        """Test that all valid status strings are mapped correctly and return 200."""
        valid_statuses = [
            ("PROGRESSING", TranscodingJobStatus.PROGRESSING),
            ("ERROR", TranscodingJobStatus.FAILED),
            ("COMPLETE", TranscodingJobStatus.COMPLETE),
        ]

        for aws_status, expected_internal_status in valid_statuses:
            with self.subTest(aws_status=aws_status):
                job = MediaTranscodingJob.objects.create(
                    media=self.media,
                    job_id=f"test-job-{aws_status.lower()}",
                    status=TranscodingJobStatus.PENDING,
                    backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
                )

                payload = {
                    "version": "0",
                    "id": "test-uuid",
                    "detail": {
                        "jobId": f"test-job-{aws_status.lower()}",
                        "status": aws_status,
                    },
                }

                # Add required outputGroupDetails for COMPLETE status
                if aws_status == "COMPLETE":
                    payload["detail"]["outputGroupDetails"] = [
                        {
                            "outputDetails": [
                                {
                                    "outputFilePaths": ["s3://bucket/media/test.mp4"],
                                    "durationInMs": 5000,
                                    "videoDetails": {
                                        "widthInPx": 1920,
                                        "heightInPx": 1080,
                                    },
                                }
                            ]
                        }
                    ]

                response = self.client.post(
                    self.webhook_url,
                    data=json.dumps(payload),
                    content_type="application/json",
                    HTTP_X_API_KEY="valid-api-key",
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {
                        "job_id": f"test-job-{aws_status.lower()}",
                        "job_status": aws_status,
                    },
                )

                # Verify the status was mapped and saved correctly
                job.refresh_from_db()
                self.assertEqual(job.status, expected_internal_status)

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


@override_settings(ROOT_URLCONF="testapp.urls_aws_webhook")
class AWSTranscodingWebhookJobUpdateTests(TestCase):
    """Tests for AWS webhook job update logic."""

    def setUp(self):
        """Set up test fixtures."""

        self.media = Media(
            title="Test media file",
            file=ContentFile("Test video content", name="test.mp4"),
            type=MediaType.VIDEO,
        )
        self.media.save()

        self.webhook_url = "/aws-transcoding-test/"

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_status_transitions(self):
        """Test all valid status transitions."""
        complete_payload = {
            "outputGroupDetails": [
                {
                    "outputDetails": [
                        {
                            "outputFilePaths": ["s3://bucket/media/test.mp4"],
                            "durationInMs": 5000,
                            "videoDetails": {
                                "widthInPx": 1920,
                                "heightInPx": 1080,
                            },
                        }
                    ]
                }
            ]
        }

        test_cases = [
            (
                TranscodingJobStatus.PENDING,
                "PROGRESSING",
                TranscodingJobStatus.PROGRESSING,
                None,
            ),
            (
                TranscodingJobStatus.PENDING,
                "COMPLETE",
                TranscodingJobStatus.COMPLETE,
                complete_payload,
            ),
            (TranscodingJobStatus.PENDING, "ERROR", TranscodingJobStatus.FAILED, None),
            (
                TranscodingJobStatus.PROGRESSING,
                "COMPLETE",
                TranscodingJobStatus.COMPLETE,
                complete_payload,
            ),
            (
                TranscodingJobStatus.PROGRESSING,
                "ERROR",
                TranscodingJobStatus.FAILED,
                None,
            ),
        ]

        for idx, (
            initial_status,
            aws_status,
            expected_status,
            extra_payload,
        ) in enumerate(test_cases):
            with self.subTest(transition=f"{initial_status} -> {aws_status}"):
                job = MediaTranscodingJob.objects.create(
                    media=self.media,
                    job_id=f"test-job-transition-{idx}",
                    status=initial_status,
                    backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
                )

                payload = {
                    "version": "0",
                    "id": "test-uuid",
                    "detail": {
                        "jobId": f"test-job-transition-{idx}",
                        "status": aws_status,
                    },
                }

                if extra_payload:
                    payload["detail"].update(extra_payload)

                response = self.client.post(
                    self.webhook_url,
                    data=json.dumps(payload),
                    content_type="application/json",
                    HTTP_X_API_KEY="valid-api-key",
                )

                self.assertEqual(response.status_code, 200)
                job.refresh_from_db()
                self.assertEqual(job.status, expected_status)

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_job_already_complete_skips_update(self):
        """Test that webhook skips update when job is already COMPLETE."""
        job = MediaTranscodingJob.objects.create(
            media=self.media,
            job_id="test-job-complete",
            status=TranscodingJobStatus.COMPLETE,
            backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
        )

        original_updated_at = job.updated_at

        payload = {
            "version": "0",
            "id": "test-uuid",
            "detail": {
                "jobId": "test-job-complete",
                "status": "PROGRESSING",
            },
        }
        self.assertEqual(job.status, TranscodingJobStatus.COMPLETE)
        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        self.assertEqual(response.status_code, 200)

        job.refresh_from_db()
        # Status should remain COMPLETE
        self.assertEqual(job.status, TranscodingJobStatus.COMPLETE)
        # updated_at should not change (job was not saved)
        self.assertEqual(job.updated_at, original_updated_at)

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_metadata_with_output_details_is_saved_when_job_complete(self):
        """Test that job metadata is saved correctly."""
        job = MediaTranscodingJob.objects.create(
            media=self.media,
            job_id="test-job-metadata",
            status=TranscodingJobStatus.PENDING,
            backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
        )

        metadata = [
            {
                "outputFilePaths": ["s3://bucket/media/test.mp4"],
                "durationInMs": 5000,
                "videoDetails": {
                    "widthInPx": 1920,
                    "heightInPx": 1080,
                },
            }
        ]

        payload = {
            "version": "0",
            "id": "test-uuid",
            "detail": {
                "jobId": "test-job-metadata",
                "status": "COMPLETE",
                "outputGroupDetails": [{"outputDetails": metadata}],
            },
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        self.assertEqual(response.status_code, 200)

        job.refresh_from_db()
        self.assertEqual(job.metadata, metadata)

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_rendition_created_with_correct_fields_on_job_completion(self):
        """Test that MediaRendition is created with width, height, duration, bitrate on COMPLETE."""
        job = MediaTranscodingJob.objects.create(
            media=self.media,
            job_id="test-job-rendition-fields",
            status=TranscodingJobStatus.PROGRESSING,
            backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
        )

        payload = {
            "version": "0",
            "id": "test-uuid",
            "detail": {
                "jobId": "test-job-rendition-fields",
                "status": "COMPLETE",
                "outputGroupDetails": [
                    {
                        "outputDetails": [
                            {
                                "outputFilePaths": [
                                    "s3://bucket/media/transcoded/test-video.mp4"
                                ],
                                "durationInMs": 10500,  # 10.5 seconds
                                "videoDetails": {
                                    "widthInPx": 1280,
                                    "heightInPx": 720,
                                    "averageBitrate": 2500000,
                                },
                            }
                        ]
                    }
                ],
            },
        }

        self.assertEqual(job.renditions.count(), 0)

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        self.assertEqual(response.status_code, 200)

        job.refresh_from_db()
        self.assertEqual(job.status, TranscodingJobStatus.COMPLETE)

        # Verify rendition was created and linked
        self.assertIsNotNone(job.renditions.all())

        # Verify rendition fields are populated correctly
        rendition = job.renditions.first()
        self.assertEqual(rendition.width, 1280)
        self.assertEqual(rendition.height, 720)
        self.assertEqual(rendition.duration, 10.5)  # Converted from ms to seconds
        self.assertEqual(rendition.bitrate, 2500000)
        self.assertEqual(rendition.file.name, "media/transcoded/test-video.mp4")

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_progressing_status_does_not_create_rendition(self):
        """Test that PROGRESSING status does NOT create a rendition."""
        job = MediaTranscodingJob.objects.create(
            media=self.media,
            job_id="test-job-progressing-no-rendition",
            status=TranscodingJobStatus.PENDING,
            backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
        )

        payload = {
            "version": "0",
            "id": "test-uuid",
            "detail": {
                "jobId": "test-job-progressing-no-rendition",
                "status": "PROGRESSING",
            },
        }

        self.assertEqual(job.renditions.count(), 0)

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        self.assertEqual(response.status_code, 200)

        job.refresh_from_db()
        self.assertEqual(job.status, TranscodingJobStatus.PROGRESSING)

        # Verify no rendition was created
        self.assertEqual(job.renditions.count(), 0)

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_error_status_does_not_create_rendition(self):
        """Test that ERROR status does NOT create a rendition."""
        job = MediaTranscodingJob.objects.create(
            media=self.media,
            job_id="test-job-error-no-rendition",
            status=TranscodingJobStatus.PROGRESSING,
            backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
        )

        payload = {
            "version": "0",
            "id": "test-uuid",
            "detail": {
                "jobId": "test-job-error-no-rendition",
                "status": "ERROR",
            },
        }

        self.assertEqual(job.renditions.count(), 0)

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        self.assertEqual(response.status_code, 200)

        job.refresh_from_db()
        self.assertEqual(job.status, TranscodingJobStatus.FAILED)

        # Verify no rendition was created
        self.assertEqual(job.renditions.count(), 0)

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_missing_output_group_details_on_complete(self):
        """Test that COMPLETE status without outputGroupDetails causes an error."""
        job = MediaTranscodingJob.objects.create(
            media=self.media,
            job_id="test-job-complete-no-output-details",
            status=TranscodingJobStatus.PROGRESSING,
            backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
        )

        payload = {
            "version": "0",
            "id": "test-uuid",
            "detail": {
                "jobId": "test-job-complete-no-output-details",
                "status": "COMPLETE",
                # Missing outputGroupDetails
            },
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "COMPLETE status requires outputGroupDetails", response.json()["error"]
        )
        self.assertEqual(job.renditions.count(), 0)

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_missing_output_file_paths_in_metadata(self):
        """Test that missing outputFilePaths causes an error."""
        job = MediaTranscodingJob.objects.create(
            media=self.media,
            job_id="test-job-no-output-paths",
            status=TranscodingJobStatus.PROGRESSING,
            backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
        )

        payload = {
            "version": "0",
            "id": "test-uuid",
            "detail": {
                "jobId": "test-job-no-output-paths",
                "status": "COMPLETE",
                "outputGroupDetails": [
                    {
                        "outputDetails": [
                            {
                                # Missing outputFilePaths
                                "durationInMs": 5000,
                                "videoDetails": {
                                    "widthInPx": 1920,
                                    "heightInPx": 1080,
                                },
                            }
                        ]
                    }
                ],
            },
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        # Should still return 200 but log error and not create rendition
        self.assertEqual(response.status_code, 200)

        job.refresh_from_db()
        self.assertEqual(job.status, TranscodingJobStatus.COMPLETE)
        # No rendition should be created due to missing outputFilePaths
        self.assertEqual(job.renditions.count(), 0)

    @override_settings(WAGTAILMEDIA={"WEBHOOK_API_KEY": "valid-api-key"})
    def test_missing_video_details_in_metadata(self):
        """Test that missing videoDetails is handled gracefully, creating rendition with None values."""
        job = MediaTranscodingJob.objects.create(
            media=self.media,
            job_id="test-job-no-video-details",
            status=TranscodingJobStatus.PROGRESSING,
            backend="wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
        )

        payload = {
            "version": "0",
            "id": "test-uuid",
            "detail": {
                "jobId": "test-job-no-video-details",
                "status": "COMPLETE",
                "outputGroupDetails": [
                    {
                        "outputDetails": [
                            {
                                "outputFilePaths": ["s3://bucket/media/test.mp4"],
                                "durationInMs": 5000,
                                # Missing videoDetails
                            }
                        ]
                    }
                ],
            },
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY="valid-api-key",
        )

        # Should return 200 and create rendition with None for video details
        self.assertEqual(response.status_code, 200)

        job.refresh_from_db()
        self.assertEqual(job.status, TranscodingJobStatus.COMPLETE)

        # Rendition should be created but with None values for width/height/bitrate
        self.assertEqual(job.renditions.count(), 1)
        rendition = job.renditions.first()
        self.assertIsNone(rendition.width)
        self.assertIsNone(rendition.height)
        self.assertIsNone(rendition.bitrate)
        self.assertEqual(rendition.duration, 5.0)  # Duration from durationInMs
        self.assertEqual(rendition.file.name, "media/test.mp4")
