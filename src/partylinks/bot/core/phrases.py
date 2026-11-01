"""What the bot says, in Hebrew and English. Content, not parameters: several ways to say each thing,
so the bot does not sound like a form. ``{n}`` / ``{q}`` / ``{km}`` are filled in by ``say``."""

from __future__ import annotations

import random

PHRASES: dict[str, dict[str, tuple[str, ...]]] = {
    "welcome": {
        "he": (
            (
                "היי! 👋 אני הבוט של SPOTX. כתבו לי מה בא לכם, ואמצא לכם מסיבות, הופעות ואירועים עם לינק לכרטיסים.\n\n"
                "למשל:\n• טכנו בתל אביב בסופ״ש\n• הופעות בחיפה מחר\n• משהו חינם הערב\n• Infected Mushroom\n\n"
                "אפשר גם לשתף מיקום 📍 ואמצא מה קורה לידכם."
            ),
        ),
        "en": (
            (
                "Hi! 👋 I'm the SPOTX bot. Tell me what you're in the mood for and I'll find parties, shows and "
                "events with ticket links.\n\nFor example:\n• techno in Tel Aviv this weekend\n• live music in Haifa "
                "tomorrow\n• something free tonight\n• Infected Mushroom\n\nOr share your location 📍 and I'll find "
                "what's on near you."
            ),
        ),
    },
    "greeting": {
        "he": (
            "היי! 😊 מה בא לכם? כתבו סגנון, עיר או תאריך, ואני על זה.",
            "אהלן! מחפשים משהו להערב או לסופ״ש?",
            "שלום שלום 👋 ספרו לי מה מעניין אתכם ואמצא לכם.",
        ),
        "en": (
            "Hey! 😊 What are you in the mood for? A style, a city or a date and I'm on it.",
            "Hi there! Looking for something tonight or this weekend?",
            "Hello 👋 Tell me what you like and I'll find it.",
        ),
    },
    "thanks": {
        "he": ("בכיף! 🙌 תהנו!", "בשמחה, שיהיה מעולה 🎉", "תמיד! אם תרצו עוד, אני פה."),
        "en": ("Anytime! 🙌 Have fun!", "My pleasure, enjoy 🎉", "Sure thing! I'm here if you want more."),
    },
    "help": {
        "he": (
            (
                "אני מוצא אירועים לפי מה שתכתבו: סגנון (טכנו, ג׳אז, סטנד אפ), מקום (תל אביב, בצפון, ברלין), "
                "זמן (הערב, מחר, בסופ״ש, בדצמבר), מחיר (עד 100, חינם, הכי זול) או אמן ומקום.\n\n"
                "אחרי חיפוש אפשר להמשיך: ״ומחר?״, ״ובחיפה?״, ״עוד״.\n"
                "🔔 ״עדכן אותי״ שולח לכם אירועים חדשים שמתאימים לחיפוש.\n"
                "מתחת לכל אירוע: ⏰ תזכורת לפני, 👁 מעקב מחיר וכרטיסים, 🔁 אירועים דומים, ➕ יומן, ״מי בא?״ "
                "ומעקב אחרי האמן או המקום.\n"
                "/alerts ההתראות · /reminders תזכורות ומעקבים · /digest סיכום שבועי · /lang שפה · /stop מחיקת הכל"
            ),
        ),
        "en": (
            (
                "I find events from what you write: a style (techno, jazz, stand-up), a place (Tel Aviv, the north, "
                "Berlin), a time (tonight, tomorrow, this weekend, in December), a price (under 100, free, cheapest) "
                'or an artist or venue.\n\nAfter a search, keep going: "and tomorrow?", "and in Haifa?", "more".\n'
                '🔔 "Alert me" sends you new events that match the search.\n'
                "Under every event: ⏰ a reminder, 👁 price and ticket watch, 🔁 similar events, ➕ calendar, "
                "\"who's coming?\" and following the artist or venue.\n"
                "/alerts alerts · /reminders reminders and watches · /digest weekly picks · /lang language · "
                "/stop delete everything"
            ),
        ),
    },
    "found": {
        "he": ("מצאתי {n} 🔥 הנה הקרובים:", "יש! {n} אירועים. הנה מה שקרוב:", "בדקתי, יש {n}. אלה הבאים בתור:"),
        "en": (
            "Found {n} 🔥 Here are the soonest:",
            "Yes! {n} events. Here's what's coming up:",
            "I checked, there are {n}. Up next:",
        ),
    },
    "found_one": {
        "he": ("מצאתי אחד בדיוק 👇", "יש אחד שמתאים 👇"),
        "en": ("Found exactly one 👇", "There's one that fits 👇"),
    },
    "found_cheap": {
        "he": ("מצאתי {n}, מהזול ליקר 💸", "יש {n}. הנה הכי משתלמים:"),
        "en": ("Found {n}, cheapest first 💸", "There are {n}. Best prices first:"),
    },
    "found_near": {
        "he": ("יש {n} אירועים עד {km} ק״מ ממך 📍 הכי קרובים קודם:", "מצאתי {n} בסביבה שלך (עד {km} ק״מ) 📍"),
        "en": ("{n} events within {km} km of you 📍 Nearest first:", "Found {n} around you (up to {km} km) 📍"),
    },
    "widened_date": {
        "he": (
            "בתאריך הזה אין, אבל בלי להגביל תאריך מצאתי {n} 👇",
            "באותו זמן לא מצאתי, אז הרחבתי את התאריכים ויש {n}:",
        ),
        "en": (
            "Nothing on that date, but without the date I found {n} 👇",
            "Nothing then, so I widened the dates: {n} events:",
        ),
    },
    "widened_place": {
        "he": ("שם לא מצאתי, אבל בכל הארץ יש {n} 👇", "באזור הזה אין כרגע, אז הרחבתי, ויש {n}:"),
        "en": (
            "Nothing there, but across the country there are {n} 👇",
            "Nothing in that area right now, so I widened it: {n}:",
        ),
    },
    "none": {
        "he": (
            "לא מצאתי משהו שמתאים ל״{q}״ 😕 נסו מילה אחרת, עיר אחרת או תאריך אחר.",
            "הפעם יצאתי בידיים ריקות עם ״{q}״. אולי בסגנון קרוב או בסופ״ש?",
        ),
        "en": (
            'I couldn\'t find anything for "{q}" 😕 Try another word, city or date.',
            'Came up empty with "{q}" this time. Maybe a similar style, or this weekend?',
        ),
    },
    "alert_hint": {
        "he": ("רוצים שאעדכן אתכם כשיופיע? לחצו 🔔",),
        "en": ("Want me to tell you when it shows up? Tap 🔔",),
    },
    "did_you_mean": {"he": ("התכוונתם ל״{q}״?",), "en": ('Did you mean "{q}"?',)},
    "next": {
        "he": ("רוצים לחדד? 👇", "עוד משהו? אפשר לחדד 👇", "מה עכשיו? 👇"),
        "en": ("Want to narrow it down? 👇", "Anything else? Refine below 👇", "What next? 👇"),
    },
    "no_more": {
        "he": ("זהו, הראיתי את כל מה שמצאתי 🙂 רוצים לחפש משהו אחר?", "אלה היו כולם! נחפש עוד משהו?"),
        "en": (
            "That's all I found 🙂 Want to look for something else?",
            "Those were all of them! Search for something else?",
        ),
    },
    "no_search": {
        "he": ("עוד לא חיפשנו כלום 🙂 כתבו מה בא לכם.",),
        "en": ("We haven't searched anything yet 🙂 Tell me what you'd like.",),
    },
    "ask_location": {
        "he": ("שתפו מיקום 📍 בכפתור למטה, ואמצא מה קורה לידכם.",),
        "en": ("Share your location 📍 with the button below and I'll find what's on near you.",),
    },
    "near_none": {
        "he": ("אין אירועים קרובים בטווח {km} ק״מ ממך כרגע 😕",),
        "en": ("No events within {km} km of you right now 😕",),
    },
    "alert_ask": {
        "he": ("לשלוח לכם הודעה כשיש אירוע חדש ל״{q}״? 🔔",),
        "en": ('Send you a message when there\'s a new event for "{q}"? 🔔',),
    },
    "alert_saved": {
        "he": ("סגור! 🔔 אעדכן אתכם כשיצוץ משהו חדש ל״{q}״. /alerts לניהול ההתראות.",),
        "en": ('Done! 🔔 I\'ll let you know when something new comes up for "{q}". /alerts to manage them.',),
    },
    "alert_exists": {"he": ("כבר יש לכם התראה על ״{q}״ 🙂",), "en": ('You already have an alert for "{q}" 🙂',)},
    "alert_full": {
        "he": ("יש לכם כבר {n} התראות, המקסימום. מחקו אחת ב-/alerts ונסו שוב.",),
        "en": ("You already have {n} alerts, the most there can be. Delete one in /alerts and try again.",),
    },
    "alert_too_long": {
        "he": ("החיפוש ארוך מדי להתראה (עד {n} תווים). נסו לקצר אותו ולחפש שוב.",),
        "en": ("That search is too long for an alert (up to {n} characters). Try a shorter one.",),
    },
    "alert_cancelled": {"he": ("בסדר, בלי התראה 👌",), "en": ("OK, no alert 👌",)},
    "alert_none_pending": {
        "he": ("אין חיפוש שמחכה לאישור. חפשו משהו ולחצו 🔔",),
        "en": ("No search waiting. Search, then tap 🔔",),
    },
    "alerts_list": {"he": ("ההתראות שלכם 🔔 (לחיצה מוחקת):",), "en": ("Your alerts 🔔 (tap to delete):",)},
    "alerts_empty": {
        "he": ("אין לכם התראות עדיין. אחרי חיפוש, לחצו 🔔 ״עדכן אותי״.",),
        "en": ('No alerts yet. After a search, tap 🔔 "Alert me".',),
    },
    "alert_deleted": {"he": ("מחקתי את ההתראה ״{q}״ 🗑",), "en": ('Deleted the alert "{q}" 🗑',)},
    "alert_new": {
        "he": ("🔔 חדש ל״{q}״:", "🔔 יש משהו חדש שמתאים ל״{q}״:"),
        "en": ('🔔 New for "{q}":', '🔔 Something new matches "{q}":'),
    },
    "stopped": {
        "he": ("מחקתי את כל מה ששמרתי עליכם, כולל ההתראות. אם תכתבו שוב, נתחיל מחדש 👋",),
        "en": ("I deleted everything I kept about you, alerts included. Write again any time to start over 👋",),
    },
    "lang_set": {"he": ("מעכשיו אדבר עברית 🇮🇱",), "en": ("I'll speak English from now on 🇬🇧",)},
    "slow_down": {"he": ("רגע, נשימה 😅 עוד דקה אמשיך.",), "en": ("Whoa, one moment 😅 I'll be back in a minute.",)},
    "error": {
        "he": ("אופס, משהו השתבש אצלי 🙏 נסו שוב בעוד רגע.",),
        "en": ("Oops, something went wrong on my side 🙏 Try again in a moment.",),
    },
    # ------------------------------------------------------------- phase 1b / 1c
    "reminder_set": {
        "he": ("⏰ אזכיר לכם {h} לפני ״{t}״", "⏰ סגור! תקבלו תזכורת {h} לפני ״{t}״"),
        "en": ("⏰ I'll remind you {h} before “{t}”", "⏰ Done! A reminder {h} before “{t}”"),
    },
    "reminder_off": {"he": ("ביטלתי את התזכורת ל״{t}״ 👌",), "en": ('Reminder for "{t}" cancelled 👌',)},
    "reminder_soon": {
        "he": ("״{t}״ מתחיל ממש בקרוב, אין מה להזכיר 😉 לחצו 🎟 לכרטיסים.",),
        "en": ('"{t}" starts very soon, nothing to remind 😉 Tap 🎟 for tickets.',),
    },
    "reminder_full": {
        "he": ("יש לכם כבר {n} תזכורות. מחקו אחת ב-/reminders ונסו שוב.",),
        "en": ("You already have {n} reminders. Delete one in /reminders and try again.",),
    },
    "reminder_now": {
        "he": ("⏰ תזכורת: ״{t}״ מתחיל בעוד {h}!", "⏰ רק מזכיר: ״{t}״ בעוד {h} 🎉"),
        "en": ('⏰ Reminder: "{t}" starts in {h}!', '⏰ Heads up: "{t}" is in {h} 🎉'),
    },
    "watch_set": {
        "he": ("👁 עוקב אחרי ״{t}״: אגיד לכם אם המחיר יורד, אם הכרטיסים אוזלים או חוזרים.",),
        "en": ("👁 Watching “{t}”: I'll tell you if the price drops, it sells out or tickets are back.",),
    },
    "watch_off": {"he": ("הפסקתי לעקוב אחרי ״{t}״ 👌",), "en": ('Stopped watching "{t}" 👌',)},
    "watch_full": {
        "he": ("אתם כבר עוקבים אחרי {n} אירועים. הסירו אחד ב-/reminders ונסו שוב.",),
        "en": ("You already watch {n} events. Remove one in /reminders and try again.",),
    },
    "watch_price_drop": {
        "he": ("📉 המחיר של ״{t}״ ירד: {was} ← {p}",), "en": ('📉 "{t}" got cheaper: {was} → {p}',),
    },
    "watch_sold_out": {"he": ("❌ ״{t}״ אזל.",), "en": ('❌ "{t}" sold out.',)},
    "watch_back": {
        "he": ("🎟 חזרו כרטיסים ל״{t}״! כדאי למהר.",), "en": ('🎟 Tickets for "{t}" are back! Be quick.',),
    },
    "mine_list": {
        "he": ("התזכורות ⏰ והמעקבים 👁 שלכם (לחיצה מוחקת):",),
        "en": ("Your reminders ⏰ and watches 👁 (tap to delete):",),
    },
    "mine_empty": {
        "he": ("אין תזכורות או מעקבים. מתחת לכל אירוע יש ⏰ ו-👁",),
        "en": ("No reminders or watches. Every event card has ⏰ and 👁",),
    },
    "mine_deleted": {"he": ("מחקתי 🗑",), "en": ("Deleted 🗑",)},
    "similar": {
        "he": ("🔁 דומים ל״{t}״:", "🔁 אם אהבתם את ״{t}״, אולי גם:"),
        "en": ('🔁 Like "{t}":', '🔁 If you like "{t}", try:'),
    },
    "similar_none": {"he": ("לא מצאתי משהו דומה ל״{t}״ כרגע 🤔",), "en": ('Nothing like "{t}" right now 🤔',)},
    "options": {"he": ("עוד אפשרויות ל״{t}״:",), "en": ('More for "{t}":',)},
    "calendar": {
        "he": ("📅 הקובץ מוסיף את ״{t}״ ליומן (אייפון, אאוטלוק וכל יומן). ל-Google יש כפתור 👇",),
        "en": ('📅 This file adds "{t}" to your calendar (iPhone, Outlook, any calendar). Google: the button 👇',),
    },
    "plan": {
        "he": ("👥 מי בא? שלחו לחברים את הלינק, כל אחד עונה בא / אולי / לא, ותראו מי מגיע ומי מסיע:\n{u}",),
        "en": ("👥 Who's coming? Send your friends this link: each answers yes / maybe / no, and you see who comes and who drives:\n{u}",),
    },
    "plan_share": {"he": ("בואו ל״{t}״? תגידו אם אתם באים 👇",), "en": ("Coming to “{t}”? Say if you're in 👇",)},
    "follow_ask": {
        "he": ("⭐ לעדכן אתכם בכל אירוע חדש של ״{q}״?",), "en": ('⭐ Tell you about every new event of "{q}"?',),
    },
    "digest_ask": {
        "he": ("📬 פעם בשבוע (ביום {d}) אשלח לכם את האירועים הכי שווים לסופ״ש. מה לשלוח?",),
        "en": ("📬 Once a week (on {d}) I'll send you the best events for the weekend. Which?",),
    },
    "digest_on": {
        "he": ("📬 נרשמתם לסיכום השבועי ({q}). ביטול: /digest",), "en": ("📬 You're in for the weekly picks ({q}). To stop: /digest",),
    },
    "digest_off": {"he": ("ביטלתי את הסיכום השבועי 👌",), "en": ("Weekly picks stopped 👌",)},
    "digest_new": {
        "he": ("📬 הסופ״ש שלכם: מה הכי שווה ({q})", "📬 נבחרת הסופ״ש ({q}):"),
        "en": ("📬 Your weekend: the best picks ({q})", "📬 This weekend's picks ({q}):"),
    },
    "channel_tonight": {"he": ("🌙 הערב: מה הכי שווה",), "en": ("🌙 Tonight's picks",)},
    "channel_weekend": {"he": ("🎉 הסופ״ש: מה הכי שווה",), "en": ("🎉 The weekend's picks",)},
    "gone": {"he": ("האירוע הזה כבר לא זמין 😕",), "en": ("That event is no longer available 😕",)},
    "only_text": {
        "he": ("אני מבין טקסט ומיקום 🙂 כתבו מה אתם מחפשים.",),
        "en": ("I understand text and locations 🙂 Write what you're looking for.",),
    },
}

