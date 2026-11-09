// An event's description, laid out by the server (partylinks.event_text): paragraphs, headings, lists.
import { useRef, useState, type ReactNode } from "react";
import type { DescriptionLayout, TextBlock } from "../types";
import { useT } from "../ui";
import "./event_text.css";

// Bare links, Markdown links and bold, as sources write them; a link's trailing punctuation stays text.
const INLINE = /(\*\*[^*\n]+\*\*|\[[^\]\n]+\]\((?:https?:\/\/|www\.)[^)\s]+\)|(?:https?:\/\/|www\.)[^\s<>"]+)/g;
const MD_LINK = /^\[([^\]]+)\]\(([^)]+)\)$/;
const TRAILING = /[.,;:!?)\]}'"…]+$/;
// "דיזנגוף 143 - שישה במאי: ...", "📍 Location: ...": the name opening a list line stands out.
const LEAD = /^(.{2,40}?)(:\s| [-–] )/;

/** What a URL shows: the address without scheme, www or tracking (CSS cuts a long one). The link keeps all of it. */
const linkLabel = (url: string) => url.replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/[?#].*$/, "").replace(/\/$/, "");

function TextLink({ url, children }: { url: string; children: ReactNode }) {
  const href = url.startsWith("www.") ? `https://${url}` : url;
  return <a href={href} title={url} target="_blank" rel="noopener noreferrer nofollow ugc">{children}</a>;
}

function inline(text: string): ReactNode[] {
  return text.split(INLINE).flatMap((part, i): ReactNode[] => {
    if (i % 2 === 0) return part ? [part] : [];
    if (part.startsWith("**")) return [<b key={i}>{inline(part.slice(2, -2))}</b>];
    const md = MD_LINK.exec(part);
    if (md) return [<TextLink key={i} url={md[2]}>{md[1]}</TextLink>];
    const url = part.replace(TRAILING, "");
    return [<TextLink key={i} url={url}><bdi dir="ltr" className="etext-url">{linkLabel(url)}</bdi> ↗</TextLink>, part.slice(url.length)];
  });
}

function Item({ text, lead }: { text: string; lead: boolean }) {
  const found = lead && !text.includes("**") ? LEAD.exec(text) : null;
  if (!found) return <li>{inline(text)}</li>;
  return <li><b>{found[1]}</b>{found[2]}{inline(text.slice(found[0].length))}</li>;
}

function Block({ b }: { b: TextBlock }) {
  if (b.kind === "h") return <h5 dir={b.dir}>{inline(b.text)}</h5>;
  if (b.kind === "p") return <p dir={b.dir}>{inline(b.text)}</p>;
  return (
    <ul dir={b.dir} className={b.plain ? "plain" : undefined}>
      {b.items.map((text, i) => <Item key={i} text={text} lead={b.plain} />)}
    </ul>
  );
}

export function EventText({ layout }: { layout: DescriptionLayout }) {
  const { t, lang } = useT();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const shut = layout.collapse && !open;
  const toggle = () => {
    if (open) root.current?.scrollIntoView({ block: "nearest" }); // back to where the text starts
    setOpen(!open);
  };
  return (
    <div className="etext-card">
      {/* All of the text stays in the page when collapsed; tabbing into it opens it. */}
      <div ref={root} id="event-text" className={"etext" + (shut ? " shut" : "")} onFocus={() => setOpen(true)}>
        {layout.blocks.map((b, i) => <Block key={i} b={b} />)}
      </div>
      {layout.collapse && (
        <button className="etext-more" aria-expanded={open} aria-controls="event-text" onClick={toggle}>
          {open ? (lang === "he" ? "הצגת פחות" : "Show less") : t.readMore}
        </button>
      )}
    </div>
  );
}
