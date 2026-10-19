import { Link } from "react-router-dom";
import { Hero, useT } from "../ui";

export default function NotFound() {
  const { t } = useT();
  return (
    <Hero title="404">
      <p className="sub">{t.notFound} · <Link to="/">{t.nav.parties}</Link></p>
    </Hero>
  );
}
