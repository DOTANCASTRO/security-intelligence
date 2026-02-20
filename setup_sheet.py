"""
setup_sheet.py
==============
Run this ONCE to create the Google Sheet structure.
It writes headers to row 1 and placeholder rows 2–7 (one per facility).

Usage:
  python setup_sheet.py

Requires GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID to be set
as environment variables (or Replit Secrets).
"""

import os
import json
import gspread
from google.oauth2.service_account import Credentials

GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
GOOGLE_SHEET_ID             = os.environ.get("GOOGLE_SHEET_ID", "")
SHEET_NAME = "Security Intelligence"

HEADERS = [
    "Last Updated", "Facility Name", "City", "Country", "Type", "Lat", "Lng",
    "Weather Score", "Weather Description",
    "Unrest Score",  "Unrest Description",
    "Crime Score",   "Crime Description",
    "Geopolitical Score", "Geopolitical Description",
    "Composite Score", "Color", "Top Alert", "Recommended Action",
]

FACILITIES = [
    ["—", "Amsterdam HQ",       "Amsterdam", "Netherlands",    "Office",      52.3676,  4.9041,  0, "Not yet scored", 0, "Not yet scored", 0, "Not yet scored", 0, "Not yet scored", 0.0, "Green", "—", "—"],
    ["—", "Berlin Tech Center",  "Berlin",    "Germany",        "Data Center", 52.5200, 13.4050,  0, "Not yet scored", 0, "Not yet scored", 0, "Not yet scored", 0, "Not yet scored", 0.0, "Green", "—", "—"],
    ["—", "Warsaw R&D Lab",      "Warsaw",    "Poland",         "R&D Lab",     52.2297, 21.0122,  0, "Not yet scored", 0, "Not yet scored", 0, "Not yet scored", 0, "Not yet scored", 0.0, "Green", "—", "—"],
    ["—", "Paris Office",        "Paris",     "France",         "Office",      48.8566,  2.3522,  0, "Not yet scored", 0, "Not yet scored", 0, "Not yet scored", 0, "Not yet scored", 0.0, "Green", "—", "—"],
    ["—", "Madrid Data Center",  "Madrid",    "Spain",          "Data Center", 40.4168, -3.7038,  0, "Not yet scored", 0, "Not yet scored", 0, "Not yet scored", 0, "Not yet scored", 0.0, "Green", "—", "—"],
    ["—", "Prague Tech Hub",     "Prague",    "Czech Republic", "Mixed",       50.0755, 14.4378,  0, "Not yet scored", 0, "Not yet scored", 0, "Not yet scored", 0, "Not yet scored", 0.0, "Green", "—", "—"],
]


def main():
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEET_ID:
        print("ERROR: Set GOOGLE_SERVICE_ACCOUNT_JSON and GOOGLE_SHEET_ID first.")
        return

    print("Connecting to Google Sheets...")
    creds_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds  = Credentials.from_service_account_info(creds_info, scopes=scopes)
    client = gspread.authorize(creds)

    try:
        sheet = client.open_by_key(GOOGLE_SHEET_ID).worksheet(SHEET_NAME)
        print(f"Found existing worksheet: '{SHEET_NAME}'")
    except gspread.WorksheetNotFound:
        spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
        sheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=10, cols=20)
        print(f"Created new worksheet: '{SHEET_NAME}'")

    # Write headers + all 6 facility rows in one API call
    all_rows = [HEADERS] + FACILITIES
    sheet.update("A1", all_rows)

    # Bold the header row
    sheet.format("A1:S1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.2, "green": 0.2, "blue": 0.2},
    })

    print(f"\n✓ Sheet set up successfully.")
    print(f"  Rows written: 1 header + {len(FACILITIES)} facilities")
    print(f"  Open your sheet and confirm rows 1–7 look correct.")
    print(f"  Then run main.py to populate with live data.")


if __name__ == "__main__":
    main()
