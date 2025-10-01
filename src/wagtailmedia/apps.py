import logging

from django.apps import AppConfig
from django.db.models import ForeignKey

from wagtailmedia.utils import get_media_transcoding_backend


logger = logging.getLogger(__name__)


class WagtailMediaAppConfig(AppConfig):
    default_auto_field = "django.db.models.AutoField"
    name = "wagtailmedia"
    label = "wagtailmedia"
    verbose_name = "Wagtail media"

    def ready(self):
        from wagtail.admin.compare import register_comparison_class

        from .edit_handlers import MediaFieldComparison
        from .models import get_media_model
        from .signal_handlers import register_signal_handlers

        register_signal_handlers()

        # Set up image ForeignKeys to use ImageFieldComparison as the comparison class
        # when comparing page revisions
        register_comparison_class(
            ForeignKey, to=get_media_model(), comparison_class=MediaFieldComparison
        )

        # Check for a transcoding backend in wagtailmedia settings
        backend_cls = get_media_transcoding_backend()

        # Debugging
        if backend_cls is None:
            logger.info("No transcoding backend specified.")
        else:
            logger.info(f"Using transcoding backend: {backend_cls.__name__}")
