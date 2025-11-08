from wagtailmedia.transcoding_backends.aws.config import (
    AWS_MEDIACONVERT_QUEUE_NAME,
    AWS_MEDIACONVERT_ROLE_NAME,
    DESTINATION_BUCKET,
)
from wagtailmedia.transcoding_backends.aws.mediaconvert import (
    MediaConvertJobSettings,
    MediaConvertService,
)
from wagtailmedia.transcoding_backends.aws.s3 import S3Service
from wagtailmedia.transcoding_backends.base import (
    AbstractTranscodingBackend,
)


class EMCTranscodingBackend(AbstractTranscodingBackend):
    """
    AWS MediaConvert transcoding backend orchestrator.
    """

    def __init__(self):
        """Initialise the AWS transcoding backend."""

        self.s3_service = S3Service()
        self.mediaconvert_service = MediaConvertService(
            role=AWS_MEDIACONVERT_ROLE_NAME, queue=AWS_MEDIACONVERT_QUEUE_NAME
        )
        self.job_settings = MediaConvertJobSettings()

    def start_transcode(self, source_file) -> dict:
        """
        Start transcoding a media file using AWS MediaConvert.

        Args:
            source_file: Django file object to transcode (must have 'name' attribute
                        and optionally 'url' for web-accessible files)

        Returns:
            dict: MediaConvert CreateJob API response
        """

        # Ensure file is publicly accessible
        source_url = self.s3_service.ensure_file_is_available(
            source_file, DESTINATION_BUCKET
        )

        # Build job settings
        destination_url = f"s3://{DESTINATION_BUCKET}/"
        job_settings = self.job_settings.webm_vp8_settings(source_url, destination_url)

        # Create transcode job
        response = self.mediaconvert_service.create_transcode_job(
            source_url, destination_url, job_settings
        )

        return response

    def stop_transcode(self, task_id: str):
        """Stop a running MediaConvert transcode job."""

        raise NotImplementedError("Stop transcode is not yet implemented")
