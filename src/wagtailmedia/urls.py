"""
URL patterns for wagtailmedia webhooks.

To enable webhooks in your project, include these URLs in your urlconf:

    from django.urls import path, include

    urlpatterns = [
        # ... the rest of your URLconf goes here ...
        path('media/webhooks/', include('wagtailmedia.urls')),
    ]

This will make the webhook available at: /media/webhooks/transcoding/
"""

from django.conf import settings
from django.urls import path


app_name = "wagtailmedia"

urlpatterns = []

if getattr(settings, "AWS_WEBHOOK_API_KEY", None):
    from wagtailmedia.views.aws_webhooks import AWSTranscodingWebhookView

    urlpatterns.append(
        path(
            "aws-transcoding/",
            AWSTranscodingWebhookView.as_view(),
            name="aws_transcoding_webhook",
        ),
    )
