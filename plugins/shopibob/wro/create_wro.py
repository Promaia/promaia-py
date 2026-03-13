import os
import argparse
import json
import requests
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# NOTE: This script requires the 'requests' and 'python-dotenv' libraries.
# Please install them if you haven't already: pip install requests python-dotenv

# --- Configuration (from WRO_Automation_Guide.md) ---

SHIPBOB_API_BASE = "https://api.shipbob.com"

# A mapping of human-readable fulfillment center names to their ShipBob IDs, nested by region.
FULFILLMENT_CENTERS = {
    "US": {
        "US": 156,  # Default to Ontario
        "Ontario": 156,
        "Ontario 6": 156, # Alias
        "Moreno Valley": 100,
        "Commerce": 111,
        "US West LTS 1": 111, # Alias
        "Fontana": 12
    },
    "AU": {
        "Sydney": 227,
        "Sydney 3": 227 # Alias
    },
    "UK": {
        "Manchester": 32
    },
    "CA": {
        "Toronto": 35,
        "Brampton": 48,
        "Brampton (Ontario)": 48
    }
}


def get_inventory_map(api_key, skus):
    """
    Fetches the inventory ID for each given SKU from the ShipBob API by querying them one by one.
    """
    print("Fetching inventory IDs for SKUs...")
    headers = {"Authorization": f"Bearer {api_key}"}
    inventory_map = {}

    for sku in skus:
        print(f"  - Fetching info for SKU: {sku}")
        # The /inventory endpoint can be queried with a single SKU.
        response = requests.get(f"{SHIPBOB_API_BASE}/2.0/inventory", headers=headers, params={'sku': sku})
        
        if response.status_code == 404:
            raise ValueError(f"SKU '{sku}' not found in ShipBob inventory.")
        response.raise_for_status()
        
        response_data = response.json()
        
        # The inventory item is inside the 'items' list in the response
        if 'items' in response_data and len(response_data['items']) > 0:
            item = response_data['items'][0]
            if 'sku' in item and 'inventory_id' in item:
                inventory_map[item['sku']] = item['inventory_id']
            else:
                raise ValueError(f"Inventory item for SKU '{sku}' is missing 'sku' or 'inventory_id' keys.")
        else:
            raise ValueError(f"No 'items' found in the API response for SKU '{sku}'.")

    print("Successfully mapped all SKUs to inventory IDs.")
    return inventory_map


def calculate_pallets(po_data, pallet_limits):
    """
    Calculates the distribution of items onto pallets based on defined limits.
    Returns a list of all pallets, with contents of each.
    
    Algorithm:
    1. Create full pallets for each SKU (up to pallet limit)
    2. Intelligently pack remainders into mixed pallets using bin-packing approach
    3. Try to minimize total pallet count by filling pallets as much as possible
    """
    print("Calculating pallet distribution...")
    full_pallets = []
    remainders = []

    # 1. Create full pallets and identify remainders
    for sku, total_quantity in po_data.items():
        if sku not in pallet_limits:
            raise ValueError(f"SKU '{sku}' not found in pallet_limits definition.")
        
        limit = pallet_limits[sku]
        if limit <= 0:
            print(f"Warning: Pallet limit for SKU '{sku}' is zero or less. Skipping.")
            continue
            
        num_full_pallets = total_quantity // limit
        remainder_qty = total_quantity % limit
        
        for _ in range(num_full_pallets):
            full_pallets.append({'contents': {sku: limit}})
            
        if remainder_qty > 0:
            remainders.append({'sku': sku, 'quantity': remainder_qty})
    
    print(f"Created {len(full_pallets)} full pallets")
    if remainders:
        print(f"Remainders to pack: {', '.join([f'{r['sku']}: {r['quantity']}' for r in remainders])}")
            
    # 2. Intelligently consolidate remainders into mixed pallets
    # Use first-fit-decreasing bin packing algorithm
    consolidated_pallets = []
    if remainders:
        max_limit = max(pallet_limits.values())
        
        # Sort remainders by quantity (largest first) for better packing
        remainders_sorted = sorted(remainders, key=lambda x: x['quantity'], reverse=True)
        
        # Track available space in each mixed pallet
        mixed_pallets = []  # List of {'contents': {}, 'remaining_space': int}
        
        for rem in remainders_sorted:
            sku = rem['sku']
            qty_to_pack = rem['quantity']
            
            # Try to fit this SKU into existing mixed pallets
            while qty_to_pack > 0:
                placed = False
                
                # Try to add to an existing pallet
                for pallet in mixed_pallets:
                    if pallet['remaining_space'] >= qty_to_pack:
                        # Fits entirely in this pallet
                        pallet['contents'][sku] = pallet['contents'].get(sku, 0) + qty_to_pack
                        pallet['remaining_space'] -= qty_to_pack
                        qty_to_pack = 0
                        placed = True
                        break
                
                if not placed:
                    # Check if we can split across pallets or need a new one
                    best_fit = None
                    best_fit_space = 0
                    
                    # Find the pallet with the most space that can fit at least some of this SKU
                    for pallet in mixed_pallets:
                        if pallet['remaining_space'] > 0 and pallet['remaining_space'] > best_fit_space:
                            best_fit = pallet
                            best_fit_space = pallet['remaining_space']
                    
                    if best_fit and best_fit_space > 0:
                        # Fill the best fit pallet as much as possible
                        qty_for_this_pallet = min(qty_to_pack, best_fit_space)
                        best_fit['contents'][sku] = best_fit['contents'].get(sku, 0) + qty_for_this_pallet
                        best_fit['remaining_space'] -= qty_for_this_pallet
                        qty_to_pack -= qty_for_this_pallet
                    else:
                        # Create a new pallet
                        qty_for_new_pallet = min(qty_to_pack, max_limit)
                        new_pallet = {
                            'contents': {sku: qty_for_new_pallet},
                            'remaining_space': max_limit - qty_for_new_pallet
                        }
                        mixed_pallets.append(new_pallet)
                        qty_to_pack -= qty_for_new_pallet
        
        # Extract contents from mixed pallets
        for pallet in mixed_pallets:
            consolidated_pallets.append({'contents': pallet['contents']})
        
        print(f"Created {len(consolidated_pallets)} mixed pallet(s)")
        for i, pallet in enumerate(consolidated_pallets, 1):
            total = sum(pallet['contents'].values())
            contents_str = ', '.join([f'{sku}: {qty}' for sku, qty in pallet['contents'].items()])
            print(f"  Mixed Pallet {i}: {contents_str} (Total: {total} cartons)")
            
    all_pallets = full_pallets + consolidated_pallets
    print(f"Total: {len(all_pallets)} pallets")
    return all_pallets


