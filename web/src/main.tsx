import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import EventPage from "./pages/EventPage";
import Home from "./pages/Home";
import { ArtistPage, ArtistsIndex, VenuePage, VenuesIndex } from "./pages/Browse";
import NotFound from "./pages/NotFound";
import AccessibilityPage from "./pages/Accessibility";
import { useStore } from "./store";
import { Layout } from "./ui";
import "./styles.css";

// Leaflet is only loaded when the map is opened.
const MapPage = lazy(() => import("./pages/MapPage"));
const AdminPage = lazy(() => import("./pages/Admin"));

// ?lang=en / ?lang=he (used by hreflang links and the sitemap) picks the language.
const urlLang = new URLSearchParams(location.search).get("lang");
if (urlLang === "he" || urlLang === "en") useStore.getState().setLang(urlLang);

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Home /> },
      { path: "/event/:id", element: <EventPage /> },
      { path: "/map", element: <Suspense><MapPage /></Suspense> },
      { path: "/venues", element: <VenuesIndex /> },
      { path: "/venue/:name", element: <VenuePage /> },
      { path: "/artists", element: <ArtistsIndex /> },
      { path: "/artist/:name", element: <ArtistPage /> },
      { path: "/accessibility", element: <AccessibilityPage /> },
      { path: "/admin", element: <Suspense><AdminPage /></Suspense> },
      { path: "*", element: <NotFound /> },
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
