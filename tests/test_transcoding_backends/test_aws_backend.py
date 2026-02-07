import json
import tempfile

from pathlib import Path
from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from wagtailmedia.models import (
    Media,
    MediaRendition,
    MediaTranscodingJob,
    TranscodingJobStatus,
)
from wagtailmedia.transcoding_backends.aws.backend import (
    IAMGetRoleError,
    MediaConvertJobError,
    MediaConvertJobSettings,
    MediaConvertService,
    OutputDetail,
    S3Downloader,
    S3Service,
    download_and_create_rendition,
)


class S3ServiceFileAvailabilityTests(TestCase):
    """
    Tests for S3Service URL detection logic.

    These tests focus on the business logic for determining whether a file
    needs to be uploaded to S3 or is already web-accessible. They test actual
    behavior without mocking AWS services.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.s3_service = S3Service()

    def test_detects_web_url_as_web_accessible(self):
        """Test that HTTPS URLs are recognized as web-accessible."""
        mock_file = Mock()

        for url in [
            "https://example.com/media/video.mp4",
            "http://example.com/media/video.mp4",
            "s3://my-bucket/path/to/video.mp4",
        ]:
            mock_file.url = url

            with self.subTest(msg="File upload should not be attempted", url=url):
                with patch.object(self.s3_service, "upload_file") as mock_upload:
                    result = self.s3_service.ensure_file_is_available(
                        mock_file, "test-bucket"
                    )

                    self.assertEqual(result, url)
                    mock_upload.assert_not_called()

    def test_detects_local_paths_as_needing_upload(self):
        """Test that local filesystem paths are detected as needing upload."""
        mock_file = Mock()
        mock_file.url = "/media/video.mp4"
        mock_file.name = "video.mp4"

        with patch.object(self.s3_service, "upload_file") as mock_upload:
            result = self.s3_service.ensure_file_is_available(mock_file, "test-bucket")

            self.assertEqual(result, "s3://test-bucket/video.mp4")
            mock_upload.assert_called_once_with(mock_file, "test-bucket", "video.mp4")

    def test_uploads_file_without_url_attribute(self):
        """Test that files without url attribute are treated as needing upload."""
        mock_file = Mock(spec=["name"])  # No url attribute
        mock_file.name = "video.mp4"

        with patch.object(self.s3_service, "upload_file") as mock_upload:
            result = self.s3_service.ensure_file_is_available(mock_file, "test-bucket")

            self.assertEqual(result, "s3://test-bucket/video.mp4")
            mock_upload.assert_called_once()


class MediaConvertServiceTests(TestCase):
    """Tests for MediaConvertService logic."""

    def setUp(self):
        """Set up test fixtures."""
        from botocore.exceptions import ClientError

        self.service = MediaConvertService(
            role="MediaConvert_Default_Role",
            queue="test-queue",
        )
        # Simple error for exception testing
        self.client_error = ClientError({"Error": {}}, "TestOperation")

    @patch("wagtailmedia.transcoding_backends.aws.backend.get_iam_client")
    def test_get_role_arn_raises_improperly_configured_on_iam_error(
        self, mock_get_client
    ):
        """Test that IAM errors are converted to IAMGetRoleError."""
        mock_iam = Mock()
        mock_iam.get_role.side_effect = self.client_error
        mock_get_client.return_value = mock_iam

        with self.assertRaises(IAMGetRoleError) as context:
            self.service.get_role_arn()
        self.assertIn("Failed to get IAM role", str(context.exception))

    @patch("wagtailmedia.transcoding_backends.aws.backend.get_mediaconvert_client")
    @patch.object(MediaConvertService, "get_role_arn")
    def test_create_transcode_job_passes_parameters_correctly(
        self, mock_get_role, mock_get_client
    ):
        """Test that job parameters are assembled and passed to MediaConvert."""
        test_role_arn = "arn:aws:iam::123456789:role/MediaConvert_Default_Role"
        test_settings = {"OutputGroups": [], "Inputs": []}
        test_queue = "test-queue"

        mock_mediaconvert = Mock()
        mock_mediaconvert.create_job.return_value = {"Job": {"Id": "job-12345"}}
        mock_get_client.return_value = mock_mediaconvert
        mock_get_role.return_value = test_role_arn

        result = self.service.create_transcode_job(
            "s3://bucket/source.mp4", "s3://bucket/output/", test_settings
        )

        mock_mediaconvert.create_job.assert_called_once_with(
            Role=test_role_arn, Settings=test_settings, Queue=test_queue
        )
        self.assertEqual(result, {"Job": {"Id": "job-12345"}})

    @patch("wagtailmedia.transcoding_backends.aws.backend.get_mediaconvert_client")
    @patch.object(MediaConvertService, "get_role_arn")
    def test_create_transcode_job_raises_error_on_mediaconvert_failure(
        self, mock_get_role, mock_get_client
    ):
        """Test that MediaConvert ClientError is converted to MediaConvertJobError."""
        mock_mediaconvert = Mock()
        mock_mediaconvert.create_job.side_effect = self.client_error
        mock_get_client.return_value = mock_mediaconvert
        mock_get_role.return_value = "arn:aws:iam::123:role/Test"

        with self.assertRaises(MediaConvertJobError) as context:
            self.service.create_transcode_job(
                "s3://bucket/source.mp4",
                "s3://bucket/output/",
                {"test": "settings"},
            )

        self.assertIn("Failed to create MediaConvert job", str(context.exception))


class FinalizationTaskTests(TestCase):
    """Test the download_and_create_rendition finalization task."""

    def setUp(self):
        """Create test media and transcoding job."""
        self.media = Media.objects.create(
            title="Test Video",
            file=ContentFile(b"test", name="test.mp4"),
            type="video",
            duration=100,
        )
        self.job = MediaTranscodingJob.objects.create(
            media=self.media,
            job_id="test-job-123",
            status=TranscodingJobStatus.FINALISING,
            backend="wagtailmedia.transcoding_backends.aws.backend.AWSMediaConvertBackend",
        )

    @patch("wagtailmedia.transcoding_backends.aws.backend.S3Downloader")
    def test_skip_download_creates_rendition_and_completes(self, mock_downloader_class):
        """Golden path: skip download, create rendition, mark COMPLETE."""
        mock_downloader = Mock()
        mock_downloader.should_skip_download.return_value = True
        mock_downloader_class.return_value = mock_downloader

        download_and_create_rendition.func(
            transcoding_job_id=self.job.pk,
            s3_output_url="s3://bucket/test.webm",
            duration_ms=10500,
            width=1280,
            height=720,
            bitrate=2500000,
        )

        # Rendition created with correct metadata
        self.assertEqual(MediaRendition.objects.count(), 1)
        rendition = MediaRendition.objects.first()
        self.assertEqual(rendition.media, self.media)
        self.assertEqual(rendition.file.name, "test.webm")  # S3 key (no bucket prefix)
        self.assertEqual(rendition.width, 1280)
        self.assertEqual(rendition.height, 720)
        self.assertEqual(rendition.duration, 10.5)  # Converted from ms
        self.assertEqual(rendition.bitrate, 2500000)

        # Job marked COMPLETE
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, TranscodingJobStatus.COMPLETE)

    @patch("wagtailmedia.transcoding_backends.aws.backend.S3Downloader")
    def test_downloads_file_when_needed(self, mock_downloader_class):
        """Test file download when storage requires it."""
        mock_downloader = Mock()
        mock_downloader.should_skip_download.return_value = False
        mock_downloader.download_from_s3_to_storage.return_value = "media/test.webm"
        mock_downloader_class.return_value = mock_downloader

        download_and_create_rendition.func(
            transcoding_job_id=self.job.pk,
            s3_output_url="s3://bucket/test.webm",
            duration_ms=10000,
            width=1280,
            height=720,
            bitrate=2500000,
        )

        # Download was called
        mock_downloader.download_from_s3_to_storage.assert_called_once_with(
            s3_url="s3://bucket/test.webm", destination_name="test.webm"
        )

        # Rendition created with downloaded file path
        self.assertEqual(MediaRendition.objects.count(), 1)
        rendition = MediaRendition.objects.first()
        self.assertEqual(rendition.file.name, "media/test.webm")


class S3DownloaderSkipLogicTests(TestCase):
    """Test S3Downloader.should_skip_download() decision logic."""

    @patch(
        "wagtailmedia.transcoding_backends.aws.backend.AWS_MEDIACONVERT_STORAGE_BUCKET_NAME",
        "test-bucket",
    )
    @patch("wagtailmedia.transcoding_backends.aws.backend.default_storage")
    def test_skip_download_when_same_s3_bucket(self, mock_storage):
        """Golden path: Same S3 bucket → skip download."""
        # Mock S3 storage with same bucket
        mock_storage.bucket_name = "test-bucket"

        downloader = S3Downloader()
        result = downloader.should_skip_download()

        self.assertTrue(result)

    @patch(
        "wagtailmedia.transcoding_backends.aws.backend.AWS_MEDIACONVERT_STORAGE_BUCKET_NAME",
        "test-bucket",
    )
    @patch("wagtailmedia.transcoding_backends.aws.backend.default_storage")
    def test_download_required_when_different_s3_bucket(self, mock_storage):
        """Critical: Different S3 bucket → download required."""
        # Mock S3 storage with different bucket
        mock_storage.bucket_name = "different-bucket"

        downloader = S3Downloader()
        result = downloader.should_skip_download()

        self.assertFalse(result)

    @patch("wagtailmedia.transcoding_backends.aws.backend.default_storage", spec=[])
    def test_download_required_when_non_s3_storage(self, mock_storage):
        """Critical: Non-S3 storage → download required."""
        # Mock non-S3 storage (spec=[] means no attributes)
        downloader = S3Downloader()
        result = downloader.should_skip_download()

        self.assertFalse(result)


class OutputDetailParsingTests(TestCase):
    """Test OutputDetail parsing from AWS webhook payloads."""

    def test_parse_valid_complete_webhook(self):
        """Golden path: Valid COMPLETE webhook → parsed OutputDetail."""
        detail = {
            "jobId": "test-job-123",
            "status": "COMPLETE",
            "outputGroupDetails": [
                {
                    "outputDetails": [
                        {
                            "outputFilePaths": ["s3://bucket/output/video.webm"],
                            "durationInMs": 45000,
                            "videoDetails": {
                                "widthInPx": 1920,
                                "heightInPx": 1080,
                                "averageBitrate": 5000000,
                            },
                        }
                    ]
                }
            ],
        }

        output = OutputDetail.from_detail_dict(detail)

        self.assertEqual(output.output_file_path, "s3://bucket/output/video.webm")
        self.assertEqual(output.duration_ms, 45000)
        self.assertEqual(output.width_px, 1920)
        self.assertEqual(output.height_px, 1080)
        self.assertEqual(output.average_bitrate, 5000000)


class ProfileJSONLoadingTests(TestCase):
    """Test MediaConvertJobSettings profile JSON loading."""

    def test_load_valid_profile_and_inject_paths(self):
        """Golden path: Load valid JSON profile and inject S3 paths."""
        profile_data = {
            "Settings": {
                "OutputGroups": [
                    {
                        "Name": "WebM Output",
                        "OutputGroupSettings": {
                            "Type": "FILE_GROUP_SETTINGS",
                            "FileGroupSettings": {
                                "Destination": "PLACEHOLDER_DESTINATION"
                            },
                        },
                        "Outputs": [{"VideoDescription": {}, "AudioDescriptions": []}],
                    }
                ],
                "Inputs": [{"FileInput": "PLACEHOLDER_SOURCE"}],
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(profile_data, f)
            profile_path = f.name

        try:
            with override_settings(WAGTAILMEDIA={"TRANSCODING_PROFILE": profile_path}):
                settings_obj = MediaConvertJobSettings()
                result = settings_obj.get_settings(
                    source_url="s3://bucket/source.mp4",
                    destination_bucket="s3://bucket/output/",
                )

                # Verify placeholders were replaced
                self.assertEqual(
                    result["Inputs"][0]["FileInput"], "s3://bucket/source.mp4"
                )
                self.assertEqual(
                    result["OutputGroups"][0]["OutputGroupSettings"][
                        "FileGroupSettings"
                    ]["Destination"],
                    "s3://bucket/output/",
                )
        finally:
            Path(profile_path).unlink()

    @override_settings(WAGTAILMEDIA={})
    def test_missing_profile_setting_raises_error(self):
        """Critical: Missing TRANSCODING_PROFILE setting raises ImproperlyConfigured."""
        settings_obj = MediaConvertJobSettings()

        with self.assertRaises(ImproperlyConfigured) as context:
            settings_obj.get_settings(
                source_url="s3://bucket/source.mp4",
                destination_bucket="s3://bucket/output/",
            )

        self.assertIn("TRANSCODING_PROFILE not configured", str(context.exception))

    @override_settings(
        WAGTAILMEDIA={"TRANSCODING_PROFILE": "/nonexistent/profile.json"}
    )
    def test_nonexistent_profile_file_raises_error(self):
        """Critical: Non-existent profile file raises FileNotFoundError."""
        settings_obj = MediaConvertJobSettings()

        with self.assertRaises(FileNotFoundError):
            settings_obj.get_settings(
                source_url="s3://bucket/source.mp4",
                destination_bucket="s3://bucket/output/",
            )
