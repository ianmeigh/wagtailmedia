from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from wagtailmedia.transcoding_backends.aws import AWSTranscodingConfig


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
