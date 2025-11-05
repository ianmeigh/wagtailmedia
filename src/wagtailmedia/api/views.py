from django.db.models import Count
from wagtail.api.v2.filters import FieldsFilter, OrderingFilter, SearchFilter
from wagtail.api.v2.views import BaseAPIViewSet

from ..models import get_media_model
from .serializers import MediaItemSerializer


class MediaAPIViewSet(BaseAPIViewSet):
    base_serializer_class = MediaItemSerializer
    filter_backends = [FieldsFilter, OrderingFilter, SearchFilter]
    body_fields = BaseAPIViewSet.body_fields + [
        "title",
        "width",
        "height",
        "media_type",
        "collection",
        "renditions",
        "num_renditions",
    ]
    meta_fields = BaseAPIViewSet.meta_fields + [
        "tags",
        "download_url",
    ]
    listing_default_fields = BaseAPIViewSet.listing_default_fields + [
        "media_type",
        "title",
        "width",
        "height",
        "tags",
        "collection",
        "thumbnail",
        "download_url",
        "num_renditions",
    ]
    nested_default_fields = BaseAPIViewSet.nested_default_fields + [
        "title",
        "collection",
        "download_url",
    ]
    name = "media"
    model = get_media_model()

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.annotate(num_renditions=Count("renditions"))
