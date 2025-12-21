import boto3

from wagtailmedia.transcoding_backends.aws.config import (
    AWS_MEDIACONVERT_ACCESS_KEY_ID,
    AWS_MEDIACONVERT_REGION_NAME,
    AWS_MEDIACONVERT_SECRET_ACCESS_KEY,
)


def get_boto3_session():
    """
    Get boto3 session for transcoding operations.

    Uses MediaConvert-specific credentials if configured, otherwise falls back to
    default boto3 credential chain.
    """

    if AWS_MEDIACONVERT_ACCESS_KEY_ID and AWS_MEDIACONVERT_SECRET_ACCESS_KEY:
        return boto3.Session(
            aws_access_key_id=AWS_MEDIACONVERT_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_MEDIACONVERT_SECRET_ACCESS_KEY,
            region_name=AWS_MEDIACONVERT_REGION_NAME,
        )

    return boto3.Session()


def get_s3_client():
    """
    Get S3 client with appropriate credentials.
    """
    session = get_boto3_session()
    return session.client("s3")


def get_mediaconvert_client():
    """Get MediaConvert client with transcoding credentials."""
    session = get_boto3_session()
    return session.client("mediaconvert")


def get_iam_client():
    """Get IAM client with transcoding credentials."""
    session = get_boto3_session()
    return session.client("iam")
