from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from wagtailmedia.models import TranscodingJobStatus
from wagtailmedia.signal_handlers import transcode_video
from wagtailmedia.transcoding_backends.base import TranscodingError


class TranscodeVideoTests(TestCase):
    """Tests for transcode_video signal handler logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_media = Mock()
        self.mock_media.id = 1
        self.mock_media.title = "Test Video"
        self.mock_media.type = "video"
        self.mock_media.file = Mock()

        self.mock_backend_cls = Mock()
        self.mock_backend = Mock()
        self.mock_backend_cls.return_value = self.mock_backend
        self.mock_backend_cls.__module__ = "wagtailmedia.transcoding_backends.aws"
        self.mock_backend_cls.__name__ = "EMCTranscodingBackend"

    def test_skips_transcoding_for_audio_media(self):
        """Test that non-video media types are skipped."""
        self.mock_media.type = "audio"

        with patch(
            "wagtailmedia.signal_handlers.get_media_transcoding_backend",
            return_value=self.mock_backend_cls,
        ):
            transcode_video(self.mock_media)

            self.mock_backend_cls.assert_not_called()

    @override_settings(WAGTAILMEDIA={"TRANSCODING_BACKEND": None})
    def test_skips_transcoding_when_no_backend_configured(self):
        """Test that transcoding is skipped when no backend is configured."""
        transcode_video(self.mock_media)

        self.mock_backend_cls.assert_not_called()

    def test_skips_transcoding_when_active_job_exists(self):
        """Test that transcoding is skipped when an active job already exists."""
        active_statuses = [
            (TranscodingJobStatus.PENDING, "pending-job"),
            (TranscodingJobStatus.PROGRESSING, "progressing-job"),
        ]

        for status, job_id in active_statuses:
            with self.subTest(status=status):
                with patch(
                    "wagtailmedia.signal_handlers.get_media_transcoding_backend",
                    return_value=self.mock_backend_cls,
                ):
                    with patch(
                        "wagtailmedia.signal_handlers.MediaTranscodingJob.objects.filter"
                    ) as mock_filter:
                        mock_existing_job = Mock()
                        mock_existing_job.job_id = job_id
                        mock_existing_job.status = status
                        mock_filter.return_value.first.return_value = mock_existing_job

                        transcode_video(self.mock_media)

                        self.mock_backend.start_transcode.assert_not_called()

    def test_creates_job_and_starts_transcode_successfully(self):
        """Test successful transcode job creation and start."""
        test_response = {"Job": {"Id": "test-job"}}
        self.mock_backend.start_transcode.return_value = test_response

        with patch(
            "wagtailmedia.signal_handlers.get_media_transcoding_backend",
            return_value=self.mock_backend_cls,
        ):
            with patch(
                "wagtailmedia.signal_handlers.MediaTranscodingJob.objects.filter"
            ) as mock_filter:
                mock_filter.return_value.first.return_value = None

                with patch(
                    "wagtailmedia.signal_handlers.MediaTranscodingJob.objects.create"
                ) as mock_create:
                    mock_job = Mock()
                    mock_create.return_value = mock_job

                    transcode_video(self.mock_media)

                    # Verify job was created
                    mock_create.assert_called_once_with(
                        media=self.mock_media, status=TranscodingJobStatus.PENDING
                    )

                    # Verify backend was called
                    self.mock_backend.start_transcode.assert_called_once_with(
                        self.mock_media.file
                    )

                    # Verify job was updated
                    self.assertEqual(mock_job.job_id, "test-job")
                    self.assertEqual(
                        mock_job.backend,
                        "wagtailmedia.transcoding_backends.aws.EMCTranscodingBackend",
                    )
                    mock_job.save.assert_called_once()

    def test_marks_job_failed_on_transcoding_error(self):
        """Test that TranscodingError marks job as failed."""
        self.mock_backend.start_transcode.side_effect = TranscodingError(
            "S3 upload failed"
        )

        with patch(
            "wagtailmedia.signal_handlers.get_media_transcoding_backend",
            return_value=self.mock_backend_cls,
        ):
            with patch(
                "wagtailmedia.signal_handlers.MediaTranscodingJob.objects.filter"
            ) as mock_filter:
                mock_filter.return_value.first.return_value = None

                with patch(
                    "wagtailmedia.signal_handlers.MediaTranscodingJob.objects.create"
                ) as mock_create:
                    mock_job = Mock()
                    mock_create.return_value = mock_job

                    transcode_video(self.mock_media)

                    self.assertEqual(mock_job.status, TranscodingJobStatus.FAILED)
                    self.assertEqual(
                        mock_job.metadata["error_type"], "TranscodingError"
                    )
                    self.assertIn("S3 upload failed", mock_job.metadata["error"])
                    mock_job.save.assert_called_once()

    def test_deletes_job_and_raises_on_improperly_configured(self):
        """Test that ImproperlyConfigured deletes job and re-raises."""
        self.mock_backend.start_transcode.side_effect = ImproperlyConfigured(
            "AWS credentials missing"
        )

        with patch(
            "wagtailmedia.signal_handlers.get_media_transcoding_backend",
            return_value=self.mock_backend_cls,
        ):
            with patch(
                "wagtailmedia.signal_handlers.MediaTranscodingJob.objects.filter"
            ) as mock_filter:
                mock_filter.return_value.first.return_value = None

                with patch(
                    "wagtailmedia.signal_handlers.MediaTranscodingJob.objects.create"
                ) as mock_create:
                    mock_job = Mock()
                    mock_create.return_value = mock_job

                    with self.assertRaises(ImproperlyConfigured):
                        transcode_video(self.mock_media)

                    mock_job.delete.assert_called_once()

    def test_marks_job_failed_and_raises_on_unexpected_exception(self):
        """Test that unexpected exceptions mark job as failed and re-raise."""
        self.mock_backend.start_transcode.side_effect = ValueError("test error message")

        with patch(
            "wagtailmedia.signal_handlers.get_media_transcoding_backend",
            return_value=self.mock_backend_cls,
        ):
            with patch(
                "wagtailmedia.signal_handlers.MediaTranscodingJob.objects.filter"
            ) as mock_filter:
                mock_filter.return_value.first.return_value = None

                with patch(
                    "wagtailmedia.signal_handlers.MediaTranscodingJob.objects.create"
                ) as mock_create:
                    mock_job = Mock()
                    mock_create.return_value = mock_job

                    with self.assertRaises(ValueError):
                        transcode_video(self.mock_media)

                    self.assertEqual(mock_job.status, TranscodingJobStatus.FAILED)
                    self.assertEqual(mock_job.metadata["error_type"], "unknown")
                    self.assertIn("test error message", mock_job.metadata["error"])
                    mock_job.save.assert_called_once()
