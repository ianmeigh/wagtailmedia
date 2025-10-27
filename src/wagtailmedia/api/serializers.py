import rest_framework.fields

from rest_framework.fields import ReadOnlyField
from wagtail.api.v2.serializers import BaseSerializer, serializers
from wagtail.api.v2.utils import get_full_url

from wagtailmedia.models import MediaRendition


class MediaDownloadUrlField(ReadOnlyField):
    """
    Serializes the "download_url" field for media items.

    Example:
    "download_url": "http://api.example.com/media/my_video.mp4"
    """

    def get_attribute(self, instance):
        return instance

    def to_representation(self, instance):
        return get_full_url(self.context["request"], instance.url)


class MediaRenditionSerializer(BaseSerializer):
    id = rest_framework.fields.IntegerField()
    duration = rest_framework.fields.FloatField()
    width = rest_framework.fields.IntegerField()
    height = rest_framework.fields.IntegerField()
    bitrate = rest_framework.fields.IntegerField()
    type = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    meta_fields = [
        "type",
        "download_url",
    ]

    class Meta:
        model = MediaRendition
        fields = [
            "id",
            "duration",
            "width",
            "height",
            "bitrate",
            "type",
            "download_url",
        ]

    def get_type(self, obj):
        return ".".join((obj.__class__.__module__, obj.__class__.__name__))

    def get_download_url(self, obj):
        return get_full_url(self.context["request"], obj.url)


class MediaItemSerializer(BaseSerializer):
    download_url = MediaDownloadUrlField()
    media_type = rest_framework.fields.CharField(source="type")
    renditions = MediaRenditionSerializer(many=True, read_only=True)
    num_renditions = serializers.SerializerMethodField()

    def get_num_renditions(self, obj):
        return getattr(obj, "num_renditions", obj.renditions.count())
