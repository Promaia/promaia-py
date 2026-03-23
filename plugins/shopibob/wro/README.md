# WRO Creation Scripts

## Overview
Scripts for creating Warehouse Receiving Orders (WROs) in ShipBob that **exactly match your pallet sheet configuration**.

## Scripts

### create_wro.py
Main script for creating WROs. Requires exact pallet configuration from your pallet sheet.

### cancel_wro.py
Utility script to cancel WROs that need to be corrected.

### create_products.py
Creates new products in ShipBob (US and/or AU).

---

## ⚠️ CRITICAL: WROs Must Match Pallet Sheet Exactly

This script **requires** the exact pallet configuration from your pallet sheet. There is no automatic mode.

**Why this matters:**
- Physical pallets must match what's in the system
- Logistics planning depends on accurate pallet contents
- Receiving/tracking documentation must be consistent
- Warehouse operations need exact SKU locations

**The script enforces:**
- Manual pallet specification is **required**
- Configuration must match pallet sheet exactly
- Quantities must be in **items**, not cartons

---

## Usage

### Creating a WRO

**You MUST provide the exact pallet configuration from your pallet sheet:**

```bash
python3 wro/create_wro.py \
  --po-number "0006" \
  --arrival-date "2025-11-01T12:00:00Z" \
  --region "AU" \
  --fulfillment-center "Sydney" \
  --manual-pallets '[
    {"YP5": 252, "YP6": 180},
    {"YP6": 108, "YP7": 216}
  ]'
```

**Key Points:**
- `--manual-pallets` is **REQUIRED** (not optional)
- Quantities are in **items**, not cartons
- Each object in the array is one pallet
- Must match your pallet sheet configuration **exactly**

### Converting from Pallet Sheet to Command

**Step-by-step conversion:**

1. **Get pallet configuration from pallet sheet:**
   ```
   Pallet 1: YP5 (7 cartons) + YP6 (5 cartons)
   Pallet 2: YP6 (3 cartons) + YP7 (6 cartons)
   ```

2. **Convert cartons to items (items_per_carton = 36 for plush):**
   ```
   Pallet 1: YP5 (7 × 36 = 252 items) + YP6 (5 × 36 = 180 items)
   Pallet 2: YP6 (3 × 36 = 108 items) + YP7 (6 × 36 = 216 items)
   ```

3. **Format as JSON array:**
   ```json
   [
     {"YP5": 252, "YP6": 180},
     {"YP6": 108, "YP7": 216}
   ]
   ```

4. **Use in command:**
   ```bash
   --manual-pallets '[{"YP5": 252, "YP6": 180}, {"YP6": 108, "YP7": 216}]'
   ```

### Environment Variables

Set API keys for your region:
```bash
export SHIPBOB_API_KEY_US="your-us-key"
export SHIPBOB_API_KEY_AU="your-au-key"
export SHIPBOB_API_KEY_CA="your-ca-key"
export SHIPBOB_API_KEY_UK="your-uk-key"
```

### Cancelling a WRO

If you need to correct a WRO:
```bash
python3 wro/cancel_wro.py --wro-id 845215 --region "AU"
```

---

## Workflow

1. **Review pallet sheet** - Get exact pallet configuration
2. **Convert cartons to items** - Multiply by items per carton
3. **Create WRO with manual pallets** - Use exact configuration
4. **Verify WRO** - Check quantities match pallet sheet
5. **If incorrect** - Cancel and recreate

---

## Common Issues

### "PO reference already exists"
- A WRO with that PO number already exists
- Cancel the old one first or use a different PO number

### "Resource not found" when cancelling
- Try POST to `/2.0/receiving/{id}/cancel` endpoint
- The cancel_wro.py script uses the correct endpoint

### Quantities don't match pallet sheet
- Make sure you're using **items**, not cartons
- Verify items per carton (usually 36 for plush)
- Use `--manual-pallets` to specify exact configuration

---

## Files

- `create_wro.py` - Main WRO creation script
- `cancel_wro.py` - Cancel WRO utility
- `create_products.py` - Product creation script
- `WRO_Automation_Guide.md` - Detailed API documentation
- `README.md` - This file

