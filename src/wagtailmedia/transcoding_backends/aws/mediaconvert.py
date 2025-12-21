import botocore.exceptions as botocore_exceptions

from wagtailmedia.transcoding_backends.aws.clients import (
    get_iam_client,
    get_mediaconvert_client,
)
from wagtailmedia.transcoding_backends.aws.exceptions import (
    IAMGetRoleError,
    MediaConvertJobError,
)


class MediaConvertJobSettings:
    """
    MediaConvert job settings configurations.

    Provides static methods to generate job settings dictionaries for different
    transcoding profiles and output formats.
    """

    @staticmethod
    def webm_vp8_settings(source_url: str, destination_bucket: str) -> dict:
        """
        Build a standard WEBM/VP8/OPUS transcode job configuration.

        Creates a MediaConvert job that transcodes video to WEBM container
        with VP8 video codec (2.5 Mbps VBR, 24fps) and OPUS audio codec.

        Args:
            source_url: S3 URL of source file (s3://bucket/key format)
            destination_bucket: S3 URL of destination directory (s3://bucket/prefix/)

        Returns:
            dict: Complete MediaConvert job settings dictionary
        """

        return {
            "TimecodeConfig": {"Source": "EMBEDDED"},
            "FollowSource": 1,
            "Inputs": [
                {
                    "AudioSelectors": {
                        "Audio Selector 1": {"DefaultSelection": "DEFAULT"}
                    },
                    "TimecodeSource": "EMBEDDED",
                    "FileInput": source_url,
                }
            ],
            "OutputGroups": [
                {
                    "Name": "File Group",
                    "Outputs": [
                        {
                            "ContainerSettings": {"Container": "WEBM"},
                            "VideoDescription": {
                                "CodecSettings": {
                                    "Codec": "VP8",
                                    "Vp8Settings": {
                                        "RateControlMode": "VBR",
                                        "Bitrate": 2500000,
                                        "FramerateControl": "SPECIFIED",
                                        "FramerateNumerator": 24,
                                        "FramerateDenominator": 1,
                                    },
                                }
                            },
                            "AudioDescriptions": [
                                {
                                    "AudioSourceName": "Audio Selector 1",
                                    "CodecSettings": {
                                        "Codec": "OPUS",
                                        "OpusSettings": {},
                                    },
                                }
                            ],
                        }
                    ],
                    "OutputGroupSettings": {
                        "Type": "FILE_GROUP_SETTINGS",
                        "FileGroupSettings": {"Destination": destination_bucket},
                    },
                }
            ],
        }


class MediaConvertService:
    """
    Handles AWS MediaConvert service operations.

    Manages MediaConvert job creation, IAM role resolution, and client initialization.
    """

    def __init__(self, role, queue):
        """Initialise MediaConvert service with configuration."""
        self.role = role
        self.queue = queue

    def get_role_arn(self) -> str:
        """
        Get the IAM role ARN for MediaConvert jobs.

        Retrieves and caches the ARN for the configured IAM role that
        MediaConvert will assume when executing transcode jobs.

        Returns:
            str: Full IAM role ARN (arn:aws:iam::account-id:role/role-name)

        Raises:
            IAMGetRoleError: If role cannot be found or IAM access is denied
        """

        iam_client = get_iam_client()

        try:
            response = iam_client.get_role(RoleName=self.role)
            return response["Role"]["Arn"]
        except botocore_exceptions.ClientError as err:
            raise IAMGetRoleError(
                f"Failed to get IAM role '{self.role}': {err}"
            ) from err

    def create_transcode_job(
        self, source_url: str, destination_bucket: str, job_settings: dict
    ) -> dict:
        """
        Create and submit a MediaConvert transcode job.

        Submits a transcode job to AWS MediaConvert with the specified settings.
        The job is executed asynchronously by MediaConvert.

        Args:
            source_url: S3 URL of source file (s3://bucket/key format)
            destination_bucket: S3 URL of destination directory (s3://bucket/prefix/)
            job_settings: Complete MediaConvert job settings dictionary

        Returns:
            dict: MediaConvert CreateJob API response containing job ID and metadata

        Raises:
            MediaConvertJobError: If job creation fails (invalid settings, permissions, etc.)
        """

        mediaconvert = get_mediaconvert_client()

        role_arn = self.get_role_arn()

        try:
            response = mediaconvert.create_job(
                Role=role_arn,
                Settings=job_settings,
                Queue=self.queue,
            )
            return response
        except botocore_exceptions.ClientError as err:
            raise MediaConvertJobError(
                f"Failed to create MediaConvert job: {err}"
            ) from err