LABELS: dict[str, dict[str, str]] = {
    "tickets": {"he": "🎟 כרטיסים", "en": "🎟 Tickets"},
    "details": {"he": "ℹ️ פרטים", "en": "ℹ️ Details"},
    "more": {"he": "עוד ⬅️", "en": "More ➡️"},
    "alert": {"he": "🔔 עדכן אותי", "en": "🔔 Alert me"},
    "yes": {"he": "✅ כן, עדכן אותי", "en": "✅ Yes, alert me"},
    "no": {"he": "ביטול", "en": "Cancel"},
    "near": {"he": "📍 קרוב אליי", "en": "📍 Near me"},
    "tonight": {"he": "🌙 הערב", "en": "🌙 Tonight"},
    "weekend": {"he": "🎉 בסופ״ש", "en": "🎉 This weekend"},
    "cheap": {"he": "💸 הכי זול", "en": "💸 Cheapest"},
    "free": {"he": "🆓 חינם", "en": "🆓 Free"},
    "my_alerts": {"he": "🔔 ההתראות שלי", "en": "🔔 My alerts"},
    "help": {"he": "❓ עזרה", "en": "❓ Help"},
    "free_entry": {"he": "🆓 כניסה חופשית", "en": "🆓 Free entry"},
    "from_price": {"he": "💸 החל מ-{p}", "en": "💸 From {p}"},
    "sold_out": {"he": "❌ אזל", "en": "❌ Sold out"},
    "discount": {"he": "🔥 יש הנחה", "en": "🔥 On sale"},
    "km": {"he": "📍 {km} ק״מ ממך", "en": "📍 {km} km away"},
    "remind": {"he": "⏰ תזכורת", "en": "⏰ Remind"},
    "watch": {"he": "👁 מעקב", "en": "👁 Watch"},
    "similar": {"he": "🔁 דומים", "en": "🔁 Similar"},
    "options": {"he": "➕ עוד", "en": "➕ More"},
    "ics": {"he": "📅 קובץ ליומן", "en": "📅 Calendar file"},
    "google_cal": {"he": "📅 Google Calendar", "en": "📅 Google Calendar"},
    "plan": {"he": "👥 מי בא?", "en": "👥 Who's coming?"},
    "share": {"he": "📤 שליחה לחברים", "en": "📤 Send to friends"},
    "follow": {"he": "⭐ {q}", "en": "⭐ {q}"},
    "follow_yes": {"he": "✅ כן, עדכן אותי", "en": "✅ Yes, tell me"},
    "digest_all": {"he": "📬 הכל", "en": "📬 Everything"},
    "digest_last": {"he": "📬 כמו ״{q}״", "en": '📬 Like "{q}"'},
    "digest_stop": {"he": "🔕 בלי סיכום", "en": "🔕 No digest"},
    "everything": {"he": "הכל", "en": "everything"},
    "hours": {"he": "{n} שעות", "en": "{n} hours"},
    "hour": {"he": "שעה", "en": "an hour"},
    "day": {"he": "יום", "en": "a day"},
    "days": {"he": "{n} ימים", "en": "{n} days"},
    "minutes": {"he": "{n} דקות", "en": "{n} minutes"},
    "my_events": {"he": "⏰ התזכורות שלי", "en": "⏰ My reminders"},
    "tonight_at": {"he": "הערב", "en": "Tonight"},
    "today_at": {"he": "היום", "en": "Today"},
    "tomorrow_at": {"he": "מחר", "en": "Tomorrow"},
}


def say(key: str, lang: str, **values: object) -> str:
    """One of the ways to say ``key``, filled in."""
    return random.choice(PHRASES[key][lang]).format(**values)


def label(key: str, lang: str, **values: object) -> str:
    return LABELS[key][lang].format(**values)


WEEKDAY_NAMES = {
    "he": ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}


def duration(hours: float, lang: str) -> str:
    """2 -> "2 שעות", 24 -> "יום", 0.5 -> "30 דקות", 71.1 -> "3 ימים": rounded as people say it."""
    if hours < 1:
        return label("minutes", lang, n=max(1, round(hours * 60)))
    if hours >= 22:
        days = round(hours / 24)
        return label("day", lang) if days == 1 else label("days", lang, n=days)
    n = round(hours)
    return label("hour", lang) if n == 1 else label("hours", lang, n=n)
