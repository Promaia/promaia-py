import os
import requests
import json

# This script fetches and lists all available fulfillment centers for the given API key.

SHIPBOB_API_BASE = "https://api.shipbob.com"
API_KEY = os.getenv("SHIPBOB_API_KEY")

def get_available_centers():
    """
    Fetches all available fulfillment centers from the ShipBob API.
    """
    if not API_KEY:
        print("❌ Error: SHIPBOB_API_KEY environment variable not set.")
        return

    print("Fetching available fulfillment centers...")
    headers = {"Authorization": f"Bearer {API_KEY}"}
    
    try:
        response = requests.get(f"{SHIPBOB_API_BASE}/1.0/fulfillmentCenter", headers=headers)
        response.raise_for_status()
        
        centers = response.json()
        
        print("\n✅ Available Fulfillment Centers:")
        print("---------------------------------")
        for center in centers:
            # Reformatting the name for clarity if it contains the location in parentheses
            name = center.get('name', 'N/A')
            if '(' in name and ')' in name:
                 # e.g., "Sydney (NSW)" -> "Sydney"
                 name = name.split('(')[0].strip()

            print(f"Name: {name}, ID: {center.get('id', 'N/A')}")
        print("---------------------------------")

    except requests.exceptions.HTTPError as e:
        print(f"\n❌ Error fetching centers: {e.response.status_code}")
        print(f"   Response: {e.response.text}")
    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")

if __name__ == "__main__":
    get_available_centers() 