from django.conf import settings


DESTINATION_BUCKET = getattr(
    settings,
    "AWS_STORAGE_BUCKET_NAME",
    "",
)
AWS_MEDIACONVERT_ROLE_NAME = getattr(
    settings, "AWS_MEDIACONVERT_ROLE_NAME", "MediaConvert_Default_Role"
)
AWS_MEDIACONVERT_QUEUE_NAME = getattr(
    settings, "AWS_MEDIACONVERT_QUEUE_NAME", "Default"
)
