"""
Security Intelligence Monitor
==============================
Collects weather, news, and geopolitical data for 6 European tech facilities,
scores each using Claude AI, writes results to Supabase, and sends
email alerts when a facility reaches RED status (composite score >= 7).

Triggered every 4 hours by GitHub Actions. No server required.

Environment variables (set as GitHub Secrets):
  ANTHROPIC_API_KEY
  NEWSAPI_KEY
  SUPABASE_URL
  SUPABASE_SERVICE_KEY
  ALERT_EMAIL
  GMAIL_SENDER          (usually same as ALERT_EMAIL)
  GMAIL_APP_PASSWORD
  ACLED_EMAIL           (ACLED account email)
  ACLED_PASSWORD        (ACLED account password)
"""

import os
import json
import smtplib
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import anthropic
from supabase import create_client


# ─── Facilities ───────────────────────────────────────────────────────────────
# Update name/city/country/lat/lng to match your real locations.

FACILITIES = [
    {"name": "Warsaw R&D Lab",      "city": "Warsaw",    "country": "Poland",         "country_slug": "poland",         "type": "R&D Lab",     "lat": 52.2297, "lng": 21.0122},
    {"name": "Bucharest Office",    "city": "Bucharest", "country": "Romania",        "country_slug": "romania",        "type": "Office",      "lat": 44.4268, "lng": 26.1025},
    {"name": "Prague Tech Hub",     "city": "Prague",    "country": "Czech Republic", "country_slug": "czech-republic", "type": "Mixed",       "lat": 50.0755, "lng": 14.4378},
    {"name": "Belgrade Data Center","city": "Belgrade",  "country": "Serbia",         "country_slug": "serbia",         "type": "Data Center", "lat": 44.8176, "lng": 20.4633},
    {"name": "Tel Aviv Tech Hub",   "city": "Tel Aviv",  "country": "Israel",         "country_slug": "israel",         "type": "Mixed",       "lat": 32.0853, "lng": 34.7818, "min_geo_score": 9,
     "extra_unrest_keywords": ["missile","rocket","airstrike","bombing","explosion","siren","IDF","Hamas","Hezbollah","projectile","interception","Iron Dome"]},
]


# ─── Config ───────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
NEWSAPI_KEY        = os.environ.get("NEWSAPI_KEY", "")
SUPABASE_URL       = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
ALERT_EMAIL        = os.environ.get("ALERT_EMAIL", "")
GMAIL_SENDER       = os.environ.get("GMAIL_SENDER", ALERT_EMAIL)
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
ACLED_EMAIL        = os.environ.get("ACLED_EMAIL", "")
ACLED_PASSWORD     = os.environ.get("ACLED_PASSWORD", "")


# ─── Weather codes ────────────────────────────────────────────────────────────

WEATHER_CODE_DESCRIPTIONS = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Freezing fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    61: "Light rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Rain showers", 81: "Moderate showers", 82: "Violent showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}

def describe_weather_code(code):
    return WEATHER_CODE_DESCRIPTIONS.get(int(code), f"Weather code {code}")


# ─── ACLED ────────────────────────────────────────────────────────────────────

