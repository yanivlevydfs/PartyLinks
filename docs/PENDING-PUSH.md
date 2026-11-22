# PartyLinks: רשימת משימות

עודכן: 22.11.2026

## ✅ בוצע

- master תוקן: `config/settings.yaml` נטען שוב (`feef4a9`), נדחף ל-master ב-22.11 (`602116d`).
- 9 קופות Smarticket חדשות ב-master: mvhr, pastoral, migvanim, matnastc, kikar-bialik, pnay, betgabriel, mofa, docaviv.

## 1. לבדוק בפרודקשן (אחרי הדיפלוי)

- [ ] הדיפלוי עבר.
- [ ] **mugics** (`sources.mugics`): נכנס ב-18.11 כשהדיפלוי היה שבור. לוודא שהריצה הצליחה ושההופעות מופיעות באתר עם תאריך, מקום, פין ולינק.
- [ ] 9 קופות Smarticket החדשות: כל אחת מחזירה אירועים. קופה ריקה = להסיר.
- [ ] **cinematheque_tlv**: להסיר את Soundtrack TLV 2026 מ-`festivals` אחרי 28.11.

## 2. מקורות חדשים לבנות

סימון "כבר נשלח" = לינק שהמשתמש שלח. כל השאר מהמחקר של 22.11.
לא נפתחו (רשת חסומה בסשן), אז דרך הקריאה היא השערה עד שבודקים.

| # | עדיפות | אתר | מה זה | הערה |
|---|---|---|---|---|
| 1 | גבוהה | ticket4u.co.il | שפע הפקות, קופה על Eventim | כמו `barak`: לשכפל את הקורא שלו. הכי זול |
| 2 | גבוהה | parties247.co.il | מדריך מסיבות ארצי | עמודי ערים וחגים |
| 3 | גבוהה | basalon.co.il | הרצאות, סטנדאפ, סדנאות | לסנן "בבית הלקוח" |
| 4 | גבוהה | havanaclub.co.il (כבר נשלח) | Havana Music Club, ת"א | היום רק פין במפה. ערבי סלסה |
| 5 | גבוהה | newacropolis.org.il (כבר נשלח) | אקרופוליס החדשה: הרצאות, קורס פילוסופיה | אתר Wix (`/event-details/`). לסנן מפגשי קורס |
| 6 | בינונית | hamesila.org.il (כבר נשלח) | המסילה, הרצאות | יש `/events/` |
| 7 | בינונית | jerusalemarts.co.il (כבר נשלח) | Jerusalem Arts | |
| 8 | בינונית | arthurmurray.co.il (כבר נשלח) | Arthur Murray, ערבי ריקוד (The Salon) | |
| 9 | בינונית | online-reservations.yadvashem.org (כבר נשלח) | הזמנות יד ושם | פעילויות שלא דרך Smarticket `yadvashem` |
| 10 | בינונית | standupfactory.co.il | מועדון סטנדאפ, ת"א | |
| 11 | בינונית | tarbut.events | אתר תרבות עם עמודי מקומות | לבדוק תוספת מעבר ל-Tickchak |
| 12 | בינונית | glatticket.co.il | כרטוס למגזר החרדי/דתי | קהל שאין לנו |
| 13 | בינונית | ticketsi.co.il | מגזין + כרטיסים | כנראה כפילויות |
| 14 | לבדוק | בית הגפן חיפה | פסטיבל "חג החגים" 10–20.12.2026, חינם | יש רק פין |
| 15 | לבדוק | bimot.co.il | מפיק וקופה | לא ברור אם פעיל |
| 16 | נמוכה | cluberim.co.il | מדריך מסיבות לפי ז'אנר | אולי לא מתעדכן |
| 17 | נמוכה | tickster.co.il | מאגד | ברובו כפילויות |
| 18 | נמוכה | israelshow.co.il, ishow.co.il, kzat-tarbut.com, jeroozevents.co.il | מאגדים | נראים לא מעודכנים |
| 19 | נמוכה | salsavida.com, where-to-dance-salsa.com | סלסה/בצ'אטה שבועי | אירועים חוזרים |
| 20 | נמוכה | kolsadna.co.il | השוואת סדנאות | רובן לפי הזמנה |

## 3. עוד קופות Smarticket

חיפוש הרשת מוצה (~50 חיפושים, הכל כבר ברשימה; יש 220 מתוך "350+").
להריץ מהמחשב ולהעלות את הקובץ, ואני אשווה מול הקונפיג:

```bash
curl "https://web.archive.org/cdx/search/cdx?url=*.smarticket.co.il&fl=original&collapse=urlkey&output=txt" | sed -E 's#https?://([^/:]+).*#\1#' | sort -u > smarticket-hosts.txt
```

## 4. הרשאות רשת לאשר (סשן ענן)

כדי לבנות את סעיף 2 בסשן ענן צריך לאשר גישה לאתרים האלה (או לעבוד מהמחשב, שם הרשת פתוחה):

```
www.ticket4u.co.il  ticket4u.co.il
www.parties247.co.il  parties247.co.il
basalon.co.il  www.basalon.co.il
www.havanaclub.co.il  havanaclub.co.il
www.newacropolis.org.il  newacropolis.org.il
hamesila.org.il  www.hamesila.org.il
jerusalemarts.co.il  www.jerusalemarts.co.il
www.arthurmurray.co.il  arthurmurray.co.il
online-reservations.yadvashem.org
www.standupfactory.co.il  standupfactory.co.il
www.tarbut.events  tarbut.events
home.glatticket.co.il  glatticket.co.il
www.ticketsi.co.il  ticketsi.co.il
web.archive.org
```

## 5. כבר נסרק, לא צריך כלום

ksaba (כפר סבא), yotzim, cinema.co.il / Soundtrack TLV, tcj (בית מזיא), yadvashem (Smarticket), mugics,
kupatbravo, leaan, tickchak, mevalim, muzi, hypd, makore, plusparty, airdrop, zygo, popit, eventim, zappa,
kupat (קופת ת"א), comedybar.

## 6. לא ברור

- לינק פייסבוק `facebook.com/share/1FCNdDWfNW`: לא נפתח. צריך שם אירוע או מקום.

## בדיקה לפני כל push

```bash
python -c "import yaml; yaml.safe_load(open('config/settings.yaml'))"
```
