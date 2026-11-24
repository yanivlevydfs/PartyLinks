"""Events posted by signed-in users ("community" events): a signed-in user fills the /submit form (when, where,
categories, ticket links or free entry without a ticket, pictures, an optional sponsored-link request), passes the
CAPTCHA, and the event is saved as the ``community`` source's listing through the same enrichment and upsert as
every scraper's. An account's first event waits for an admin; once one was approved, its next events go live at
once. Tables ``community_events`` and ``community_uploads`` (models), the form's checks (schemas, links, captcha),
pictures (images), listing / unlisting (publish), endpoints (routes), settings ``community:`` (config)."""

SOURCE = "community"  # EventRow.source of every posted event; never a scraper (not in scrapers.SCRAPERS)
