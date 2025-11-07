from wagtailmedia.transcoding_backends.base import TranscodingError


class S3UploadError(TranscodingError):
    """Failed to upload file to S3."""

    pass


class IAMGetRoleError(TranscodingError):
    """Failed to get IAM role."""

    pass


class MediaConvertJobError(TranscodingError):
    """Failed to create or manage MediaConvert job."""

    pass


class DataValidationError(Exception):
    pass


class TranscodingJobNotFound(Exception):
    pass
