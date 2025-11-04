import logging

from django.apps import AppConfig
from django.db.models import ForeignKey


logger = logging.getLogger(__name__)


class WagtailMediaAppConfig(AppConfig):
    default_auto_field = "django.db.models.AutoField"
    name = "wagtailmedia"
    label = "wagtailmedia"
    verbose_name = "Wagtail media"

    def ready(self):
        from django.conf import settings
        from wagtail.admin.compare import register_comparison_class

        from .checks import check_transcoding_backend_configuration  # noqa: F401
        from .edit_handlers import MediaFieldComparison
        from .models import get_media_model
        from .signal_handlers import register_signal_handlers

        register_signal_handlers()

        # Register checks only if AWS backend is configured
        wagtailmedia_settings = getattr(settings, "WAGTAILMEDIA", {})
        backend_path = wagtailmedia_settings.get("TRANSCODING_BACKEND", "")
        if (
            isinstance(backend_path, str)
            and backend_path.split(".")[-1] == "EMCTranscodingBackend"
        ):
            from wagtailmedia.transcoding_backends.aws_checks import (
                check_aws_transcoding_backend_configuration,  # noqa: F401
            )

        # Set up image ForeignKeys to use ImageFieldComparison as the comparison class
        # when comparing page revisions
        register_comparison_class(
            ForeignKey, to=get_media_model(), comparison_class=MediaFieldComparison
        )
