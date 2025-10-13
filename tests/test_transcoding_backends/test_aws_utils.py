import sys

from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase

from wagtailmedia.transcoding_backends import aws_utils


class ImportBoto3Tests(TestCase):
    def setUp(self):
        """Set up mock boto3 modules for testing."""
        self.mock_boto3 = Mock()
        self.mock_botocore_exceptions = Mock()
        self.mock_botocore = Mock()
        self.mock_botocore.exceptions = self.mock_botocore_exceptions

    def test_caches_boto3_modules_on_repeated_calls(self):
        """Test that import_boto3 returns cached modules on repeated calls."""

        with patch.dict(
            sys.modules,
            {
                "boto3": self.mock_boto3,
                "botocore": self.mock_botocore,
                "botocore.exceptions": self.mock_botocore_exceptions,
            },
        ):
            boto3_1, botocore_exceptions_1 = aws_utils.import_boto3()
            boto3_2, botocore_exceptions_2 = aws_utils.import_boto3()

            self.assertIs(boto3_1, boto3_2)
            self.assertIs(botocore_exceptions_1, botocore_exceptions_2)

            # Verify module-level cache was populated
            self.assertIsNotNone(aws_utils._boto3)
            self.assertIsNotNone(aws_utils._botocore_exceptions)

    def test_raises_improperly_configured_when_boto3_missing(self):
        """Test that import_boto3 raises ImproperlyConfigured when boto3 is not available."""

        with patch.dict(sys.modules, {"boto3": None, "botocore.exceptions": None}):
            self.assertRaises(ImproperlyConfigured, aws_utils.import_boto3)
