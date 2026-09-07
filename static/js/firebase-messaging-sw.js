// Firebase service worker — handles push notifications while the site is
// not the focused tab. Config is injected at runtime via a query string
// (see push-notifications.js) since a service worker script cannot read
// Django template context directly.
importScripts('https://www.gstatic.com/firebasejs/10.12.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.12.0/firebase-messaging-compat.js');

const params = new URLSearchParams(self.location.search);
firebase.initializeApp({
  apiKey: params.get('apiKey'),
  authDomain: params.get('authDomain'),
  projectId: params.get('projectId'),
  storageBucket: params.get('storageBucket'),
  messagingSenderId: params.get('messagingSenderId'),
  appId: params.get('appId'),
});

const messaging = firebase.messaging();

messaging.onBackgroundMessage(function (payload) {
  const notification = payload.notification || {};
  self.registration.showNotification(notification.title || 'TournamentSC', {
    body: notification.body,
    icon: '/static/images/favicon.png',
    data: payload.data || {},
  });
});

self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(clients.openWindow(url));
});
