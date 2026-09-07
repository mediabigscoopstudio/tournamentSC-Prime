from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Notification, PlayerProfile, User


@receiver(post_save, sender=User)
def ensure_player_profile(sender, instance, created, **kwargs):
    """Every account gets a PlayerProfile — being a player is the baseline
    capability. Organizer capability is added separately via approval."""
    if created and not instance.is_staff:
        PlayerProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=Notification)
def push_fcm_on_notification(sender, instance, created, **kwargs):
    """Every Notification.push() call site in the codebase gets FCM delivery
    for free, gated on the recipient's preference and the feature flag."""
    if not created or not settings.USE_FCM_PUSH:
        return
    if not instance.recipient.fcm_notifications:
        return
    from .tasks import send_fcm_push_task
    send_fcm_push_task.delay(instance.pk)
