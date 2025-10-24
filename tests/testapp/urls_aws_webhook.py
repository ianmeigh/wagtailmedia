from django.urls import path

from wagtailmedia.views.aws_webhooks import AWSTranscodingWebhookView


urlpatterns = [
    path(
        "aws-transcoding-test/",
        AWSTranscodingWebhookView.as_view(),
        name="aws_transcoding_webhook",
    ),
]
