"""
Shared utilities for AWS services.

This module provides common AWS functionality used by the transcoding backend
and management commands. It handles lazy importing of boto3 to keep it as an
optional dependency.
"""

from django.core.exceptions import ImproperlyConfigured


# Module-level cache for boto3 imports
_boto3 = None
_botocore_exceptions = None


def import_boto3():
    """
    Lazy import of boto3 with module-level caching.

    This function imports boto3 and botocore only when first called, rather than at
    module import time. This allows the module to be imported without boto3 installed,
    supporting optional dependencies.

    Returns:
        tuple: (boto3 module, botocore.exceptions module)

    Raises:
        ImproperlyConfigured: If boto3 is not installed

    Example:
        - boto3, botocore_exceptions = import_boto3()
        - client = boto3.client('s3')
    """
    global _boto3, _botocore_exceptions

    if _boto3 is not None:
        return _boto3, _botocore_exceptions

    try:
        import boto3
        import botocore.exceptions

        _boto3 = boto3
        _botocore_exceptions = botocore.exceptions
        return _boto3, _botocore_exceptions
    except ImportError as err:
        # FIXME: Handle this in a system check rather than at runtime
        raise ImproperlyConfigured(
            "boto3 is required for AWS features. "
            "Install with: pip install wagtailmedia[aws]"
        ) from err


def create_boto3_client(service_name, **kwargs):
    """
    Create a boto3 client with consistent error handling.

    Factory function for creating boto3 service clients with standardized error handling
    for common AWS credential and configuration issues.

    Args:
        service_name: AWS service name (e.g., 's3', 'mediaconvert', 'iam', 'sqs')
        **kwargs: Additional arguments passed to boto3.client
                 (e.g., region_name, endpoint_url, aws_access_key_id)

    Returns:
        boto3.client: Configured boto3 client instance for the specified service

    Raises:
        ImproperlyConfigured: If boto3 is not installed, credentials are invalid,
                            or AWS region is not specified

    Example:
        - s3_client = create_boto3_client('s3', region_name='us-east-1')
        - sqs_client = create_boto3_client('sqs', endpoint_url='http://localhost:4566')
    """
    boto3, botocore_exceptions = import_boto3()

    try:
        return boto3.client(service_name, **kwargs)
    except botocore_exceptions.PartialCredentialsError as err:
        raise ImproperlyConfigured(f"Incomplete AWS credentials: {err}") from err
    except botocore_exceptions.NoCredentialsError as err:
        raise ImproperlyConfigured("No AWS credentials found.") from err
    except botocore_exceptions.NoRegionError as err:
        raise ImproperlyConfigured("AWS region not specified.") from err
