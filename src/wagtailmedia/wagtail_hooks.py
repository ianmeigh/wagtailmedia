from django.urls import include, path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from wagtail import hooks
from wagtail.admin.admin_url_finder import (
    ModelAdminURLFinder,
    register_admin_url_finder,
)
from wagtail.admin.menu import MenuItem
from wagtail.admin.navigation import get_site_for_user
from wagtail.admin.search import SearchArea
from wagtail.admin.site_summary import SummaryItem
from wagtail.admin.staticfiles import versioned_static
from wagtail.admin.ui.tables import Column, TitleColumn
from wagtail.admin.views.generic import IndexView
from wagtail.admin.viewsets.model import ModelViewSet

from wagtailmedia import admin_urls
from wagtailmedia.forms import GroupMediaPermissionFormSet
from wagtailmedia.models import MediaTranscodingJob, get_media_model
from wagtailmedia.permissions import TranscodingJobPermissionPolicy, permission_policy


@hooks.register("register_admin_urls")
def register_admin_urls():
    return [
        path("media/", include((admin_urls, "wagtailmedia"), namespace="wagtailmedia")),
    ]


class MediaMenuItem(MenuItem):
    def is_shown(self, request):
        return permission_policy.user_has_any_permission(
            request.user, ["add", "change", "delete"]
        )


@hooks.register("register_admin_menu_item")
def register_media_menu_item():
    return MediaMenuItem(
        _("Media"),
        reverse("wagtailmedia:index"),
        name="media",
        icon_name="media",
        order=300,
    )


class JobIndexView(IndexView):
    """
    Custom index view for transcoding jobs that filters the queryset based on
    collection-level permissions from the related Media objects.

    This ensures users only see transcoding jobs for media files they have
    permission to access, respecting collection ownership and user permissions.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        accessible_media = self.permission_policy.media_permission_policy.instances_user_has_any_permission_for(
            self.request.user, ["change", "delete"]
        )
        return qs.filter(media__in=accessible_media)


class MediaTranscodingJobViewSet(ModelViewSet):
    model = MediaTranscodingJob
    index_view_class = JobIndexView
    permission_policy = TranscodingJobPermissionPolicy(MediaTranscodingJob)
    icon = "cog"
    menu_label = "Transcoding Jobs"
    menu_order = 300
    add_to_admin_menu = True
    form_fields = []
    list_display = [
        "job_id",
        TitleColumn("media", label="Original media", url_name="wagtailmedia:edit"),
        Column("backend_class_name", label="Backend"),
        Column("get_status_display", label="Status"),
        "created_at",
    ]
    ordering = ["-created_at"]
    list_filter = ["status"]
    search_fields = ["media__title"]
    inspect_view_enabled = True
    copy_view_enabled = False


media_transcoding_job_viewset = MediaTranscodingJobViewSet("jobs")


@hooks.register("register_admin_viewset")
def register_viewset():
    return media_transcoding_job_viewset


class MediaSummaryItem(SummaryItem):
    order = 300
    template_name = "wagtailmedia/homepage/site_summary_media.html"

    def get_context_data(self, parent_context):
        site_name = get_site_for_user(self.request.user)["site_name"]
        return {
            "total_media": permission_policy.instances_user_has_any_permission_for(
                self.request.user, {"add", "change", "delete", "choose"}
            ).count(),
            "site_name": site_name,
        }

    def is_shown(self):
        return permission_policy.user_has_any_permission(
            self.request.user, ["add", "change", "delete"]
        )


@hooks.register("construct_homepage_summary_items")
def add_media_summary_item(request, items):
    items.append(MediaSummaryItem(request))


class MediaSearchArea(SearchArea):
    def is_shown(self, request):
        return permission_policy.user_has_any_permission(
            request.user, ["add", "change", "delete"]
        )


@hooks.register("register_admin_search_area")
def register_media_search_area():
    return MediaSearchArea(
        _("Media"),
        reverse("wagtailmedia:index"),
        name="media",
        icon_name="media",
        order=400,
    )


@hooks.register("register_group_permission_panel")
def register_media_permissions_panel():
    return GroupMediaPermissionFormSet


@hooks.register("describe_collection_contents")
def describe_collection_media(collection):
    media_count = get_media_model().objects.filter(collection=collection).count()
    if media_count:
        url = reverse("wagtailmedia:index") + f"?collection_id={collection.id}"
        return {
            "count": media_count,
            "count_text": ngettext(
                "%(count)s media file", "%(count)s media files", media_count
            )
            % {"count": media_count},
            "url": url,
        }


class MediaAdminURLFinder(ModelAdminURLFinder):
    permission_policy = permission_policy
    edit_url_name = "wagtailmedia:edit"


register_admin_url_finder(get_media_model(), MediaAdminURLFinder)


@hooks.register("insert_global_admin_css")
def add_media_css_tweaks():
    return format_html(
        '<link rel="stylesheet" href="{}">',
        versioned_static("wagtailmedia/css/wagtailmedia.css"),
    )


@hooks.register("insert_global_admin_css")
def add_media_comparison_css():
    return format_html(
        '<link rel="stylesheet" href="{}">',
        versioned_static("wagtailmedia/css/wagtailmedia-comparison.css"),
    )


@hooks.register("register_icons")
def register_icons(icons):
    return icons + [
        "wagtailmedia/icons/wagtailmedia-audio.svg",  # {% icon "wagtailmedia-audio" %}
        "wagtailmedia/icons/wagtailmedia-video.svg",  # {% icon "wagtailmedia-video" %}
    ]
