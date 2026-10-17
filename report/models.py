"""
Models for the report generator application.
"""
from datetime import timedelta
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied


def default_expires_at():
    """Default expiration is 7 days from creation."""
    return timezone.now() + timedelta(days=7)


class UploadedImage(models.Model):
    """
    Stores metadata and file references for user-uploaded report images.
    Only the owning session_key may use an image id.
    """
    session_key = models.CharField(max_length=40, db_index=True)
    file = models.FileField(upload_to='report_images/')
    fmt = models.CharField(max_length=4)  # 'jpg' or 'png'
    size = models.PositiveIntegerField()  # Size in bytes
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_expires_at, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Image #{self.pk} ({self.fmt}, {self.size} bytes) [session={self.session_key[:8]}...]"

    @property
    def is_expired(self):
        """Check if image has expired past its expires_at timestamp."""
        return timezone.now() > self.expires_at

    def extend_expiration(self, days=7):
        """Extend image expiration by specified number of days (default 7 days)."""
        self.expires_at = timezone.now() + timedelta(days=days)
        self.save(update_fields=['expires_at'])


def get_image_for_session(image_id, session_key):
    """
    Retrieve an UploadedImage by id and ensure it belongs to the current session and has not expired.
    Raises ValidationError or PermissionDenied if invalid.
    """
    try:
        image = UploadedImage.objects.get(pk=image_id)
    except (UploadedImage.DoesNotExist, ValueError, TypeError):
        raise ValidationError(f"Image ID '{image_id}' does not exist.")

    if image.session_key != session_key:
        raise PermissionDenied(f"Image ID '{image_id}' belongs to another session.")

    if image.is_expired:
        raise ValidationError(f"Image ID '{image_id}' has expired.")

    return image
