# ארכיטקטורה

```
 כל צליל במחשב (זום, דפדפן, נגן)
          │  WASAPI loopback ("מה שאת שומעת")
          ▼
 LoopbackCapture      audio.py       16kHz mono, פריימים של 30ms
          │  queue
          ▼
 PhraseSegmenter      audio.py       חותך לפי עוצמה ושקט → משפט
          │  queue (עד 8; כשמפגרים נזרק הישן כדי להישאר חי)
          ▼
 Recognizer           engines.py     faster-whisper base.en, int8, מעבד
          ▼
 Translator           engines.py     CTranslate2 + מודל Argos en→he
          ▼
 SubtitleWindow       ui.py          Tk, תמיד למעלה, מימין לשמאל
```

כל שלב רץ ב-thread נפרד (`app.py → Pipeline`). כך הקלטת הקול לא נעצרת בזמן שהזיהוי עובד.

## קבצים

| קובץ | תפקיד |
|---|---|
| `livetranslator/app.py` | נקודת כניסה, `Pipeline`, מצב `--file` לבדיקה |
| `livetranslator/audio.py` | הקלטת loopback וחיתוך למשפטים |
| `livetranslator/engines.py` | זיהוי דיבור ותרגום |
| `livetranslator/spm_tokenizer.py` | קריאת קובץ SentencePiece בלי הספרייה `sentencepiece` (ראו למטה) |
| `livetranslator/ui.py` | חלון הכתוביות |
| `livetranslator/config.py` | הגדרות ברירת מחדל וקריאת `config.json` |
| `livetranslator/download_models.py` | הורדה חד-פעמית של המודלים |

## החלטות ולמה

**Whisper `base.en` ולא `small.en`.** ‏base זיהה את קובץ הבדיקה בלי שגיאות, תוך שבריר מהזמן ש-small צריך (0.8 שניות לעומת 5 שניות על 13 שניות של אודיו). ‏small זמין בהגדרות.

**4 ליבות ולא 8.** במעבד i5-12450H יש ליבות חסכוניות (E-cores). ב-8 ליבות הזיהוי היה איטי יותר ופחות יציב.

**מודל תרגום ייעודי ולא מודל שפה.** ‏Argos en→he הוא 133MB ומתרגם משפט ב-0.05 שנייה. מודל שפה מקומי (למשל gpt-oss-20b) צריך כ-16GB זיכרון ואיטי מדי לזמן אמת בלי כרטיס מסך.

**`spm_tokenizer.py`.** ‏Smart App Control של Windows חוסם במחשב הזה את ה-DLL של החבילה `sentencepiece`, כי הוא לא חתום. במקום לעקוף את מדיניות האבטחה, הקוד קורא את קובץ ה-`.model` (פורמט protobuf) ובונה ממנו tokenizer של הספרייה `tokenizers` של Hugging Face, שמותרת לטעינה. התוצאה זהה לחבילה המקורית.

**בלי `av` לקריאת קבצים.** הגרסה הנוכחית של PyAV לא תואמת ל-faster-whisper 1.2.1. האודיו מועבר כ-numpy ישירות מהזיכרון, ובמצב `--file` הקובץ נקרא עם `wave`.

**חלון Tk.** ‏Tk לא תומך בכיווניות (bidi), ולכן Windows מצייר כל תווית משמאל לימין. כדי שהעברית תוצג נכון:
- הכתובית עטופה בתווי RLM (`U+200F`), כדי שסימני פיסוק כמו `?` יופיעו בצד הנכון.
- בשורת הסטטוס עברית ואנגלית נמצאות בתוויות נפרדות.

**התאמה ל-DPI.** התוכנה מגדירה את עצמה כ-DPI aware, כדי שמיקום החלון יהיה נכון כשהגדלת התצוגה ב-Windows היא 125%.

**סוף משפט כשהשמע נעצר.** כשאין שום צליל, WASAPI loopback לא שולח פריימים בכלל. לכן `flush_if_stalled` סוגר משפט פתוח אחרי חצי שנייה בלי פריימים, למשל כשעוצרים סרטון באמצע משפט.

**סינון "הזיות" של Whisper.** בשקט או במוזיקה Whisper לפעמים "שומע" משפטים כמו "Thank you." שלא נאמרו. הם מסוננים לפי `no_speech_prob` ולפי רשימה קבועה.
