"""Django system checks for wagtailmedia configuration."""

import importlib

from django.core.checks import Error, Tags, register

from wagtailmedia.settings import wagtailmedia_settings


@register(Tags.compatibility)
def check_transcoding_backend_configuration(app_configs, **kwargs):
    """
    Check that the configured transcoding backend is valid.

    Validates that:
    1. If TRANSCODING_BACKEND is set, the import path is valid
    2. The imported class is a subclass of AbstractTranscodingBackend

    Args:
        app_configs: List of app configs to check
        **kwargs: Additional keyword arguments

    Returns:
        List of Error objects for any configuration issues
    """
    errors = []

    backend_path = getattr(wagtailmedia_settings, "TRANSCODING_BACKEND", None)

    # If no backend configured, that's fine - it's optional
    if not backend_path:
        return errors

    if type(backend_path) is not str:
        errors.append(
            Error(
                "Cannot import backend module",
                hint=f"Check that TRANSCODING_BACKEND path is correct: '{backend_path}'",
                id="wagtailmedia.E100",
            )
        )
        return errors

    # Try to import the backend
    try:
        module_path, class_name = backend_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        backend_class = getattr(module, class_name)
    except ValueError:
        errors.append(
            Error(
                f"Invalid TRANSCODING_BACKEND path: '{backend_path}'",
                hint=(
                    "TRANSCODING_BACKEND must be a dotted path to a class, "
                    "e.g., 'myapp.backends.MyBackend'"
                ),
                id="wagtailmedia.E100",
            )
        )
        return errors
    except ModuleNotFoundError as err:
        errors.append(
            Error(
                f"Cannot import transcoding backend module: {err}",
                hint=f"Module '{module_path}' from TRANSCODING_BACKEND cannot be found.",
                id="wagtailmedia.E100",
            )
        )
        return errors
    except AttributeError as err:
        errors.append(
            Error(
                f"Cannot retrieve class name from module: {err}",
                hint=f"Check that TRANSCODING_BACKEND path is correct: '{backend_path}'",
                id="wagtailmedia.E100",
            )
        )
        return errors

    # Validate it's a proper backend class
    from wagtailmedia.transcoding_backends.base import AbstractTranscodingBackend

    if not issubclass(backend_class, AbstractTranscodingBackend):
        errors.append(
            Error(
                f"Transcoding backend '{class_name}' must inherit from AbstractTranscodingBackend",
                hint=(
                    f"Class '{backend_path}' does not inherit from "
                    "'wagtailmedia.transcoding_backends.base.AbstractTranscodingBackend'"
                ),
                id="wagtailmedia.E101",
            )
        )

    return errors
