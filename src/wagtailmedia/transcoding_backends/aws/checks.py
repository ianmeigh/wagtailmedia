import json
import logging

from pathlib import Path

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from wagtailmedia.settings import wagtailmedia_settings
from wagtailmedia.transcoding_backends.aws.backend import AWSMediaConvertBackend
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
    if not isinstance(backend_path, str):
        return errors

    # Only run checks if AWS backend is configured
    backend = get_media_transcoding_backend()
    if backend and not issubclass(backend, AWSMediaConvertBackend):
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
    mediaconvert_bucket = getattr(
        settings, "AWS_MEDIACONVERT_STORAGE_BUCKET_NAME", None
    )
    storage_bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", None)

    # Check if we have a bucket (either explicit MediaConvert or fallback to storage)
    if not mediaconvert_bucket and not storage_bucket:
        errors.append(
            Error(
                "S3 bucket configuration required for AWS transcoding backend",
                hint=(
                    "Set AWS_STORAGE_BUCKET_NAME (if using S3 storage) or "
                    "AWS_MEDIACONVERT_STORAGE_BUCKET_NAME (for a dedicated transcoding workspace). "
                    "AWS_MEDIACONVERT_STORAGE_BUCKET_NAME will default to AWS_STORAGE_BUCKET_NAME if not specified."
                ),
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


@register(Tags.compatibility)
def check_transcoding_profile_configuration(app_configs, **kwargs):
    """
    Check transcoding profile configuration at startup.

    Validates that TRANSCODING_PROFILE is set, file exists, JSON is valid, and has
    required structure for input and output key value pairs to be added.

    Returns:
        List of any configuration issues
    """
    errors = []

    backend_path = getattr(wagtailmedia_settings, "TRANSCODING_BACKEND", "")
    if not backend_path:
        return errors

    backend = get_media_transcoding_backend()
    if backend and not issubclass(backend, AWSMediaConvertBackend):
        return errors

    profile_path = getattr(wagtailmedia_settings, "TRANSCODING_PROFILE", "")
    if not profile_path:
        errors.append(
            Error(
                "TRANSCODING_PROFILE not configured",
                hint=(
                    "Set WAGTAILMEDIA['TRANSCODING_PROFILE'] to path of profile JSON file. "
                    "Export job template from AWS MediaConvert Console and save as JSON file."
                ),
                id="wagtailmedia.E210",
            )
        )
        return errors

    # Resolve path relative to BASE_DIR
    path_obj = Path(profile_path)
    if not path_obj.is_absolute():
        path_obj = Path(settings.BASE_DIR) / profile_path

    if not path_obj.exists():
        errors.append(
            Error(
                f"Transcoding profile file not found: {path_obj}",
                hint="Create the file or update WAGTAILMEDIA['TRANSCODING_PROFILE']",
                id="wagtailmedia.E211",
            )
        )
        return errors

    if not path_obj.is_file():
        errors.append(
            Error(
                f"Transcoding profile path is not a file: {path_obj}",
                id="wagtailmedia.E212",
            )
        )
        return errors

    # Check JSON is valid
    try:
        with open(path_obj, encoding="utf-8") as f:
            profile_data = json.load(f)
    except json.JSONDecodeError as e:
        errors.append(
            Error(
                f"Transcoding profile has invalid JSON: {e}",
                hint="Validate JSON syntax using a JSON validator",
                id="wagtailmedia.E213",
            )
        )
        return errors
    except Exception as e:
        errors.append(
            Error(
                f"Failed to read transcoding profile: {e}",
                id="wagtailmedia.E214",
            )
        )
        return errors

    # Validate dict structure
    if not isinstance(profile_data, dict):
        errors.append(
            Error(
                "Transcoding profile must be a JSON object",
                id="wagtailmedia.E215",
            )
        )
        return errors

    # Check that Settings key exists and is a valid object
    settings_dict = profile_data.get("Settings")
    if not isinstance(settings_dict, dict):
        errors.append(
            Error(
                "Transcoding profile missing or invalid 'Settings' object",
                hint="Profile must be exported from AWS MediaConvert Job Template",
                id="wagtailmedia.E215a",
            )
        )
        return errors

    # Check Inputs list exists and is a valid
    if "Inputs" not in settings_dict:
        errors.append(
            Error(
                "Transcoding profile missing 'Inputs' array",
                hint="Profile must have Inputs array. Export from AWS MediaConvert Job Template.",
                id="wagtailmedia.E216",
            )
        )
    elif (
        not isinstance(settings_dict["Inputs"], list)
        or len(settings_dict["Inputs"]) == 0
    ):
        errors.append(
            Error(
                "Transcoding profile Inputs must be a non-empty array",
                id="wagtailmedia.E217",
            )
        )
    elif len(settings_dict["Inputs"]) > 1:
        errors.append(
            Error(
                "Transcoding profile has multiple inputs - only first will be used",
                hint="For single file transcoding, use one input only",
                id="wagtailmedia.W218",
            )
        )

    # Check OutputGroups list exists and is valid
    if "OutputGroups" not in settings_dict:
        errors.append(
            Error(
                "Transcoding profile missing 'OutputGroups' array",
                hint="Profile must have OutputGroups array. Export from AWS MediaConvert Job Template.",
                id="wagtailmedia.E219",
            )
        )
    elif (
        not isinstance(settings_dict["OutputGroups"], list)
        or len(settings_dict["OutputGroups"]) == 0
    ):
        errors.append(
            Error(
                "Transcoding profile OutputGroups must be a non-empty array",
                id="wagtailmedia.E220",
            )
        )
    elif len(settings_dict["OutputGroups"]) > 1:
        errors.append(
            Error(
                "Transcoding profile has multiple output groups - only first will be used, but AWS will charge for all",
                hint="Remove extra output groups to avoid unnecessary transcoding costs. Keep only one output group.",
                id="wagtailmedia.E221",
            )
        )
    else:
        # Check first output group has valid structure
        output_group = settings_dict["OutputGroups"][0]
        if "Outputs" in output_group and len(output_group["Outputs"]) > 1:
            errors.append(
                Error(
                    "Transcoding profile has multiple outputs in first output group - only first will be used",
                    hint="For single rendition, use one output only. Multiple renditions will be supported in future.",
                    id="wagtailmedia.W222",
                )
            )

        # Check OutputGroupSettings exists so the destination can be added
        if "OutputGroupSettings" not in output_group:
            errors.append(
                Error(
                    "Transcoding profile first output group missing OutputGroupSettings",
                    hint="May not be able to inject destination path",
                    id="wagtailmedia.W223",
                )
            )

    return errors
