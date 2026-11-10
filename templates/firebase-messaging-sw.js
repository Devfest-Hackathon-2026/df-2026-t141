importScripts("https://www.gstatic.com/firebasejs/13.1.0/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/13.1.0/firebase-messaging-compat.js");

firebase.initializeApp({{ firebase | tojson }});
firebase.messaging();
