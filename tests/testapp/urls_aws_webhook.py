from django.urls import path
from wagtail.api.v2.router import WagtailAPIRouter

from wagtailmedia.api.views import MediaAPIViewSet
from wagtailmedia.views.aws_webhooks import AWSTranscodingWebhookView


api_router = WagtailAPIRouter("wagtailapi_v2")
api_router.register_endpoint("media", MediaAPIViewSet)

urlpatterns = [
    path(
        "aws-transcoding-test/",
        AWSTranscodingWebhookView.as_view(),
        name="aws_transcoding_webhook",
    ),
]
