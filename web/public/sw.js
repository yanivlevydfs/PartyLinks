// This site has no service worker. Browsers that installed one on this address (another app that
// ran on the same localhost port, or an older deploy) keep asking for /sw.js; this one removes itself
// and reloads the open tabs so they load straight from the network.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", async () => {
  await self.registration.unregister();
  for (const client of await self.clients.matchAll({ type: "window" })) client.navigate(client.url);
});
