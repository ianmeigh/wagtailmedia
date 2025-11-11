from django.core.checks import Error
from django.test import TestCase, override_settings

from wagtailmedia.checks import check_transcoding_backend_configuration
from wagtailmedia.transcoding_backends.base import AbstractTranscodingBackend


class MockTranscodingBackend(AbstractTranscodingBackend):
    """Mock transcoding backend for testing."""

    def start_transcode(self, media_file):
        pass

    def stop_transcode(self, task_id):
        pass


class DummyClass:
    pass


class TestGenericTranscodingBackendChecks(TestCase):
    """Test generic transcoding backend configuration checks."""

    @override_settings(WAGTAILMEDIA={})
    def test_no_backend_configured(self):
        """Test that checks pass when no transcoding backend is configured."""
        issues = check_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(issues, [])

    @override_settings(
        WAGTAILMEDIA={"TRANSCODING_BACKEND": "test_checks.MockTranscodingBackend"}
    )
    def test_valid_backend_path(self):
        """Test that a valid backend path passes checks."""
        issues = check_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(issues, [])

    @override_settings(WAGTAILMEDIA={"TRANSCODING_BACKEND": "invalid_path"})
    def test_invalid_backend_path_format(self):
        """Test that an invalid backend path raises an error."""
        issues = check_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(len(issues), 1)
        self.assertIsInstance(issues[0], Error)
        self.assertIn("Invalid TRANSCODING_BACKEND path", issues[0].msg)
        self.assertEqual(issues[0].id, "wagtailmedia.E100")

    @override_settings(
        WAGTAILMEDIA={"TRANSCODING_BACKEND": "nonexistent.module.Backend"}
    )
    def test_backend_module_not_found(self):
        """Test that a non-existent module raises an error."""
        issues = check_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(len(issues), 1)
        self.assertIsInstance(issues[0], Error)
        self.assertIn("Cannot import transcoding backend module", issues[0].msg)
        self.assertEqual(issues[0].id, "wagtailmedia.E100")

    @override_settings(
        WAGTAILMEDIA={"TRANSCODING_BACKEND": "test_checks.NonExistentClass"}
    )
    def test_backend_class_not_found(self):
        """Test that a non-existent class in valid module raises an error."""
        issues = check_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(len(issues), 1)
        self.assertIsInstance(issues[0], Error)
        self.assertIn("Cannot retrieve class", issues[0].msg)
        self.assertEqual(issues[0].id, "wagtailmedia.E100")

    @override_settings(WAGTAILMEDIA={"TRANSCODING_BACKEND": 12345})
    def test_backend_path_not_a_string(self):
        """Test that a non-string backend path raises an error."""
        issues = check_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(len(issues), 1)
        self.assertIsInstance(issues[0], Error)
        self.assertEqual(issues[0].msg, "Cannot import backend module")
        self.assertEqual(issues[0].id, "wagtailmedia.E100")

    @override_settings(WAGTAILMEDIA={"TRANSCODING_BACKEND": "test_checks.DummyClass"})
    def test_backend_not_subclass_of_abstract_backend(self):
        """Test that a class that doesn't inherit from AbstractTranscodingBackend raises an error."""
        issues = check_transcoding_backend_configuration(app_configs=None)

        self.assertEqual(len(issues), 1)
        self.assertIsInstance(issues[0], Error)
        self.assertIn("must inherit from AbstractTranscodingBackend", issues[0].msg)
        self.assertEqual(issues[0].id, "wagtailmedia.E101")
