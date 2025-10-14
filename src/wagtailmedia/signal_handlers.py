import logging

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models.signals import post_delete, post_save

from wagtailmedia.models import (
    MediaTranscodingJob,
    TranscodingJobStatus,
    get_media_model,
)
from wagtailmedia.transcoding_backends.base import TranscodingError
from wagtailmedia.utils import get_media_transcoding_backend


logger = logging.getLogger(__name__)


def delete_files(instance):
    # Pass false so FileField doesn't save the model.
    instance.file.delete(False)
    if instance.thumbnail:
        instance.thumbnail.delete(False)


def post_delete_file_cleanup(instance, **kwargs):
    transaction.on_commit(lambda: delete_files(instance))


def transcode_video(instance):
    backend_cls = get_media_transcoding_backend()

    if instance.type == "video" and backend_cls:
        file = instance.file

        backend = backend_cls()

        # Check for existing active job
        existing_job = MediaTranscodingJob.objects.filter(
            media=instance,
            status__in=[TranscodingJobStatus.PENDING, TranscodingJobStatus.PROGRESSING],
        ).first()

        if existing_job:
            logger.info(
                f"Skipping transcode for media {instance.id} ({instance.title}): "
                f"Job {existing_job.job_id} already {existing_job.status}"
            )
            return

        transcoding_job = MediaTranscodingJob.objects.create(
            media=instance,
            status=TranscodingJobStatus.PENDING,
        )

        try:
            response = backend.start_transcode(file)
            transcoding_job.job_id = response["Job"]["Id"]
            transcoding_job.backend = f"{backend_cls.__module__}.{backend_cls.__name__}"
            transcoding_job.save()

            logger.info(
                f"Started transcode job {transcoding_job.job_id} for media {instance.id}"
            )
        except TranscodingError as err:
            logger.error(
                f"Transcode failed for media {instance.id} ({instance.title}): {err}",
                exc_info=True,
                extra={
                    "media_id": instance.id,
                    "error_type": err.__class__.__name__,
                },
            )

            transcoding_job.status = TranscodingJobStatus.FAILED
            transcoding_job.metadata = {
                "error_type": err.__class__.__name__,
                "error": str(err),
            }
            transcoding_job.save()
        except ImproperlyConfigured as err:
            logger.critical(
                f"AWS transcoding misconfigured for media {instance.id}: {err}",
                exc_info=True,
            )

            transcoding_job.delete()
            raise
        except Exception as err:
            logger.error(
                f"Unexpected error transcoding media {instance.id}: {err}",
                extra={"media_id": instance.id},
            )

            transcoding_job.status = TranscodingJobStatus.FAILED
            transcoding_job.metadata = {"error_type": "unknown", "error": str(err)}
            transcoding_job.save()
            raise


def post_save_transcode_video(instance, **kwargs):
    transaction.on_commit(lambda: transcode_video(instance))


def register_signal_handlers():
    Media = get_media_model()
    post_delete.connect(post_delete_file_cleanup, sender=Media)
    post_save.connect(post_save_transcode_video, sender=Media)
