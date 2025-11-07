from django.conf import settings


class AWSTranscodingConfig:
    def __init__(self):
        self.destination_bucket = settings.AWS_STORAGE_BUCKET_NAME
        self.mediaconvert_role = getattr(
            settings,
            "AWS_MEDIACONVERT_ROLE_NAME",
            "MediaConvert_Default_Role",
        )
        self.mediaconvert_queue = getattr(
            settings,
            "AWS_MEDIACONVERT_QUEUE_NAME",
            "Default",
        )
