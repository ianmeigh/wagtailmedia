import sys

from unittest.mock import Mock, patch

from django.core.checks import Error, Warning
from django.test import TestCase, override_settings

from wagtailmedia.transcoding_backends.aws.backend import (
    AWSMediaConvertBackend,  # noqa: F401
)
from wagtailmedia.transcoding_backends.aws.checks import (
    check_aws_transcoding_backend_configuration,
)
from wagtailmedia.transcoding_backends.base import AbstractTranscodingBackend


class NonAWSBackend(AbstractTranscodingBackend):
    """Non AWS transcoding backend for testing."""

    pass


class TestAWSTranscodingBackendChecks(TestCase):
    """Test AWS transcoding backend system checks."""

    @override_settings(WAGTAILMEDIA={})
    def test_no_backend_configured(self):
        """Test that checks are skipped when no transcoding backend is configured."""
        issues = check_aws_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(issues, [])

    @override_settings(
        WAGTAILMEDIA={
            "TRANSCODING_BACKEND": "test_transcoding_backends.test_aws_checks.NonAWSBackend"
        }
    )
    def test_non_aws_backend_configured(self):
        """Test that checks are skipped when a non-AWS backend is configured."""
        issues = check_aws_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(issues, [])

    @override_settings(
        WAGTAILMEDIA={
            "TRANSCODING_BACKEND": "test_transcoding_backends.test_aws_checks.AWSMediaConvertBackend"
        },
        AWS_MEDIACONVERT_STORAGE_BUCKET_NAME="test-bucket",
        AWS_MEDIACONVERT_ROLE_NAME="TestRole",
        AWS_MEDIACONVERT_QUEUE_NAME="TestQueue",
        AWS_WEBHOOK_API_KEY="test-api-key",
    )
    @patch.dict(sys.modules, {"boto3": Mock()})
    def test_valid_configuration(self):
        """Test that valid AWS configuration passes all checks."""
        issues = check_aws_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(issues, [])

    @override_settings(
        WAGTAILMEDIA={
            "TRANSCODING_BACKEND": "test_transcoding_backends.test_aws_checks.AWSMediaConvertBackend"
        },
        AWS_MEDIACONVERT_STORAGE_BUCKET_NAME=None,
        AWS_STORAGE_BUCKET_NAME=None,
    )
    @patch.dict(sys.modules, {"boto3": Mock()})
    def test_missing_required_bucket_setting(self):
        """Test that missing both bucket settings raises an error."""
        issues = check_aws_transcoding_backend_configuration(app_configs=None)
        errors = [e for e in issues if isinstance(e, Error)]

        self.assertEqual(len(errors), 1)
        self.assertIn("S3 bucket configuration required", errors[0].msg)
        self.assertEqual(errors[0].id, "wagtailmedia.E201")

    @override_settings(
        WAGTAILMEDIA={
            "TRANSCODING_BACKEND": "test_transcoding_backends.test_aws_checks.AWSMediaConvertBackend"
        },
        AWS_STORAGE_BUCKET_NAME="",
    )
    @patch.dict(sys.modules, {"boto3": Mock()})
    def test_empty_bucket_name_treated_as_missing(self):
        """Test that empty string for both bucket names is treated as missing."""
        with self.settings(AWS_MEDIACONVERT_STORAGE_BUCKET_NAME=""):
            issues = check_aws_transcoding_backend_configuration(app_configs=None)
            errors = [e for e in issues if isinstance(e, Error)]

            self.assertEqual(len(errors), 1)
            self.assertIn("S3 bucket configuration required", errors[0].msg)
            self.assertEqual(errors[0].id, "wagtailmedia.E201")

    @override_settings(
        WAGTAILMEDIA={
            "TRANSCODING_BACKEND": "test_transcoding_backends.test_aws_checks.AWSMediaConvertBackend"
        },
        AWS_STORAGE_BUCKET_NAME="storage-bucket",
        AWS_MEDIACONVERT_ROLE_NAME="TestRole",
        AWS_MEDIACONVERT_QUEUE_NAME="TestQueue",
        AWS_WEBHOOK_API_KEY="test-api-key",
    )
    @patch.dict(sys.modules, {"boto3": Mock()})
    def test_defaults_to_storage_bucket_when_mediaconvert_bucket_not_set(self):
        """Test that AWS_STORAGE_BUCKET_NAME is used when AWS_MEDIACONVERT_STORAGE_BUCKET_NAME is not set."""
        issues = check_aws_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(issues, [])

    @override_settings(
        WAGTAILMEDIA={
            "TRANSCODING_BACKEND": "test_transcoding_backends.test_aws_checks.AWSMediaConvertBackend"
        },
        AWS_MEDIACONVERT_STORAGE_BUCKET_NAME="test-bucket",
    )
    @patch.dict(sys.modules, {"boto3": Mock()})
    def test_missing_optional_settings_generates_warnings(self):
        """Test that missing optional settings generate warnings with default values."""

        issues = check_aws_transcoding_backend_configuration(app_configs=None)
        warnings = [w for w in issues if isinstance(w, Warning)]

        expected_warnings = [
            "AWS_MEDIACONVERT_ROLE_NAME not set, using default: 'MediaConvert_Default_Role'",
            "AWS_MEDIACONVERT_QUEUE_NAME not set, using default: 'Default'",
            "AWS_WEBHOOK_API_KEY not set - webhook status updates will fail",
        ]

        self.assertEqual(len(warnings), len(expected_warnings))

        for i, expected_msg in enumerate(expected_warnings):
            with self.subTest(i, warning=expected_msg):
                self.assertEqual(expected_msg, warnings[i].msg)
