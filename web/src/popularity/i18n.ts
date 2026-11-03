// Likes / views / watching-now texts, Hebrew and English (kept beside the feature, not in the site-wide i18n).
import { useT } from "../ui";

const he = {
  likes: (n: string) => `${n} אהבו`,
  views: (n: string) => `${n} צפו`,
  watching: (n: string) => `${n} צופים עכשיו`,
};

const en: typeof he = {
  likes: (n) => `${n} liked`,
  views: (n) => `${n} viewed`,
  watching: (n) => `${n} watching now`,
};

export function usePopularityText() {
  const { lang, t } = useT();
  return { ...(lang === "en" ? en : he), locale: t.locale };
}
