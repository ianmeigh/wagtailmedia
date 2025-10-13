from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from wagtailmedia.transcoding_backends.aws import AWSTranscodingConfig, S3Service


class AWSTranscodingConfigTests(TestCase):
    """Tests for AWS configuration management."""

    @override_settings(
        AWS_STORAGE_BUCKET_NAME="test-bucket", AWS_MEDIACONVERT_ROLE_NAME="TestRole"
    )
    def test_valid_configuration_with_all_settings(self):
        """Test configuration loads successfully with all required settings."""
        config = AWSTranscodingConfig()

        self.assertEqual(config.destination_bucket, "test-bucket")
        self.assertEqual(config.mediaconvert_role, "TestRole")

    @override_settings(AWS_STORAGE_BUCKET_NAME=None)
    def test_missing_bucket_name_setting_raises_error(self):
        """Test that missing AWS_STORAGE_BUCKET_NAME raises ImproperlyConfigured."""
        with self.assertRaises(ImproperlyConfigured) as err:
            AWSTranscodingConfig()

        self.assertIn("AWS_STORAGE_BUCKET_NAME", str(err.exception))
        self.assertIn("required for AWS transcoding", str(err.exception))

    @override_settings(AWS_STORAGE_BUCKET_NAME="test-bucket")
    def test_default_mediaconvert_role_used_when_not_specified(self):
        """Test that AWS_MEDIACONVERT_ROLE_NAME defaults to 'MediaConvert_Default_Role'."""
        # Remove the setting if it exists
        from django.conf import settings

        if hasattr(settings, "AWS_MEDIACONVERT_ROLE_NAME"):
            delattr(settings, "AWS_MEDIACONVERT_ROLE_NAME")

        config = AWSTranscodingConfig()

        self.assertEqual(config.mediaconvert_role, "MediaConvert_Default_Role")


class S3ServiceFileAvailabilityTests(TestCase):
    """
    Tests for S3Service URL detection logic.

    These tests focus on the business logic for determining whether a file
    needs to be uploaded to S3 or is already web-accessible. They test actual
    behavior without mocking AWS services.
    """

    def setUp(self):
        """Set up test fixtures."""
        self.config = Mock(spec=AWSTranscodingConfig)
        self.config.destination_bucket = "test-bucket"
        self.s3_service = S3Service(self.config)

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
