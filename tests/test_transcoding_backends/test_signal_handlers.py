from unittest.mock import Mock, patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from wagtailmedia.models import (
    Media,
    MediaTranscodingJob,
    MediaType,
    TranscodingJobStatus,
)
from wagtailmedia.signal_handlers import transcode_video
from wagtailmedia.transcoding_backends.aws import MediaConvertJobError
from wagtailmedia.transcoding_backends.base import TranscodingConfigurationError


class TranscodeVideoTests(TestCase):
    """Tests for transcode_video signal handler logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.media = Media(
            title="Test media file",
            file=ContentFile("Dog chasing tail", name="mov.mp4"),
            type=MediaType.VIDEO,
        )
        self.media.save()

        self.mock_backend_cls = Mock()
        self.mock_backend = Mock()
        self.mock_backend_cls.return_value = self.mock_backend
        self.mock_backend_cls.__module__ = "wagtailmedia.transcoding_backends.aws"
        self.mock_backend_cls.__name__ = "EMCTranscodingBackend"

        # Set default return value for start_transcode
        self.mock_backend.start_transcode.return_value = {
            "Job": {"Id": "default-job-id"}
        }

    def test_skips_transcoding_for_audio_media(self):
        """Test that non-video media types are skipped."""
        self.media.type = MediaType.AUDIO

        with patch(
            "wagtailmedia.signal_handlers.get_media_transcoding_backend",
            return_value=self.mock_backend_cls,
        ):
            transcode_video(self.media)

            self.mock_backend_cls.assert_not_called()

    @override_settings(WAGTAILMEDIA={"TRANSCODING_BACKEND": None})
    def test_skips_transcoding_when_no_backend_configured(self):
        """Test that transcoding is skipped when no backend is configured."""
        transcode_video(self.media)

        self.mock_backend_cls.assert_not_called()

    def test_skips_transcoding_when_active_job_exists(self):
        """Test that transcoding is skipped when an active job already exists."""

        with patch(
            "wagtailmedia.signal_handlers.get_media_transcoding_backend",
            return_value=self.mock_backend_cls,
        ):
            transcode_video(self.media)

            self.mock_backend.start_transcode.assert_called_once()
            self.assertEqual(MediaTranscodingJob.objects.count(), 1)
            self.assertEqual(
                MediaTranscodingJob.objects.first().status, TranscodingJobStatus.PENDING
            )

            with self.assertLogs("wagtailmedia.signal_handlers", level="INFO") as logs:
                transcode_video(self.media)

                self.assertEqual(MediaTranscodingJob.objects.count(), 1)
                self.mock_backend.start_transcode.assert_called_once()
                self.assertEqual(len(logs.output), 1)
                self.assertIn(
                    "Skipping transcode for media 1 (Test media file): Job default-job-id already pending",
                    logs.output[0],
                )

    def test_creates_job_and_starts_transcode_successfully(self):
        """Test successful transcode job creation and start."""
        test_response = {"Job": {"Id": "test-job"}}
        self.mock_backend.start_transcode.return_value = test_response

        with patch(
            "wagtailmedia.signal_handlers.get_media_transcoding_backend",
            return_value=self.mock_backend_cls,
        ):
            transcode_video(self.media)

            # Verify backend was called with correct file
            self.mock_backend.start_transcode.assert_called_once_with(self.media.file)

            # Verify job was created and updated correctly using real database
            self.assertEqual(MediaTranscodingJob.objects.count(), 1)
            job = MediaTranscodingJob.objects.first()
            self.assertEqual(job.media, self.media)
            self.assertEqual(job.job_id, "test-job")
            self.assertEqual(job.status, TranscodingJobStatus.PENDING)
            self.assertEqual(
                job.backend,
                "wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
            )

    def test_marks_job_failed_on_transcoding_error(self):
        """Test that TranscodingError marks job as failed."""
        self.mock_backend.start_transcode.side_effect = MediaConvertJobError(
            "Failed to create MediaConvert job"
        )

        with patch(
            "wagtailmedia.signal_handlers.get_media_transcoding_backend",
            return_value=self.mock_backend_cls,
        ):
            transcode_video(self.media)

            # Verify job was created and marked as failed using real database
            self.assertEqual(MediaTranscodingJob.objects.count(), 1)
            job = MediaTranscodingJob.objects.first()
            self.assertEqual(job.media, self.media)
            self.assertEqual(job.status, TranscodingJobStatus.FAILED)
            self.assertEqual(job.metadata["error_type"], "MediaConvertJobError")
            self.assertIn("Failed to create MediaConvert job", job.metadata["error"])

    def test_no_job_persists_when_improperly_configured(self):
        """Test that no job persists when TranscodingConfigurationError is raised."""
        self.mock_backend.start_transcode.side_effect = TranscodingConfigurationError()

        with patch(
            "wagtailmedia.signal_handlers.get_media_transcoding_backend",
            return_value=self.mock_backend_cls,
        ):
            with self.assertRaises(TranscodingConfigurationError):
                transcode_video(self.media)

            # Verify no job persists in database
            self.assertEqual(MediaTranscodingJob.objects.count(), 0)

    def test_marks_job_failed_and_raises_on_unexpected_exception(self):
        """Test that unexpected exceptions mark job as failed and re-raise."""
        self.mock_backend.start_transcode.side_effect = ValueError("test error message")

        with patch(
            "wagtailmedia.signal_handlers.get_media_transcoding_backend",
            return_value=self.mock_backend_cls,
        ):
            with self.assertRaises(ValueError):
                transcode_video(self.media)

            # Verify job was created and marked as failed using real database
            self.assertEqual(MediaTranscodingJob.objects.count(), 1)
            job = MediaTranscodingJob.objects.first()
            self.assertEqual(job.media, self.media)
            self.assertEqual(job.status, TranscodingJobStatus.FAILED)
            self.assertEqual(job.metadata["error_type"], "unknown")
            self.assertIn("test error message", job.metadata["error"])
