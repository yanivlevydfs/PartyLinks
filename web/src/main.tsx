import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, useLocation } from "react-router-dom";
import EventPage from "./pages/EventPage";
import Home from "./pages/Home";
import Abroad from "./pages/Abroad";
import { ArtistPage, ArtistsIndex, VenuePage, VenuesIndex } from "./pages/Browse";
import NotFound from "./pages/NotFound";
import AccessibilityPage from "./pages/Accessibility";
import { BlogIndex, BlogPost } from "./pages/Blog";
import { AboutPage, ContactPage, FaqPage, PartnersPage } from "./pages/Info";
import PrivacyPage from "./pages/Privacy";
import StoresPage from "./pages/Stores";
import TermsPage from "./pages/Terms";
import { useStore } from "./store";
import { Layout, Loader } from "./ui";
import "./styles.css";
// Self-hosted fonts: no request (and no visitor IP) goes to Google Fonts.
import "@fontsource/heebo/400.css";
import "@fontsource/heebo/500.css";
import "@fontsource/heebo/700.css";
import "@fontsource/heebo/900.css";
import "@fontsource/rubik/700.css";
import "@fontsource/rubik/900.css";

// A deploy renames the chunks: a page opened before it fails to load the map. Reload once to get the new ones.
window.addEventListener("vite:preloadError", (e) => {
  try {
    if (Date.now() - Number(sessionStorage.getItem("chunk-reload")) < 10_000) return; // just reloaded: no loop
    sessionStorage.setItem("chunk-reload", String(Date.now()));
  } catch { /* storage blocked: still reload */ }
  e.preventDefault();
  location.reload();
});

// Leaflet is only loaded when the map is opened.
const MapPage = lazy(() => import("./pages/MapPage"));
const AdminPage = lazy(() => import("./pages/Admin"));
const AccountPage = lazy(() => import("./accounts/AccountPage")); // optional Google account (its own files: accounts/)
const PlanPage = lazy(() => import("./plans/PlanPage")); // "who's coming with me?" (its own files: plans/)

// ?lang=en / ?lang=he (used by hreflang links and the sitemap) picks the language.
const urlLang = new URLSearchParams(location.search).get("lang");
if (urlLang === "he" || urlLang === "en") useStore.getState().setLang(urlLang);

// Landing URLs that exist as routes but not as pages (/city/haifa with no events, an unknown genre):
// the server answered 404, which it marks noindex (landing pages never are). Show the 404 page for
// that URL instead of an empty listing.
const serverMissing = document.querySelector('meta[name="robots"][content^="noindex"]') ? location.pathname : null;
type LandingKind = "tonight" | "weekend" | "genre" | "city" | "region";
// The home page is a Landing too: picking a filter moves "/" to "/genre/x" (and back) without remounting
// the page, so an open filter drawer stays open.
function Landing({ landing }: { landing?: LandingKind }) {
  return useLocation().pathname === serverMissing ? <NotFound /> : <Home landing={landing} />;
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Landing /> },
      { path: "/tonight", element: <Landing landing="tonight" /> },
      { path: "/weekend", element: <Landing landing="weekend" /> },
      { path: "/genre/:genre", element: <Landing landing="genre" /> },
      { path: "/genre/:genre/:when", element: <Landing landing="genre" /> },
      { path: "/city/:city", element: <Landing landing="city" /> },
      { path: "/city/:city/:sub", element: <Landing landing="city" /> },
      { path: "/region/:region", element: <Landing landing="region" /> },
      { path: "/event/:id", element: <EventPage /> },
      { path: "/plan/:id", element: <Suspense fallback={<Loader />}><PlanPage /></Suspense> },
      { path: "/map", element: <Suspense fallback={<Loader />}><MapPage /></Suspense> },
      { path: "/abroad", element: <Abroad /> },
      { path: "/abroad/:country", element: <Abroad /> },
      { path: "/venues", element: <VenuesIndex /> },
      { path: "/venue/:name", element: <VenuePage /> },
      { path: "/artists", element: <ArtistsIndex /> },
      { path: "/artist/:name", element: <ArtistPage /> },
      { path: "/blog", element: <BlogIndex /> },
      { path: "/blog/category/:category", element: <BlogIndex /> },
      { path: "/blog/:slug", element: <BlogPost /> },
      { path: "/accessibility", element: <AccessibilityPage /> },
      { path: "/account", element: <Suspense fallback={<Loader />}><AccountPage /></Suspense> },
      { path: "/admin", element: <Suspense fallback={<Loader />}><AdminPage /></Suspense> },
      { path: "*", element: <NotFound /> },
      { path: "/privacy", element: <PrivacyPage /> },
      { path: "/terms", element: <TermsPage /> },
      { path: "/about", element: <AboutPage /> },
      { path: "/faq", element: <FaqPage /> },
      { path: "/contact", element: <ContactPage /> },
      { path: "/partners", element: <PartnersPage /> },
      { path: "/stores", element: <StoresPage /> },
    ],
  },
]);

// Accessibility widget (Sienna): text size, contrast, dyslexia font, stop animations, big cursor...
// Its own chunk, loaded after the app; it follows <html lang> so עב/EN switches its language too.
import("sienna-accessibility");

// No copying the listings: copy, cut and the context menu are blocked, except in text fields (search)
// and on the admin page. Links and buttons stay clickable.
const editable = (t: EventTarget | null) => t instanceof Element && !!t.closest("input, textarea, [contenteditable]");
for (const type of ["copy", "cut", "contextmenu", "dragstart"]) {
  document.addEventListener(type, (e) => {
    if (!editable(e.target) && !location.pathname.startsWith("/admin")) e.preventDefault();
  });
}

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 60_000, retry: 1 } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);

// The designed page has painted: fade the boot screen out (see index.html).
requestAnimationFrame(() => requestAnimationFrame(() => {
  const boot = document.getElementById("boot");
  boot?.classList.add("done");
  setTimeout(() => boot?.remove(), 400);
}));

// Offline page: the service worker answers a failed page load with /offline.html.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
