"""
Fundamental Analysis Agent — Gemini 2.5 Flash.
Only blocks trades for scheduled high-impact events within 60 minutes.
General geopolitical uncertainty does NOT block a trade.
"""
import os
import json
import requests
from datetime import datetime, timezone
from google import genai
from google.genai import types
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

GOOGLE_KEY = os.getenv("GEMINI_API_KEY")
GROQ_KEY   = os.getenv("GROQ_API_KEY")
client     = genai.Client(api_key=GOOGLE_KEY) if GOOGLE_KEY else None

PAIR_CURRENCIES = {
    "EURUSD": ("EUR", "USD"),
    "GBPUSD": ("GBP", "USD"),
    "USDJPY": ("USD", "JPY"),
    "GBPJPY": ("GBP", "JPY"),
    "AUDUSD": ("AUD", "USD"),
    "XAUUSD": ("XAU", "USD"),
}

def analyse(pair: str) -> dict:
    base, quote = PAIR_CURRENCIES.get(pair, ("", ""))
    calendar    = _get_economic_calendar(base, quote)
    now_utc     = datetime.now(timezone.utc)

    # Check for imminent high-impact events (within 60 minutes)
    imminent = [e for e in calendar
                if e.get("impact") == "High"
                and -15 <= e.get("mins_away", 999) <= 60]

    if imminent:
        event = imminent[0]
        return {
            "pair":                   pair,
            "fundamental_bias":       "neutral",
            "high_impact_news_soon":  True,
            "safe_to_trade":          False,
            "avoid_reason":           (
                f"{event['title']} ({event['currency']}) "
                f"in {event['mins_away']} minutes"
            ),
            "fundamental_score":      40,
            "calendar":               calendar,
            "summary":                f"Avoid — scheduled event imminent"
        }

    # No imminent events — ask AI for bias only (not for blocking decision)
    prompt = f"""You are a forex fundamental analyst.

Pair: {pair} | Base: {base} | Quote: {quote}
Time: {now_utc.strftime('%Y-%m-%d %H:%M UTC')}

Upcoming scheduled events (next 24h):
{json.dumps(calendar, indent=2) if calendar else 'None found'}

IMPORTANT RULES:
- Only set safe_to_trade=false if there is a SCHEDULED high-impact event within 60 minutes
- General geopolitical news, wars, or uncertainty does NOT block a trade
- Ongoing conflicts are already priced in by the market
- Give a fundamental bias based on interest rate differentials and recent data

Respond ONLY with valid JSON:
{{
  "fundamental_bias": "bullish_base / bearish_base / neutral",
  "high_impact_news_soon": false,
  "safe_to_trade": true,
  "avoid_reason": null,
  "currency_strength": {{
    "{base}": "strong/neutral/weak",
    "{quote}": "strong/neutral/weak"
  }},
  "fundamental_score": <40-70>,
  "summary": "One sentence on fundamental backdrop only"
}}"""

    try:
        if False:  # Gemini daily limit hit — Groq only
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1
                )
            )
            result = json.loads(response.text)
        else:
            result = _groq_fallback(prompt)

        result["pair"]     = pair
        result["calendar"] = calendar

        # Safety override — AI cannot block if no imminent events found
        if not imminent:
            result["safe_to_trade"] = True
            result["avoid_reason"]  = None

        return result

    except Exception as e:
        print(f"[fundamental] Error {pair}: {e}")
        return {
            "pair":              pair,
            "fundamental_bias":  "neutral",
            "safe_to_trade":     True,
            "fundamental_score": 50,
            "high_impact_news_soon": False,
            "calendar":          calendar
        }

def _get_economic_calendar(base: str, quote: str) -> list:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        urls = [
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json",
        ]
        data = None
        for url in urls:
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 200 and r.text.strip():
                    data = r.json()
                    break
            except:
                continue

        if not data:
            return []

        now      = datetime.now(timezone.utc)
        relevant = []
        for event in data:
            currency = event.get("country", "")
            impact   = event.get("impact", "")
            if currency not in [base, quote]:
                continue
            if impact not in ["High", "Medium"]:
                continue
            try:
                date_str   = event.get("date", "")
                event_time = datetime.fromisoformat(
                    date_str.replace("Z", "+00:00")
                )
                mins_away  = int((event_time - now).total_seconds() / 60)
                if -60 <= mins_away <= 1440:
                    relevant.append({
                        "title":     event.get("title", ""),
                        "currency":  currency,
                        "impact":    impact,
                        "mins_away": mins_away,
                    })
            except:
                continue
        return sorted(relevant, key=lambda x: abs(x["mins_away"]))[:5]

    except Exception as e:
        return []

def _groq_fallback(prompt: str) -> dict:
    try:
        g = Groq(api_key=GROQ_KEY)
        r = g.chat.completions.create(
            messages=[
                {"role": "system", "content": "Respond only in JSON."},
                {"role": "user",   "content": prompt}
            ],
            model="llama-3.3-70b-versatile",
            response_format={"type": "json_object"},
            temperature=0.1
        )
        return json.loads(r.choices[0].message.content)
    except:
        return {
            "fundamental_bias":  "neutral",
            "safe_to_trade":     True,
            "fundamental_score": 50,
            "high_impact_news_soon": False
        }

if __name__ == "__main__":
    print("Testing Fundamental Agent on EURUSD...")
    result = analyse("EURUSD")
    print(json.dumps(result, indent=2, default=str))
