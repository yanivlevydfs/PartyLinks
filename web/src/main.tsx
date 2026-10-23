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
import TermsPage from "./pages/Terms";
import { useStore } from "./store";
import { Layout } from "./ui";
import "./styles.css";
// Self-hosted fonts: no request (and no visitor IP) goes to Google Fonts.
import "@fontsource/heebo/400.css";
import "@fontsource/heebo/500.css";
import "@fontsource/heebo/700.css";
import "@fontsource/heebo/900.css";
import "@fontsource/rubik/700.css";
import "@fontsource/rubik/900.css";

// Leaflet is only loaded when the map is opened.
const MapPage = lazy(() => import("./pages/MapPage"));
const AdminPage = lazy(() => import("./pages/Admin"));

// ?lang=en / ?lang=he (used by hreflang links and the sitemap) picks the language.
const urlLang = new URLSearchParams(location.search).get("lang");
if (urlLang === "he" || urlLang === "en") useStore.getState().setLang(urlLang);

// Landing URLs that exist as routes but not as pages (/city/haifa with no events, an unknown genre):
// the server answered 404, which it marks noindex (landing pages never are). Show the 404 page for
// that URL instead of an empty listing.
const serverMissing = document.querySelector('meta[name="robots"][content^="noindex"]') ? location.pathname : null;
type LandingKind = "tonight" | "weekend" | "genre" | "city";
function Landing({ landing }: { landing: LandingKind }) {
  return useLocation().pathname === serverMissing ? <NotFound /> : <Home landing={landing} />;
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Home /> },
      { path: "/tonight", element: <Landing landing="tonight" /> },
      { path: "/weekend", element: <Landing landing="weekend" /> },
      { path: "/genre/:genre", element: <Landing landing="genre" /> },
      { path: "/genre/:genre/:when", element: <Landing landing="genre" /> },
      { path: "/city/:city", element: <Landing landing="city" /> },
      { path: "/event/:id", element: <EventPage /> },
      { path: "/map", element: <Suspense><MapPage /></Suspense> },
      { path: "/abroad", element: <Abroad /> },
      { path: "/venues", element: <VenuesIndex /> },
      { path: "/venue/:name", element: <VenuePage /> },
      { path: "/artists", element: <ArtistsIndex /> },
      { path: "/artist/:name", element: <ArtistPage /> },
      { path: "/blog", element: <BlogIndex /> },
      { path: "/blog/category/:category", element: <BlogIndex /> },
      { path: "/blog/:slug", element: <BlogPost /> },
      { path: "/accessibility", element: <AccessibilityPage /> },
      { path: "/admin", element: <Suspense><AdminPage /></Suspense> },
      { path: "*", element: <NotFound /> },
      { path: "/privacy", element: <PrivacyPage /> },
      { path: "/terms", element: <TermsPage /> },
      { path: "/about", element: <AboutPage /> },
      { path: "/faq", element: <FaqPage /> },
      { path: "/contact", element: <ContactPage /> },
      { path: "/partners", element: <PartnersPage /> },
    ],
  },
]);

// Accessibility widget (Sienna): text size, contrast, dyslexia font, stop animations, big cursor...
// Its own chunk, loaded after the app; it follows <html lang> so עב/EN switches its language too.
import("sienna-accessibility");

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
