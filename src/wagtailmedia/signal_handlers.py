from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models.signals import post_delete, post_save

from wagtailmedia.models import (
    MediaTranscodingJob,
    TranscodingJobStatus,
    get_media_model,
)
from wagtailmedia.transcoding_backends.aws import TranscodingError
from wagtailmedia.utils import get_media_transcoding_backend


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

        transcoding_job, created = MediaTranscodingJob.objects.get_or_create(
            media=instance,
            status=TranscodingJobStatus.PENDING,
        )

        if not created:
            # Job already exists, skip transcoding
            return

        try:
            response = backend.start_transcode(file)
            transcoding_job.job_id = response["Job"]["Id"]
            transcoding_job.backend = f"{backend_cls.__module__}.{backend_cls.__name__}"
            transcoding_job.save()
        except TranscodingError as err:
            # All backend-specific errors inherit from this
            transcoding_job.status = TranscodingJobStatus.FAILED
            transcoding_job.metadata = {
                "error_type": err.__class__.__name__,
                "error": str(err),
            }
            transcoding_job.save()
        except ImproperlyConfigured:
            transcoding_job.delete()
            raise
        except Exception as err:
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
