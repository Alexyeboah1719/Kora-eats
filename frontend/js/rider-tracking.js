/* Shares the rider's GPS position with the server while they're online (Browser Geolocation API). */
const RiderTracker = (() => {
  let watchId = null, last = 0;
  function start() {
    if (watchId !== null) return;
    if (!navigator.geolocation) return UI.toast("This device can't share its location.", "error");
    watchId = navigator.geolocation.watchPosition((pos) => {
      const now = Date.now();
      if (now - last < 8000) return;             // at most one update every 8 seconds
      last = now;
      App.api("/rider/location", { method: "POST", body: { lat: pos.coords.latitude, lng: pos.coords.longitude } });
    }, () => UI.toast("Turn on location so customers can track you.", "error"), { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 });
  }
  function stop() { if (watchId !== null) { navigator.geolocation.clearWatch(watchId); watchId = null; } }
  return { start, stop };
})();
