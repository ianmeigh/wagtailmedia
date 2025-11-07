import logging

from urllib.parse import urlparse

from django_tasks import task

from wagtailmedia.models import (
    MediaRendition,
    MediaTranscodingJob,
    TranscodingJobStatus,
)
from wagtailmedia.transcoding_backends.aws.exceptions import (
    TranscodingJobNotFound,
)
from wagtailmedia.transcoding_backends.aws.parsers import map_aws_status_to_internal


logger = logging.getLogger(__name__)


@task()
def create_rendition(
    transcoding_job_id: int,
    output_file_path: str,
    duration_ms: int,
    width: int,
    height: int,
    bitrate: int,
):
    # TODO: If storage backend not S3 (or same bucket) copy the file to the default storage backend
    # 1. Get backend (from django.core.files.storage import default_storage)
    # 2. Save file content to file like object
    # 3. Create model instance with file like object
    # 4. Remove from S3?
    transcoding_job = MediaTranscodingJob.objects.get(pk=transcoding_job_id)

    o = urlparse(output_file_path)
    s3_key = o.path.lstrip("/")
    duration = duration_ms / 1000

    # Create the MediaRendition linked to the media from the job
    rendition = MediaRendition.objects.create(
        media=transcoding_job.media,
        transcoding_job=transcoding_job,
        file=s3_key,
        width=width,
        height=height,
        duration=duration,
        bitrate=bitrate,
    )

    logger.info("Created rendition (%s) for %s", rendition, rendition.media)


def process_aws_job_status_update(
    job_id: str, aws_status: str, detail: dict, output_detail=None
):
    """
    Process AWS MediaConvert job status update.
    """
    job = get_transcoding_job(job_id)

    # Skip if already complete
    if job.status == TranscodingJobStatus.COMPLETE:
        logger.debug("Job %s already complete, skipping update", job_id)
        return job

    # Map and update status
    internal_status = map_aws_status_to_internal(aws_status)
    job.update_status(internal_status, detail)

    # Create rendition if complete
    if internal_status == TranscodingJobStatus.COMPLETE and output_detail:
        # Enqueue as background task
        create_rendition.enqueue(
            job.pk,
            output_file_path=output_detail.output_file_path,
            duration_ms=output_detail.duration_ms,
            width=output_detail.width_px,
            height=output_detail.height_px,
            bitrate=output_detail.average_bitrate,
        )

    return job


def get_transcoding_job(job_id):
    try:
        media_transcoding_job = MediaTranscodingJob.objects.get(job_id=job_id)
    except MediaTranscodingJob.DoesNotExist as err:
        logger.warning("Unknown job: %s", job_id)
        raise TranscodingJobNotFound from err

    return media_transcoding_job
