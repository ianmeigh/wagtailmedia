from wagtail.permission_policies import BasePermissionPolicy
from wagtail.permission_policies.collections import CollectionOwnershipPermissionPolicy

from wagtailmedia.models import Media, get_media_model


permission_policy = CollectionOwnershipPermissionPolicy(
    get_media_model(), auth_model=Media, owner_field_name="uploaded_by_user"
)


class TranscodingJobPermissionPolicy(BasePermissionPolicy):
    """
    Permission policy for MediaTranscodingJob that restricts access to read-only
    and ties permissions to the related Media object.

    Transcoding jobs are system-managed, so users cannot add, edit, or delete them
    through the admin. Users can only view jobs for media they have access to,
    respecting collection-level permissions.
    """

    def __init__(self, model):
        super().__init__(model)
        # Delegate permission checks to the media permission policy
        self.media_permission_policy = permission_policy

    def user_has_permission(self, user, action):
        """
        Check if user has permission to perform an action on ANY transcoding job.
        """

        # Transcoding jobs are read-only
        if action in ["add", "change", "delete"]:
            return False

        # User can access jobs if they can view/change any media
        return self.media_permission_policy.user_has_any_permission(
            user, ["view", "change"]
        )

    def user_has_permission_for_instance(self, user, action, instance):
        """
        Check if user has permission to perform an action on a SPECIFIC transcoding job.
        """
        # Transcoding jobs are read-only
        if action in ["add", "change", "delete"]:
            return False

        # Check permission on the related media object via the ForeignKey
        return self.media_permission_policy.user_has_permission_for_instance(
            user, "view", instance.media
        )

    def instances_user_has_any_permission_for(self, user, actions):
        """
        Return a queryset of transcoding jobs the user has permission to access.
        """

        # Get all media objects the user can access
        accessible_media = (
            self.media_permission_policy.instances_user_has_any_permission_for(
                user, ["view", "change"]
            )
        )

        # Filter jobs to only those related to accessible media
        return self.model.objects.filter(media__in=accessible_media)

    def users_with_any_permission(self, actions):
        """
        Return a queryset of users who have any permission on transcoding jobs.
        """

        from django.contrib.auth import get_user_model

        return get_user_model().objects.filter(is_active=True)
