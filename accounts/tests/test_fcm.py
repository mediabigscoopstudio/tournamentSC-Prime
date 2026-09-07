import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Notification, UserFCMToken

User = get_user_model()


class FCMTokenRegistrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='pusher@example.com', password='pw12345')

    def test_register_token_creates_row(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse('fcm_register_token'),
            data=json.dumps({'token': 'abc123', 'device_type': 'web'}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        token = UserFCMToken.objects.get(user=self.user)
        self.assertEqual(token.token, 'abc123')

    def test_reregistering_same_token_under_new_user_moves_ownership(self):
        other = User.objects.create_user(email='other@example.com', password='pw12345')
        UserFCMToken.objects.create(user=other, token='shared-token')

        self.client.force_login(self.user)
        self.client.post(
            reverse('fcm_register_token'),
            data=json.dumps({'token': 'shared-token'}), content_type='application/json')

        self.assertFalse(UserFCMToken.objects.filter(user=other).exists())
        self.assertEqual(UserFCMToken.objects.get(token='shared-token').user, self.user)


class FCMSignalGatingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='notifyme@example.com', password='pw12345')

    @override_settings(USE_FCM_PUSH=False)
    def test_push_disabled_never_enqueues_task(self):
        with patch('accounts.tasks.send_fcm_push_task.delay') as mock_delay:
            Notification.push(self.user, 'hello')
        mock_delay.assert_not_called()

    @override_settings(USE_FCM_PUSH=True)
    def test_push_enabled_enqueues_task_when_user_opted_in(self):
        self.user.fcm_notifications = True
        self.user.save(update_fields=['fcm_notifications'])
        with patch('accounts.tasks.send_fcm_push_task.delay') as mock_delay:
            notif = Notification.push(self.user, 'hello')
        mock_delay.assert_called_once_with(notif.pk)

    @override_settings(USE_FCM_PUSH=True)
    def test_push_enabled_but_user_opted_out_skips_task(self):
        self.user.fcm_notifications = False
        self.user.save(update_fields=['fcm_notifications'])
        with patch('accounts.tasks.send_fcm_push_task.delay') as mock_delay:
            Notification.push(self.user, 'hello')
        mock_delay.assert_not_called()
