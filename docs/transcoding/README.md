# Working with Media Renditions

## Overview

Media renditions are transcoded versions of your original media files. When you upload a video or audio file, wagtailmedia can automatically create renditions in different formats, resolutions, or quality levels using a configured transcoding backend.

**Why use renditions?**

- Deliver media in formats optimized for different devices and browsers
- Provide multiple quality levels for adaptive streaming
- Reduce file sizes for faster loading
- Ensure browser compatibility

**Prerequisites:** Before using renditions, you need to configure a transcoding backend. Currently we support AWS Elemental MediaConvert, please see the [AWS Elemental MediaConvert setup](aws_elemental_mediaconvert.md) documentation.

## How to display a rendition

### As a Django field

You can use `MediaRendition` as a Django field. Here’s an example:

```python
from django.db import models

from wagtail.fields import RichTextField
from wagtail.models import Page
from wagtail.admin.panels import FieldPanel

from wagtailmedia.edit_handlers import MediaChooserPanel


class BlogPageWithMedia(Page):
    author = models.CharField(max_length=255)
    date = models.DateField("Post date")
    body = RichTextField(blank=False)
    featured_media = models.ForeignKey(
        "wagtailmedia.MediaRendition",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    content_panels = Page.content_panels + [
        FieldPanel("author"),
        FieldPanel("date"),
        FieldPanel("body"),
        MediaChooserPanel("featured_media"),
    ]
```

The `MediaChooserPanel` accepts the `media_type` keyword argument (kwarg) to limit the types of media that can be chosen or uploaded.

At the moment only "audio" (`MediaChooserPanel(media_type="audio")`) and "video" (`MediaChooserPanel(media_type="audio")`) are supported, and any other type will make the chooser behave as if it did not get any kwarg.

#### Name clash with Wagtail

See [README section] of the same name (../../README.md#name-clash-with-wagtail).

### In StreamField

You can use `Media` in a StreamField and add logic to retrieve the first rendition. To do this, you need to add a new block class that inherits from `wagtailmedia.blocks.VideoChooserBlock` and extend the `render_basic` method to check for a rendition.

Here is an example:

```python
from django.db import models

from wagtail import blocks
from wagtail.admin.panels import FieldPanel
from wagtail.fields import StreamField
from wagtail.models import Page

from wagtailmedia.blocks import VideoChooserBlock


class MediaBlock(VideoChooserBlock):
    def render_basic(self, value, context=None):
        if value and (rendition := value.renditions.first()):
            value = rendition
        return super().render_basic(value, context)


class BlogPage(Page):
    author = models.CharField(max_length=255)
    date = models.DateField("Post date")
    body = StreamField(
        [
            ("heading", blocks.CharBlock(classname="title", icon="title")),
            ("paragraph", blocks.RichTextBlock(icon="pilcrow")),
            ("media", MediaBlock(icon="media")),
        ]
    )

    content_panels = Page.content_panels + [
        FieldPanel("author"),
        FieldPanel("date"),
        FieldPanel("body"),
    ]
```

## Troubleshooting

### Renditions haven't been generated yet

Renditions are created asynchronously after media is uploaded. If no renditions exist yet:

- **Check transcoding status**: The media object may still be processing
- **Implement fallback logic**: Always provide a fallback to the original media file

Example with fallback:

```python
class MediaBlock(VideoChooserBlock):
    def render_basic(self, value, context=None):
        if value and (rendition := value.renditions.first()):
            value = rendition
        # If no rendition exists, value remains the original media
        return super().render_basic(value, context)
```

### No renditions are being created

If renditions are never generated:

1. **Verify transcoding backend is configured**: Check your `WAGTAILMEDIA` settings include a valid `TRANSCODING_BACKEND`
1. **Review backend-specific setup**: For AWS, verify IAM roles, S3 buckets, and MediaConvert access - see [AWS setup guide](aws_elemental_mediaconvert.md)
1. **Check logs**: Look for transcoding errors in your Django logs or backend service logs
