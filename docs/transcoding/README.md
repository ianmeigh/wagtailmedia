## How to display a rendition

### As a regular Django field

You can use `MediaRendition` as a regular Django field. Here’s an example:

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
