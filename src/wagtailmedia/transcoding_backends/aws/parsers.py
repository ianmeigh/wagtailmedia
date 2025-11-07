from dataclasses import dataclass

from wagtailmedia.models import TranscodingJobStatus
from wagtailmedia.transcoding_backends.aws.exceptions import DataValidationError


@dataclass
class JobDetail:
    """Normalized job details."""

    job_id: str
    status: str
    raw_detail: dict  # Full AWS response for metadata storage

    @classmethod
    def from_eventbridge_webhook(cls, payload: dict) -> "JobDetail":
        try:
            detail = payload["detail"]
            return cls(
                job_id=detail["jobId"],
                status=detail["status"],
                raw_detail=detail,
            )
        except KeyError as err:
            raise DataValidationError(f"Missing required field: {err}") from err

    @classmethod
    def from_get_job_response(cls, response: dict) -> "JobDetail":
        try:
            job = response["Job"]
            return cls(
                job_id=job["Id"],
                status=job["Status"],
                raw_detail=job,
            )
        except KeyError as err:
            raise DataValidationError(f"Missing required field: {err}") from err

    def get_output_detail(self) -> "OutputDetail | None":
        """Extract output details regardless of source."""
        if self.status != "COMPLETE":
            return None
        return OutputDetail.from_detail_dict(self.raw_detail)


@dataclass
class OutputDetail:
    """Represents the first outputDetails item from AWS MediaConvert webhook with a status of COMPLETE."""

    output_file_path: str
    duration_ms: int
    width_px: int
    height_px: int
    average_bitrate: int | None

    @classmethod
    def from_detail_dict(cls, detail: dict) -> "OutputDetail":
        """
        Parse and validate the first outputDetails item from AWS webhook detail.

        Expects detail to have structure:
        {
            'timestamp': 0,
            'accountId': 'ACCOUNT_ID',
            'queue': 'arn:aws:mediaconvert:AWS_REGION:AWS_ACCOUNT_ID:queues/AWS_MEDIACONVERT_QUEUE_NAME',
            'jobId': 'JOB_ID',
            'status': 'PROGRESSING/COMPLETE/ERROR',
            'userMetadata': {}
            'outputGroupDetails': [{
                'outputDetails': [{
                    'outputFilePaths': ['s3://FILE_PATH'],
                    'durationInMs': 0,
                    'videoDetails': {
                        'widthInPx': 0,
                        'heightInPx': 0,
                        'averageBitrate': 0
                    }
                }],
                'type': 'FILE_GROUP'
            }],
            'paddingInserted': 0,
            'blackVideoDetected': 0
        }

        Raises:
            DataValidationError: If structure is invalid or missing required fields
        """
        try:
            output_item = detail["outputGroupDetails"][0]["outputDetails"][0]
            output_paths = output_item["outputFilePaths"]

            if not output_paths or not isinstance(output_paths, list):
                raise DataValidationError("outputFilePaths must be a non-empty list")

            video_details = output_item["videoDetails"]

            return cls(
                output_file_path=output_paths[0],
                duration_ms=output_item["durationInMs"],
                width_px=video_details["widthInPx"],
                height_px=video_details["heightInPx"],
                average_bitrate=video_details.get("averageBitrate", None),
            )
        except (KeyError, IndexError, TypeError) as e:
            raise DataValidationError(f"Invalid COMPLETE webhook structure: {e}") from e


def map_aws_status_to_internal(status):
    """
    Map external service status to internal TranscodingJobStatus.
    """
    status_map = {
        "COMPLETE": TranscodingJobStatus.COMPLETE,
        "ERROR": TranscodingJobStatus.FAILED,
        "PROGRESSING": TranscodingJobStatus.PROGRESSING,
    }

    return status_map[status.upper()]
