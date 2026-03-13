# ShipBob API: Guide to Creating a Product

This guide provides a working example and key insights for creating a new product via the ShipBob `/2.0/product` endpoint.

## Final Working Payload

Here is the properly constructed JSON payload to create a simple product.

```json
{
    "name": "Yeeps™ Plush Small Parts",
    "type_id": 1,
    "category_id": null,
    "sub_category_id": null,
    "variants": [
        {
            "sku": "YP4",
            "name": "Yeeps™ Plush Small Parts",
            "upc": "00850069241100",
            "barcode": "00850069241100",
            "gtin": "",
            "is_digital": false,
            "status_id": 1,
            "weight": 0.0,
            "dimension": {
                "length": 0.0,
                "width": 0.0,
                "height": 0.0
            },
            "customs": {
                "value": 1,
                "description": "",
                "hs_tariff_code": "",
                "country_code_of_origin": "",
                "is321_eligible": false
            },
            "fulfillment_settings": {
                "requires_prop65": false,
                "dangerous_goods": false,
                "msds_url": "",
                "is_case_pick": false,
                "is_bpm_parcel": false,
                "serial_scan": {
                    "is_enabled": false,
                    "prefix": "",
                    "suffix": "",
                    "exact_character_length": null
                }
            },
            "lot_information": {
                "is_lot": false,
                "minimum_shelf_life_days": null
            },
            "return_preferences": {
                "primary_action_id": 1,
                "backup_action_id": 3,
                "instructions": null,
                "return_to_sender_primary_action_id": null,
                "return_to_sender_backup_action_id": null
            },
            "packaging_material_type_id": null,
            "packaging_requirement_id": null,
            "bundle_definition": [],
            "channel_metadata": []
        }
    ]
}
```

## Key Takeaways & Troubleshooting Notes

When using this endpoint, keep the following points in mind to avoid common validation errors:

1.  **Request Body Structure:** The JSON payload should be a **flat object**. Do not wrap it in a `create_product_request_model_v2` object, even if some error messages might misleadingly suggest it. The `name` and `variants` fields must be at the top level.

2.  **Product `type_id`:**
    *   The `type_id` for a "Regular" product is `1`.
    *   Using `0` will result in an `Invalid product type: 0` validation error.

3.  **Customs Value (`customs.value`):**
    *   This value must be an **integer** and **cannot be less than 1**.
    *   The API will reject a value of `0` with a validation error. Use a minimum of `1` for products, even if they have no declared customs value.

4.  **Variants Array:** Even for a simple product with no real variations, the `variants` array is mandatory and must contain at least one variant object. This object holds the core SKU information.

5.  **Nullable Fields:** Fields that are not applicable, such as `category_id` and `sub_category_id`, can be safely set to `null`. 