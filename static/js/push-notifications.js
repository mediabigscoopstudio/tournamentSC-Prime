// Vanilla-JS Firebase Cloud Messaging setup — registers the service worker,
// requests browser notification permission, and posts the resulting token to
// Django. Only loaded (see base.html) when USE_FCM_PUSH is on and the signed
// -in user has fcm_notifications enabled.
(function () {
  if (!('serviceWorker' in navigator) || !('Notification' in window)) return;
  if (!window.FIREBASE_CONFIG || !window.FIREBASE_CONFIG.apiKey) return;

  function getCookie(name) {
    var match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
    return match ? match.pop() : '';
  }

  function registerToken(token) {
    fetch('/notifications/fcm-register', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken'),
      },
      body: JSON.stringify({ token: token, device_type: 'web' }),
    }).catch(function () { /* best-effort — silently retry next page load */ });
  }

  var swUrl = '/static/js/firebase-messaging-sw.js?' + new URLSearchParams(window.FIREBASE_CONFIG).toString();
  navigator.serviceWorker.register(swUrl).then(function (registration) {
    firebase.initializeApp(window.FIREBASE_CONFIG);
    var messaging = firebase.messaging();

    function requestAndRegister() {
      messaging.getToken({ vapidKey: window.FIREBASE_VAPID_KEY, serviceWorkerRegistration: registration })
        .then(function (token) { if (token) registerToken(token); })
        .catch(function (err) { console.warn('FCM token fetch failed:', err); });
    }

    if (Notification.permission === 'granted') {
      requestAndRegister();
    } else if (Notification.permission !== 'denied') {
      Notification.requestPermission().then(function (permission) {
        if (permission === 'granted') requestAndRegister();
      });
    }

    messaging.onMessage(function (payload) {
      var n = payload.notification || {};
      if (Notification.permission === 'granted') {
        new Notification(n.title || 'TournamentSC', { body: n.body, icon: '/static/images/favicon.png' });
      }
    });
  }).catch(function (err) {
    console.warn('Service worker registration failed:', err);
  });
})();