def get_acled_token():
    """Authenticate with ACLED OAuth2 and return an access token."""
    if not ACLED_EMAIL or not ACLED_PASSWORD:
        return None
    try:
        resp = requests.post(
            "https://acleddata.com/oauth/token",
            data={
                "username":   ACLED_EMAIL,
                "password":   ACLED_PASSWORD,
                "grant_type": "password",
                "client_id":  "acled",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if token:
            print("[✓] ACLED token obtained")
        return token
    except Exception as e:
        print(f"[!] ACLED auth error: {e}")
        return None


def fetch_acled(lat, lng, token, radius_km=50):
    """Fetch ACLED conflict/protest events within radius_km of a facility (last 30 days)."""
    if not token:
        return "ACLED not available."
    try:
        today           = datetime.now(timezone.utc).date()
        thirty_days_ago = today - timedelta(days=30)
        params = {
            "latitude":         lat,
            "longitude":        lng,
            "radius":           radius_km,
            "event_date":       f"{thirty_days_ago}|{today}",
            "event_date_where": "BETWEEN",
            "fields":           "event_date|event_type|sub_event_type|location|fatalities|notes",
            "limit":            10,
        }
        resp = requests.get(
            "https://api.acleddata.com/acled/read",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        events = resp.json().get("data", [])
        if not events:
            return f"No ACLED events within {radius_km}km in last 30 days."
        lines = []
        for e in events[:5]:
            fat = int(e.get("fatalities", 0) or 0)
            fat_str = f", {fat} fatalities" if fat > 0 else ""
            lines.append(
                f"{e.get('event_date','')} — {e.get('event_type','')} / "
                f"{e.get('sub_event_type','')} in {e.get('location','')}"
                f"{fat_str}: {str(e.get('notes',''))[:200]}"
            )
        return f"{len(events)} event(s) in last 30 days:\n  " + "\n  ".join(lines)
    except Exception as e:
        print(f"  [!] ACLED fetch error: {e}")
        return f"ACLED data unavailable: {e}"


# ─── Data collection ──────────────────────────────────────────────────────────

def fetch_weather(lat, lng):
    """Open-Meteo: current conditions + tomorrow's forecast. Free, no key."""
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lng}"
            f"&current=temperature_2m,weathercode,windspeed_10m,precipitation"
            f"&daily=weathercode,precipitation_sum,windspeed_10m_max,temperature_2m_max"
            f"&forecast_days=2&timezone=Europe%2FBerlin"
        )
        data    = requests.get(url, timeout=10).json()
        current = data.get("current", {})
        daily   = data.get("daily", {})

        def safe(key, i, default=0):
            v = daily.get(key, [])
            return v[i] if len(v) > i else default

        return {
            "weathercode":       current.get("weathercode", 0),
            "temperature":       current.get("temperature_2m", 0),
            "windspeed":         current.get("windspeed_10m", 0),
            "precipitation":     current.get("precipitation", 0),
            "tomorrow_code":     safe("weathercode", 1),
            "tomorrow_max_temp": safe("temperature_2m_max", 1),
            "tomorrow_wind":     safe("windspeed_10m_max", 1),
            "tomorrow_precip":   safe("precipitation_sum", 1),
        }
    except Exception as e:
        print(f"  [!] Weather error: {e}")
        return {k: 0 for k in ["weathercode","temperature","windspeed","precipitation",
                                "tomorrow_code","tomorrow_max_temp","tomorrow_wind","tomorrow_precip"]}


def fetch_news(city, keywords):
    """NewsAPI: search recent headlines by city + keywords. Free tier: 100 req/day."""
    if not NEWSAPI_KEY:
        return ["NewsAPI key not set"]
    try:
        params = {
            "q":        f'"{city}" AND ({" OR ".join(keywords)})',
            "language": "en",
            "pageSize": 5,
            "sortBy":   "publishedAt",
            "apiKey":   NEWSAPI_KEY,
        }
        articles = requests.get("https://newsapi.org/v2/everything", params=params, timeout=10).json().get("articles", [])
        headlines = [
            f"{a.get('title','No title')} [{a.get('source',{}).get('name','?')}, {a.get('publishedAt','')[:10]}]"
            for a in articles
        ]
        return headlines or ["No recent news found"]
    except Exception as e:
        print(f"  [!] NewsAPI error: {e}")
        return [f"News fetch failed: {e}"]


def fetch_fcdo(country_slug):
    """UK FCDO travel advisory RSS. Free, no key."""
    try:
        feed    = feedparser.parse(f"https://www.gov.uk/foreign-travel-advice/{country_slug}/rss.xml")
        entries = feed.entries[:2]
        if not entries:
            return "No FCDO advisory available."
        return " | ".join(f"{e.get('title','')}: {e.get('summary','')[:400]}" for e in entries)
    except Exception as e:
        print(f"  [!] FCDO error: {e}")
        return f"FCDO unavailable: {e}"


# ─── AI Scoring ───────────────────────────────────────────────────────────────

SCORING_PROMPT = """You are a security intelligence analyst. Assess the risk for this facility and return a JSON object only — no other text, no markdown.

FACILITY: {name} ({type}) in {city}, {country}

RAW DATA:
- Current weather: {weather_desc}, temp={temperature}°C, wind={windspeed}km/h, precip={precipitation}mm
- Tomorrow: {tomorrow_desc}, max={tomorrow_max_temp}°C, wind={tomorrow_wind}km/h, precip={tomorrow_precip}mm
- Civil unrest news (last 48h):
  {unrest_headlines}
- ACLED verified conflict/protest events (last 30 days, within 50km):
  {acled_data}
- Crime news (last 48h):
  {crime_headlines}
- UK FCDO advisory for {country}:
  {fcdo_text}

SCORING RUBRIC — score each 0–10:

WEATHER: 0–1 clear/no concern | 2–3 light rain/wind | 4–5 moderate/watch issued | 6–7 storm warning/flood watch | 8–9 major storm/blizzard | 10 catastrophic/facility at risk
UNREST:  0–1 none | 2–3 small/distant protest | 4–5 city-level strike or march | 6–7 within 2km or major transport disruption | 8–9 near facility, damage or police | 10 violent/riot/facility threatened. Weight ACLED verified events heavily — they are more reliable than news headlines.
CRIME:   0–1 none | 2–3 petty theft/vandalism | 4–5 burglary/assault nearby | 6–7 serious crime within 2km | 8–9 multiple serious incidents | 10 violent crime at facility
GEOPOLITICAL: 0–1 FCDO normal | 2–3 some risks | 4–5 high caution/instability | 6–7 essential travel only | 8–9 crisis/election violence/sanctions | 10 do not travel/armed conflict

COMPOSITE = (weather×0.10) + (unrest×0.30) + (crime×0.20) + (geopolitical×0.40)
COLOR: "Green" <4.0 | "Amber" 4.0–6.9 | "Red" >=7.0

Return ONLY this JSON — no explanation, no markdown fences:
{{
  "weather_score": <int 0-10>,
  "weather_description": "<1-2 sentences referencing actual data>",
  "unrest_score": <int 0-10>,
  "unrest_description": "<1-2 sentences referencing actual headlines>",
  "crime_score": <int 0-10>,
  "crime_description": "<1-2 sentences referencing actual data>",
  "geopolitical_score": <int 0-10>,
  "geopolitical_description": "<1-2 sentences referencing FCDO advisory>",
  "composite_score": <float 1 decimal>,
  "color": "<Green|Amber|Red>",
  "top_alert": "<most important alert, one sentence>",
  "recommended_action": "<one concrete action for today>"
}}"""


def score_with_claude(facility, weather, unrest_news, crime_news, fcdo_text, acled_data=""):
    """One Claude API call per facility → returns scored dict."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = SCORING_PROMPT.format(
        name=facility["name"], type=facility["type"],
        city=facility["city"], country=facility["country"],
        weather_desc=describe_weather_code(weather["weathercode"]),
        temperature=weather["temperature"], windspeed=weather["windspeed"],
        precipitation=weather["precipitation"],
        tomorrow_desc=describe_weather_code(weather["tomorrow_code"]),
        tomorrow_max_temp=weather["tomorrow_max_temp"],
        tomorrow_wind=weather["tomorrow_wind"], tomorrow_precip=weather["tomorrow_precip"],
        unrest_headlines="\n  ".join(unrest_news),
        acled_data=acled_data,
        crime_headlines="\n  ".join(crime_news),
        fcdo_text=fcdo_text[:800],
    )
    try:
        msg = client.messages.create(
            model="claude-opus-4-6", max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"  [!] Claude error: {e}")
        return {
            "weather_score": 0, "weather_description": f"Scoring failed: {e}",
            "unrest_score":  0, "unrest_description":  f"Scoring failed: {e}",
            "crime_score":   0, "crime_description":   f"Scoring failed: {e}",
            "geopolitical_score": 0, "geopolitical_description": f"Scoring failed: {e}",
            "composite_score": 0.0, "color": "Green",
            "top_alert": "Scoring error — manual review required.",
            "recommended_action": "Check GitHub Actions logs and re-run workflow.",
        }


# ─── Score override ───────────────────────────────────────────────────────────

def apply_score_floors(facility, result):
    """Apply minimum geopolitical score for known high-conflict locations,
    then recalculate the composite and color."""
    min_geo = facility.get("min_geo_score", 0)
    if result["geopolitical_score"] < min_geo:
        print(f"  [!] Geopolitical score {result['geopolitical_score']} below floor {min_geo} — overriding")
        result["geopolitical_score"] = min_geo
        result["geopolitical_description"] = (
            result["geopolitical_description"]
            + f" [Score raised to minimum {min_geo} due to known active conflict.]"
        )
    # Recalculate composite with the corrected scores
    composite = (
        result["weather_score"]      * 0.10
        + result["unrest_score"]     * 0.30
        + result["crime_score"]      * 0.20
        + result["geopolitical_score"] * 0.40
    )
    result["composite_score"] = round(composite, 1)
    result["color"] = "Red" if composite >= 7.0 else "Amber" if composite >= 4.0 else "Green"
    return result


# ─── Supabase ─────────────────────────────────────────────────────────────────

def write_to_supabase(supabase, facility, result):
    """Upsert facility row (insert or overwrite based on facility_name)."""
    data = {
        "last_updated":            datetime.now(timezone.utc).isoformat(),
        "facility_name":           facility["name"],
        "city":                    facility["city"],
        "country":                 facility["country"],
        "type":                    facility["type"],
        "lat":                     facility["lat"],
        "lng":                     facility["lng"],
        "weather_score":           result["weather_score"],
        "weather_description":     result["weather_description"],
        "unrest_score":            result["unrest_score"],
        "unrest_description":      result["unrest_description"],
        "crime_score":             result["crime_score"],
        "crime_description":       result["crime_description"],
        "geopolitical_score":      result["geopolitical_score"],
        "geopolitical_description":result["geopolitical_description"],
        "composite_score":         result["composite_score"],
        "color":                   result["color"],
        "top_alert":               result["top_alert"],
        "recommended_action":      result["recommended_action"],
    }
    supabase.table("facility_status").upsert(data, on_conflict="facility_name").execute()
    icon = {"Green": "🟢", "Amber": "🟡", "Red": "🔴"}.get(result["color"], "⚪")
    print(f"  [✓] {facility['name']}: {icon} {result['color']} ({result['composite_score']}/10)")


# ─── Alert email ──────────────────────────────────────────────────────────────

def send_alert_email(facility, result):
    """Send RED alert email via Gmail SMTP."""
    if not ALERT_EMAIL or not GMAIL_APP_PASSWORD:
        print("  [!] Email not configured — skipping")
        return

    def row(icon, label, score, desc):
        c = "#e53e3e" if score >= 7 else "#dd6b20" if score >= 4 else "#38a169"
        return f"""<tr>
          <td style="padding:8px;border:1px solid #333;">{icon} {label}</td>
          <td style="padding:8px;text-align:center;border:1px solid #333;font-weight:bold;color:{c};">{score}/10</td>
          <td style="padding:8px;border:1px solid #333;">{desc}</td>
        </tr>"""

    html = f"""<html><body style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto;background:#0d1117;color:#c9d1d9;">
    <div style="background:#e53e3e;color:white;padding:16px 20px;border-radius:6px 6px 0 0;">
      <h2 style="margin:0;">🔴 Security Alert — {facility['name']}</h2>
    </div>
    <div style="border:1px solid #30363d;border-top:none;padding:20px;background:#161b22;border-radius:0 0 6px 6px;">
      <p><strong>Location:</strong> {facility['city']}, {facility['country']} ({facility['type']})<br>
      <strong>Composite Risk Score:</strong>
      <span style="font-size:1.3em;font-weight:bold;color:#e53e3e;">{result['composite_score']}/10</span></p>
      <table style="width:100%;border-collapse:collapse;margin-top:16px;">
        <tr style="background:#21262d;"><th style="padding:8px;border:1px solid #333;text-align:left;">Category</th>
          <th style="padding:8px;border:1px solid #333;width:60px;">Score</th>
          <th style="padding:8px;border:1px solid #333;text-align:left;">Assessment</th></tr>
        {row("🌦","Weather",      result['weather_score'],      result['weather_description'])}
        {row("✊","Civil Unrest", result['unrest_score'],       result['unrest_description'])}
        {row("🔒","Crime",        result['crime_score'],        result['crime_description'])}
        {row("🌍","Geopolitical", result['geopolitical_score'], result['geopolitical_description'])}
      </table>
      <div style="background:#2d1b1b;border-left:4px solid #e53e3e;padding:12px;margin-top:16px;">
        <strong>⚠️ Top Alert:</strong><br>{result['top_alert']}
      </div>
      <div style="background:#1b2d1b;border-left:4px solid #38a169;padding:12px;margin-top:12px;">
        <strong>✅ Action:</strong><br>{result['recommended_action']}
      </div>
      <p style="color:#8b949e;font-size:11px;margin-top:20px;">
        Security Intelligence Monitor · {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
      </p>
    </div></body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🔴 SECURITY ALERT — {facility['name']} — Score {result['composite_score']}/10"
        msg["From"]    = GMAIL_SENDER
        msg["To"]      = ALERT_EMAIL
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
            s.sendmail(GMAIL_SENDER, ALERT_EMAIL, msg.as_string())
        print(f"  [✓] Alert email sent to {ALERT_EMAIL}")
    except Exception as e:
        print(f"  [!] Email failed: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*60}")
    print(f"Security Intelligence Run — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}")

    sb          = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    acled_token = get_acled_token()

    for facility in FACILITIES:
        print(f"\n▶  {facility['name']} ({facility['city']})")
        weather     = fetch_weather(facility["lat"], facility["lng"])
        unrest_keywords = ["protest","strike","riot","demonstration"] + facility.get("extra_unrest_keywords", [])
        unrest_news = fetch_news(facility["city"], unrest_keywords)
        crime_news  = fetch_news(facility["city"], ["crime","robbery","assault","attack","shooting"])
        fcdo_text   = fetch_fcdo(facility["country_slug"])
        acled_data  = fetch_acled(facility["lat"], facility["lng"], acled_token)
        result      = score_with_claude(facility, weather, unrest_news, crime_news, fcdo_text, acled_data)
        result      = apply_score_floors(facility, result)
        write_to_supabase(sb, facility, result)
        if result["composite_score"] >= 7.0:
            print(f"  🔴 RED — sending alert email")
            send_alert_email(facility, result)

    print(f"\n{'='*60}\nRun complete.\n{'='*60}\n")


if __name__ == "__main__":
    run()
