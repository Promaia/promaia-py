import os
import requests
import json
import sys

# ShipBob API Base
SHIPBOB_API_BASE = "https://api.shipbob.com"

# Product definitions for YP5, YP6, YP7
PRODUCTS = [
    {
        "name": "Yeeps™ Plush Dog",
        "sku": "YP5",
        "barcode": "00850069241117",
        "weight": 6.0,
        "dimensions": {"length": 6.0, "width": 6.0, "height": 3.0}
    },
    {
        "name": "Yeeps™ Plush Cat",
        "sku": "YP6",
        "barcode": "00850069241124",
        "weight": 6.0,
        "dimensions": {"length": 6.0, "width": 6.0, "height": 3.0}
    },
    {
        "name": "Yeeps™ Plush Chicken",
        "sku": "YP7",
        "barcode": "00850069241131",
        "weight": 6.0,
        "dimensions": {"length": 6.0, "width": 6.0, "height": 3.0}
    }
]

def create_product(api_key, product_data):
    """
    Creates a product in ShipBob using the /2.0/product endpoint.
    """
    payload = {
        "name": product_data["name"],
        "type_id": 1,  # Regular product
        "category_id": None,
        "sub_category_id": None,
        "variants": [
            {
                "sku": product_data["sku"],
                "name": product_data["name"],
                "upc": product_data["barcode"],
                "barcode": product_data["barcode"],
                "gtin": "",
                "is_digital": False,
                "status_id": 1,  # Active
                "weight": product_data["weight"],
                "dimension": {
                    "length": product_data["dimensions"]["length"],
                    "width": product_data["dimensions"]["width"],
                    "height": product_data["dimensions"]["height"]
                },
                "customs": {
                    "value": 7,
                    "description": "Stuffed plush toy in cardboard box",
                    "hs_tariff_code": "95030041",
                    "country_code_of_origin": "CN",
                    "is321_eligible": False
                },
                "fulfillment_settings": {
                    "requires_prop65": False,
                    "dangerous_goods": False,
                    "msds_url": "",
                    "is_case_pick": False,
                    "is_bpm_parcel": False,
                    "serial_scan": {
                        "is_enabled": False,
                        "prefix": "",
                        "suffix": "",
                        "exact_character_length": None
                    }
                },
                "lot_information": {
                    "is_lot": False,
                    "minimum_shelf_life_days": None
                },
                "return_preferences": {
                    "primary_action_id": 1,  # Restock
                    "backup_action_id": 2,   # Quarantine
                    "instructions": "Please ensure that the small parts product is not hidden in the brown packaging paper\n\nAll these checks must pass:\n- Box can be opened, but it's not damaged\n- Plush looks perfect: clean, etc.",
                    "return_to_sender_primary_action_id": 1,
                    "return_to_sender_backup_action_id": 2
                },
                "packaging_material_type_id": 1,  # Box
                "packaging_requirement_id": 1,    # NoRequirements
                "bundle_definition": [],
                "channel_metadata": []
            }
        ]
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    response = requests.post(
        f"{SHIPBOB_API_BASE}/2.0/product",
        headers=headers,
        data=json.dumps(payload)
    )
    
    return response

def main():
    if len(sys.argv) < 2:
        print("Usage: python create_products.py [US|AU|BOTH]")
        sys.exit(1)
    
    region = sys.argv[1].upper()
    
    if region not in ["US", "AU", "BOTH"]:
        print("Region must be US, AU, or BOTH")
        sys.exit(1)
    
    regions_to_process = []
    if region == "BOTH":
        regions_to_process = ["US", "AU"]
    else:
        regions_to_process = [region]
    
    for reg in regions_to_process:
        print(f"\n{'='*60}")
        print(f"Creating products in {reg} ShipBob")
        print(f"{'='*60}\n")
        
        # Get API key for this region
        if reg == "US":
            api_key = os.getenv("SHIPBOB_API_KEY")
        else:
            api_key = os.getenv(f"SHIPBOB_API_KEY_{reg}")
        
        if not api_key:
            print(f"❌ API key not found for {reg}")
            continue
        
        # Create each product
        for product in PRODUCTS:
            print(f"Creating {product['sku']}: {product['name']}...")
            
            try:
                response = create_product(api_key, product)
                
                if response.status_code == 201:
                    result = response.json()
                    variant_id = result.get('variants', [{}])[0].get('id')
                    inventory_id = result.get('variants', [{}])[0].get('inventory', {}).get('inventory_id')
                    print(f"  ✅ Created successfully!")
                    print(f"     Product ID: {result.get('id')}")
                    print(f"     Variant ID: {variant_id}")
                    print(f"     Inventory ID: {inventory_id}")
                elif response.status_code == 400:
                    error_data = response.json()
                    # Check if it's a duplicate SKU error
                    if "already exists" in str(error_data).lower() or "duplicate" in str(error_data).lower():
                        print(f"  ⚠️  Product already exists (SKU: {product['sku']})")
                    else:
                        print(f"  ❌ Error: {response.status_code}")
                        print(f"     {json.dumps(error_data, indent=2)}")
                else:
                    print(f"  ❌ Error: {response.status_code}")
                    try:
                        print(f"     {json.dumps(response.json(), indent=2)}")
                    except:
                        print(f"     {response.text}")
                        
            except Exception as e:
                print(f"  ❌ Exception: {e}")
            
            print()

if __name__ == "__main__":
    main()


