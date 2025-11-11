import logging

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from wagtailmedia.settings import wagtailmedia_settings
from wagtailmedia.transcoding_backends.aws import EMCTranscodingBackend
from wagtailmedia.utils import get_media_transcoding_backend


logger = logging.getLogger(__name__)


@register(Tags.compatibility)
def check_aws_transcoding_backend_configuration(app_configs, **kwargs):
    """
    Check AWS transcoding backend configuration at startup.

    Validates that required AWS settings are present when the AWS transcoding
    backend is configured. This catches configuration errors early, before
    runtime.

    Returns:
        List of Error or Warning objects for any configuration issues
    """
    errors = []

    # Only run checks if backend is configured
    backend_path = getattr(wagtailmedia_settings, "TRANSCODING_BACKEND", "")
    if not backend_path:
        return errors

    # System check execution order isn't guaranteed, so this defensive check is required
    # even though this is caught by the general transcoding backend checks
    if type(backend_path) is not str:
        return errors

    # Only run checks if AWS backend is configured
    backend = get_media_transcoding_backend()
    if backend and not issubclass(backend, EMCTranscodingBackend):
        return errors

    # Check boto3 package available in environment
    try:
        import boto3  # noqa: F401
    except ImportError:
        errors.append(
            Error(
                "boto3 is required for AWS transcoding backend but is not installed",
                hint=(
                    "Install boto3 with: pip install boto3, "
                    "or install wagtailmedia with AWS extras: pip install wagtailmedia[aws]"
                ),
                id="wagtailmedia.E200",
            )
        )

    # Check required settings for AWS transcoding
    required_settings = {
        "AWS_STORAGE_BUCKET_NAME": (
            "Destination S3 bucket for transcoded media files. "
            "This bucket will store the output of MediaConvert jobs."
        ),
    }

    for setting_name, description in required_settings.items():
        value = getattr(settings, setting_name, None)
        if not value:
            errors.append(
                Error(
                    f"{setting_name} is required for AWS transcoding backend",
                    hint=f"{description} Add it to your Django settings.",
                    id="wagtailmedia.E201",
                )
            )

    # Check optional settings and provide warnings for defaults
    optional_settings = {
        "AWS_MEDIACONVERT_ROLE_NAME": (
            "MediaConvert_Default_Role",
            "IAM role that MediaConvert will assume to access S3 and other AWS resources.",
        ),
        "AWS_MEDIACONVERT_QUEUE_NAME": (
            "Default",
            "MediaConvert queue name for job submission.",
        ),
    }

    for setting_name, (default_value, description) in optional_settings.items():
        value = getattr(settings, setting_name, None)
        if not value:
            errors.append(
                Warning(
                    f"{setting_name} not set, using default: '{default_value}'",
                    hint=f"{description} Set explicitly if using a different value.",
                    id="wagtailmedia.E203",
                )
            )

    if not errors:
        logger.debug(f"Using transcoding backend: {backend_path.split('.')[-1]}")

    return errors
