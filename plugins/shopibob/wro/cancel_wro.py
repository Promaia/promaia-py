import os
import sys
import requests
import json
import argparse

SHIPBOB_API_BASE = "https://api.shipbob.com"

def cancel_wro(api_key, wro_id):
    """
    Cancels a Warehouse Receiving Order in ShipBob.
    """
    try:
        print(f"Cancelling WRO {wro_id}...")
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # POST to /cancel endpoint with empty body
        response = requests.post(
            f"{SHIPBOB_API_BASE}/2.0/receiving/{wro_id}/cancel",
            headers=headers,
            data=json.dumps({})
        )
        
        response.raise_for_status()
        result = response.json()
        
        if result.get('status') == 'Cancelled':
            print(f"\n✅ WRO {wro_id} cancelled successfully!")
            print(f"   PO Number: {result.get('purchase_order_number')}")
            print(f"   Status: {result.get('status')}")
        else:
            print(f"\n⚠️  WRO {wro_id} response received but status is: {result.get('status')}")
            
    except requests.exceptions.HTTPError as e:
        print(f"\n❌ Error cancelling WRO: {e.response.status_code}")
        print("   Response from ShipBob:")
        try:
            print(json.dumps(e.response.json(), indent=2))
        except json.JSONDecodeError:
            print(e.response.text)
    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cancel a ShipBob Warehouse Receiving Order (WRO).")
    
    parser.add_argument("--wro-id", type=int, required=True, help="The WRO ID to cancel")
    parser.add_argument("--region", type=str, required=True, help="The region (e.g., 'US', 'AU', 'CA'). Determines which API key to use.")
    
    args = parser.parse_args()
    
    # Get region-specific API key
    region_upper = args.region.upper()
    api_key_env_var = f"SHIPBOB_API_KEY_{region_upper}"
    api_key = os.getenv(api_key_env_var)
    if not api_key:
        raise ValueError(f"{api_key_env_var} environment variable not set.")
    
    cancel_wro(api_key, args.wro_id)


