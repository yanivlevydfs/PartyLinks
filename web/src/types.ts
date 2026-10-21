// Mirrors the FastAPI response models in src/partylinks/api.py.
export interface EventSummary {
  id: string;
  slug: string | null;
  source: string;
  source_event_id: string;
  url: string;
  title: string;
  start_at: string | null;
  end_at: string | null;
  local_date: string | null;
  day_of_week: string | null;
  time_slot: string | null;
  area: string | null;
  is_weekend: boolean | null;
  venue_name: string | null;
  venue_slug: string | null;
  address: string | null;
  city: string | null;
  latitude: number | null;
  longitude: number | null;
  country: string | null;
  in_target_city: boolean | null;
  genres: string[];
  event_types: string[];
  lineup: string[];
  min_age: number | null;
  image_url: string | null;
  cover_url: string | null;
  currency: string;
  min_price: number | null;
  max_price: number | null;
  entry_type: "free" | "paid" | "free_and_paid" | "unknown";
  has_free_entry: boolean;
  is_sold_out: boolean;
  free_sold_out: boolean;
  has_discount?: boolean;
  last_seen_at: string;
  first_seen_at: string;
  is_listed: boolean;
  offers_changed_at: string | null;
  // Across duplicate listings of the same event (see api.py EventSummary).
  listings?: number;
  sources?: string[];
  group_min_price?: number | null;
  group_has_free_entry?: boolean;
  group_entry_type?: EventSummary["entry_type"];
}

export interface Ticket {
  source_ticket_id: string | null;
  name: string;
  description: string | null;
  kind: string;
  status: string;
  price: number | null;
  service_fee: number | null;
  total_price: number | null;
  original_price: number | null;
  price_rounds: number[];
  currency: string;
  admits: number;
  is_free: boolean;
  pay_at_door: boolean;
  is_sold_out: boolean;
  is_active: boolean;
  remaining: number | null;
}

export interface EventImage {
  id: number;
  url: string;
  stored_url: string | null;
}

export interface EventDetail extends EventSummary {
  description: string | null;
  organizer: string | null;
  artists: string[];
  tickets: Ticket[];
  images: EventImage[];
  alternatives?: Alternative[];
}

/** Another store selling the same event. */
export interface Alternative {
  id: string;
  source: string;
  url: string;
  min_price: number | null;
  max_price: number | null;
  entry_type: EventSummary["entry_type"];
  has_free_entry: boolean;
  is_sold_out: boolean;
}

export interface EventPage {
  total: number;
  page: number;
  page_size: number;
  items: EventSummary[];
}

export interface Facet {
  value: string;
  events: number;
  area?: string; // venues: area of Tel Aviv; cities: region of the country
  // Artists only: verified profile curated by a ticket site (Go-Out, Eventer, Mevalim...).
  verified?: boolean;
  image_url?: string;
  kind?: string; // dj / producer / singer
  genre?: string;
  spotify_url?: string;
  profile_url?: string;
  links?: string[]; // Instagram, YouTube, SoundCloud, website...
}

export interface Categories {
  genres: Facet[];
  dates: Facet[];
  days_of_week: Facet[];
  time_slots: Facet[];
  areas: Facet[];
  regions: Facet[];
  entry_types: Facet[];
  venues: Facet[];
  cities: Facet[];
  lineup: Facet[];
  event_types: Facet[];
  sources: Facet[];
}

export interface PostCategory {
  key: string;
  he: string;
  en: string;
  posts?: number;
}

export interface PostSummary {
  slug: string;
  title: string;
  description: string;
  date: string;
  updated: string;
  category: PostCategory;
  image: string | null;
  read_minutes: number;
}

export interface PostList {
  categories: PostCategory[];
  items: PostSummary[];
}

/** A post body: HTML chunks and live event lists (an /events query). */
export type PostBlock = { html: string } | { events: Record<string, string> };

export interface Post extends PostSummary {
  blocks: PostBlock[];
  toc: { id: string; title: string }[];
  faq: { q: string; a: string }[];
  related: PostSummary[];
}
