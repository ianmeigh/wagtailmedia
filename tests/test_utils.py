from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from wagtailmedia.models import get_media_model
from wagtailmedia.utils import (
    format_audio_html,
    format_video_html,
    get_media_transcoding_backend,
    import_transcoding_backend_class,
)


Media = get_media_model()


class MediaUtilsTest(TestCase):
    def test_format_audio_html(self):
        audio = Media(
            title="Test audio 2",
            duration=1000,
            file=ContentFile("Test1", name="test1.mp3"),
            type="audio",
        )

        self.assertEqual(
            format_audio_html(audio),
            f'<audio controls>\n<source src="{audio.url}" type="audio/mpeg">\n'
            f"<p>Your browser does not support the audio element.</p>\n</audio>",
        )

    def test_format_video_html(self):
        video = Media(
            title="Test video 1",
            duration=1024,
            file=ContentFile("Test1", name="test1.mp4"),
            type="video",
        )

        self.assertEqual(
            format_video_html(video),
            f'<video controls>\n<source src="{video.url}" type="video/mp4">\n'
            f"<p>Your browser does not support the video element.</p>\n</video>",
        )


class TranscodingBackendImportTest(TestCase):
    class DummyBackend:
        pass

    def test_import_transcoding_backend_success(self):
        with patch("wagtailmedia.utils.importlib.import_module") as mock_import_module:

            class DummyModule:
                DummyBackend = self.DummyBackend

            mock_import_module.return_value = DummyModule
            backend_path = "dummy.module.DummyBackend"
            backend_class = import_transcoding_backend_class(backend_path)
            self.assertIs(backend_class, self.DummyBackend)

    @override_settings(
        WAGTAILMEDIA={"TRANSCODING_BACKEND": "dummy.module.DummyBackend"}
    )
    def test_get_media_transcoding_backend_success(self):
        with patch("wagtailmedia.utils.importlib.import_module") as mock_import_module:

            class DummyModule:
                DummyBackend = self.DummyBackend

            mock_import_module.return_value = DummyModule
            backend_class = get_media_transcoding_backend()
            self.assertIs(backend_class, self.DummyBackend)

    @override_settings(WAGTAILMEDIA={"TRANSCODING_BACKEND": "not.a.real.Backend"})
    def test_import_transcoding_backend_failure(self):
        with patch(
            "wagtailmedia.utils.importlib.import_module",
            side_effect=ModuleNotFoundError(),
        ):
            with self.assertRaises(RuntimeError) as excinfo:
                get_media_transcoding_backend()
            self.assertIn(
                "Failed to import transcoding backend", str(excinfo.exception)
            )

    @override_settings(WAGTAILMEDIA={})
    def test_import_transcoding_backend_missing_setting(self):
        backend_class = get_media_transcoding_backend()
        self.assertIsNone(backend_class)
