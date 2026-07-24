import os
import time
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "data"

BASE_URL = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc"

LOCATIONS = [
    ("Plymouth",   50.373, -4.143),
    ("Manchester", 53.483, -2.242),
    ("Glasgow",    55.829, -4.276),
    ("Inverness",  57.479, -4.222),
]

PV_SIZES = [1, 2, 3, 4, 5, 6]

session = requests.Session()

for site_name, lat, lon in LOCATIONS:

    for peakpower in PV_SIZES:

        params = {
            "lat": lat,
            "lon": lon,

            "startyear": 2023,
            "endyear": 2023,

            # PV system settings
            "pvcalculation": 1,
            "peakpower": peakpower,
            "pvtechchoice": "crystSi",
            "loss": 14,

            # Fixed system
            "trackingtype": 0,
            "angle": 35,
            "aspect": 0,

            # Building-integrated mounting
            "mountingplace": "building",

            # Radiation database
            "raddatabase": "PVGIS-SARAH3",

            # CSV output
            "outputformat": "csv",

            # Terrain horizon
            "usehorizon": 1,
        }

        filename = (
            f"{site_name}_{peakpower}kWp_2023.csv"
            .replace(" ", "_")
        )

        filepath = OUTPUT_DIR / filename

        try:
            print(f"Downloading {filename}")

            response = session.get(
                BASE_URL,
                params=params,
                timeout=120
            )

            response.raise_for_status()

            with open(filepath, "wb") as f:
                f.write(response.content)

            print(f"Saved: {filepath}")

            # Be polite to the PVGIS servers
            time.sleep(0.5)

        except requests.exceptions.RequestException as e:
            print(
                f"ERROR: {site_name}, "
                f"{peakpower} kWp -> {e}"
            )
            response = getattr(e, "response", None)
            if response is not None and response.text:
                print(f"PVGIS response: {response.text.strip()}")

print("\nFinished.")
print(f"Files saved to: {OUTPUT_DIR.resolve()}")