def generate_wro_boxes(pallets, po_number, inventory_map):
    """
    Generates the 'boxes' array for the WRO payload, including tracking numbers.
    """
    boxes = []
    for i, pallet in enumerate(pallets, 1):
        box_items = []
        for sku, quantity in pallet['contents'].items():
            box_items.append({
                "inventory_id": inventory_map[sku],
                "quantity": quantity
            })
        
        boxes.append({
            "tracking_number": f"{po_number}-PALLET-{i:03d}",
            "box_items": box_items
        })
    return boxes


def create_wro(api_key, po_number, arrival_date, po_data, pallet_limits, region, fulfillment_center_name, manual_pallets):
    """
    Main function to orchestrate the WRO creation process.
    
    Args:
        manual_pallets: REQUIRED list of pallet configurations from pallet sheet. 
                       Each pallet is a dict of SKU->quantity (in ITEMS, not cartons).
                       Example: [{"YP5": 252, "YP6": 180}, {"YP6": 108, "YP7": 216}]
                       Must match the pallet sheet configuration exactly.
        po_data: Legacy parameter, not used (kept for backwards compatibility)
        pallet_limits: Legacy parameter, not used (kept for backwards compatibility)
    """
    try:
        # 1. Validate region and fulfillment center
        region_upper = region.upper()
        if region_upper not in FULFILLMENT_CENTERS:
            raise ValueError(f"Region '{region_upper}' is not defined. Available options: {list(FULFILLMENT_CENTERS.keys())}")
        
        if fulfillment_center_name not in FULFILLMENT_CENTERS[region_upper]:
            raise ValueError(f"Fulfillment center '{fulfillment_center_name}' not found for region '{region_upper}'. Available options: {list(FULFILLMENT_CENTERS[region_upper].keys())}")
            
        fulfillment_center_id = FULFILLMENT_CENTERS[region_upper][fulfillment_center_name]

        # 2. Get inventory IDs for all SKUs
        all_skus = list(po_data.keys())
        if manual_pallets:
            # Extract all SKUs from manual pallet configurations
            manual_skus = set()
            for pallet in manual_pallets:
                manual_skus.update(pallet.keys())
            all_skus = list(manual_skus)
        
        inventory_map = get_inventory_map(api_key, all_skus)

        # 3. Use manual pallet configuration from pallet sheet
        if not manual_pallets:
            raise ValueError("Manual pallet configuration is required. The automatic packing algorithm has been removed.")
        
        print(f"Using pallet sheet configuration: {len(manual_pallets)} pallets")
        for i, pallet in enumerate(manual_pallets, 1):
            total = sum(pallet.values())
            contents_str = ', '.join([f'{sku}: {qty} items' for sku, qty in pallet.items()])
            print(f"  Pallet {i}: {contents_str} (Total: {total} items)")
        
        # Convert manual pallet format to internal format
        pallets = [{'contents': pallet} for pallet in manual_pallets]

        # 4. Generate the 'boxes' part of the payload
        boxes = generate_wro_boxes(pallets, po_number, inventory_map)

        # 5. Build the final WRO payload according to the API documentation
        payload = {
            "purchase_order_number": po_number,
            "expected_arrival_date": arrival_date,
            "fulfillment_center": {
                "id": fulfillment_center_id
            },
            "boxes": boxes,
            "package_type": "Pallet",
            "box_packaging_type": "MultipleSkuPerBox"
        }

        # 6. Submit to ShipBob API
        print("Submitting WRO to ShipBob...")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        response = requests.post(f"{SHIPBOB_API_BASE}/2.0/receiving", headers=headers, data=json.dumps(payload))
        
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        result = response.json()
        print("\n✅ WRO created successfully!")
        print(f"   WRO ID: {result.get('id')}")
        print(f"   Status: {result.get('status')}")
        print(f"   Total Pallets: {len(boxes)}")
        print(f"   Fulfillment Center: {fulfillment_center_name}")

    except requests.exceptions.HTTPError as e:
        print(f"\n❌ Error creating WRO: {e.response.status_code}")
        print("   Response from ShipBob:")
        try:
            print(json.dumps(e.response.json(), indent=2))
        except json.JSONDecodeError:
            print(e.response.text)
    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create a ShipBob Warehouse Receiving Order (WRO) matching pallet sheet configuration.",
        epilog="""
IMPORTANT: This script requires the exact pallet configuration from your pallet sheet.
Quantities must be in ITEMS, not cartons. Convert: cartons × items_per_carton = items

Example:
  python create_wro.py --po-number "0006" --arrival-date "2025-11-01T12:00:00Z" \\
    --region "AU" --fulfillment-center "Sydney" \\
    --manual-pallets '[{"YP5": 252, "YP6": 180}, {"YP6": 108, "YP7": 216}]'

How to convert from pallet sheet:
  - Pallet 1: YP5 (7 cartons) + YP6 (5 cartons) → {"YP5": 252, "YP6": 180}  (7×36=252, 5×36=180)
  - Pallet 2: YP6 (3 cartons) + YP7 (6 cartons) → {"YP6": 108, "YP7": 216}  (3×36=108, 6×36=216)
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument("--po-number", type=str, required=True, help="Purchase Order number (must be unique).")
    parser.add_argument("--arrival-date", type=str, required=True, help="Expected arrival date in ISO 8601 format (e.g., '2025-07-07T12:00:00Z').")
    parser.add_argument("--region", type=str, required=True, help="The destination region (e.g., 'US', 'AU', 'CA'). Determines which API key to use (SHIPBOB_API_KEY_{REGION}).")
    parser.add_argument("--fulfillment-center", type=str, required=True, help="Name of the destination fulfillment center.")
    parser.add_argument('--manual-pallets', type=str, required=True, help='[REQUIRED] JSON string of manual pallet configuration from pallet sheet (ITEMS not cartons). Example: \'[{"SKU1": 252, "SKU2": 180}, {"SKU1": 108}]\' - Each object is one pallet with SKU quantities.')
    parser.add_argument('--po-data', type=str, help='[DEPRECATED] Use --manual-pallets instead')
    parser.add_argument('--pallet-limits', type=str, help='[DEPRECATED] Use --manual-pallets instead')

    args = parser.parse_args()

    # Use region-specific API key
    region_upper = args.region.upper()
    api_key_env_var = f"SHIPBOB_API_KEY_{region_upper}"
    api_key = os.getenv(api_key_env_var)
    if not api_key:
        raise ValueError(f"{api_key_env_var} environment variable not set.")

    # Parse manual pallets (now required)
    try:
        manual_pallets = json.loads(args.manual_pallets)
        if not isinstance(manual_pallets, list):
            raise ValueError("--manual-pallets must be a JSON array of pallet objects")
        if len(manual_pallets) == 0:
            raise ValueError("--manual-pallets must contain at least one pallet")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in --manual-pallets: {e}")
    
    # Show deprecation warning if old arguments are used
    if args.po_data or args.pallet_limits:
        print("\n⚠️  WARNING: --po-data and --pallet-limits are deprecated.")
        print("   These arguments are ignored. Use --manual-pallets only.\n")

    create_wro(
        api_key=api_key,
        po_number=args.po_number,
        arrival_date=args.arrival_date,
        po_data={},  # Not used when manual_pallets is provided
        pallet_limits={},  # Not used when manual_pallets is provided
        region=args.region,
        fulfillment_center_name=args.fulfillment_center,
        manual_pallets=manual_pallets
    ) 