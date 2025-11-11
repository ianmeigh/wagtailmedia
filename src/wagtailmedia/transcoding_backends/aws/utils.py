"""
Shared utilities for AWS services.

This module provides common AWS functionality used by the transcoding backend
and management commands. It handles lazy importing of boto3 to keep it as an
optional dependency.
"""

# Module-level cache for boto3 imports
_boto3 = None
_botocore_exceptions = None


def import_boto3():
    """
    Lazy import of boto3 with module-level caching.

    Returns:
        tuple: (boto3 module, botocore.exceptions module)

    """
    global _boto3, _botocore_exceptions

    if _boto3 is not None:
        return _boto3, _botocore_exceptions

    import boto3
    import botocore.exceptions

    _boto3 = boto3
    _botocore_exceptions = botocore.exceptions
    return _boto3, _botocore_exceptions
