import sys

from unittest.mock import Mock, patch

from django.core.checks import Error, Warning
from django.test import TestCase, override_settings

from wagtailmedia.transcoding_backends.aws_checks import (
    check_aws_transcoding_backend_configuration,
)
from wagtailmedia.transcoding_backends.base import AbstractTranscodingBackend


class EMCTranscodingBackend(AbstractTranscodingBackend):
    """Mock AWS transcoding backend for testing."""

    def start_transcode(self, media_file):
        pass

    def stop_transcode(self, task_id):
        pass


class TestAWSTranscodingBackendChecks(TestCase):
    """Test AWS transcoding backend system checks."""

    @override_settings(WAGTAILMEDIA={})
    def test_no_backend_configured(self):
        """Test that checks are skipped when no transcoding backend is configured."""
        issues = check_aws_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(issues, [])

    @override_settings(
        WAGTAILMEDIA={"TRANSCODING_BACKEND": "some.other.backend.NotAWSBackend"}
    )
    def test_non_aws_backend_configured(self):
        """Test that checks are skipped when a non-AWS backend is configured."""
        issues = check_aws_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(issues, [])

    @override_settings(
        WAGTAILMEDIA={
            "TRANSCODING_BACKEND": "test_transcoding_backends.test_aws_checks.EMCTranscodingBackend"
        },
        AWS_STORAGE_BUCKET_NAME="test-bucket",
        AWS_MEDIACONVERT_ROLE_NAME="TestRole",
        AWS_MEDIACONVERT_QUEUE_NAME="TestQueue",
    )
    @patch.dict(sys.modules, {"boto3": Mock()})
    def test_valid_configuration(self):
        """Test that valid AWS configuration passes all checks."""
        issues = check_aws_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(issues, [])

    @override_settings(
        WAGTAILMEDIA={
            "TRANSCODING_BACKEND": "test_transcoding_backends.test_aws_checks.EMCTranscodingBackend"
        },
        AWS_STORAGE_BUCKET_NAME="test-bucket",
    )
    def test_boto3_not_installed(self):
        """Test that missing boto3 raises an error."""
        issues = check_aws_transcoding_backend_configuration(app_configs=None)
        errors = [e for e in issues if isinstance(e, Error)]

        self.assertEqual(len(errors), 1)
        self.assertEqual(
            "boto3 is required for AWS transcoding backend but is not installed",
            errors[0].msg,
        )
        self.assertEqual(errors[0].id, "wagtailmedia.E200")

    @override_settings(
        WAGTAILMEDIA={
            "TRANSCODING_BACKEND": "test_transcoding_backends.test_aws_checks.EMCTranscodingBackend"
        },
        AWS_STORAGE_BUCKET_NAME=None,
    )
    @patch.dict(sys.modules, {"boto3": Mock()})
    def test_missing_required_bucket_setting(self):
        """Test that missing AWS_STORAGE_BUCKET_NAME raises an error."""
        issues = check_aws_transcoding_backend_configuration(app_configs=None)
        errors = [e for e in issues if isinstance(e, Error)]

        self.assertEqual(len(errors), 1)
        self.assertEqual(
            errors[0].msg,
            "AWS_STORAGE_BUCKET_NAME is required for AWS transcoding backend",
        )
        self.assertEqual(errors[0].id, "wagtailmedia.E201")

    @override_settings(
        WAGTAILMEDIA={
            "TRANSCODING_BACKEND": "test_transcoding_backends.test_aws_checks.EMCTranscodingBackend"
        }
    )
    @patch.dict(sys.modules, {"boto3": Mock()})
    def test_empty_bucket_name_treated_as_missing(self):
        """Test that empty string for bucket name is treated as missing."""
        with self.settings(AWS_STORAGE_BUCKET_NAME=""):
            issues = check_aws_transcoding_backend_configuration(app_configs=None)
            errors = [e for e in issues if isinstance(e, Error)]

            self.assertEqual(len(errors), 1)
            self.assertEqual(
                errors[0].msg,
                "AWS_STORAGE_BUCKET_NAME is required for AWS transcoding backend",
            )
            self.assertEqual(errors[0].id, "wagtailmedia.E201")

    @override_settings(
        WAGTAILMEDIA={
            "TRANSCODING_BACKEND": "test_transcoding_backends.test_aws_checks.EMCTranscodingBackend"
        },
        AWS_STORAGE_BUCKET_NAME="test-bucket",
    )
    @patch.dict(sys.modules, {"boto3": Mock()})
    def test_missing_optional_settings_generates_warnings(self):
        """Test that missing optional settings generate warnings with default values."""

        issues = check_aws_transcoding_backend_configuration(app_configs=None)
        warnings = [w for w in issues if isinstance(w, Warning)]

        expected_warnings = [
            "AWS_MEDIACONVERT_ROLE_NAME not set, using default: 'MediaConvert_Default_Role'",
            "AWS_MEDIACONVERT_QUEUE_NAME not set, using default: 'Default'",
        ]

        self.assertEqual(len(warnings), len(expected_warnings))

        for i, expected_msg in enumerate(expected_warnings):
            with self.subTest(i, warning=expected_msg):
                self.assertEqual(expected_msg, warnings[i].msg)
                self.assertEqual(warnings[i].id, "wagtailmedia.E203")
