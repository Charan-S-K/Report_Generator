"""
Management command to purge expired uploaded images and remove files from disk.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from report.models import UploadedImage


class Command(BaseCommand):
    help = "Delete expired uploaded images from the database and remove their files from disk."

    def handle(self, *args, **options):
        now = timezone.now()
        expired_qs = UploadedImage.objects.filter(expires_at__lt=now)

        deleted_count = 0
        for image in expired_qs:
            try:
                if image.file:
                    image.file.delete(save=False)
            except Exception as e:
                self.stderr.write(f"Warning: could not delete file for image {image.pk}: {e}")

            image.delete()
            deleted_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully purged {deleted_count} expired image(s) and their disk files."
            )
        )
