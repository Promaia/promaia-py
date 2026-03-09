"""
Shopify REST Admin API connector for Promaia.

Syncs orders, products, and inventory into SQLite directly (no markdown, no embeddings).
Inventory uses an append-only snapshot table to preserve history over time.

Authentication uses the client credentials grant (24h expiring tokens).
"""
from __future__ import annotations

import json
import logging
import asyncio
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from urllib.parse import urlencode, parse_qs, urlparse

from .base import BaseConnector, QueryFilter, DateRangeFilter, SyncResult

logger = logging.getLogger(__name__)

# Lazy imports
aiohttp = None


def _ensure_aiohttp():
    global aiohttp
    if aiohttp is None:
        try:
            import aiohttp as _aiohttp
            aiohttp = _aiohttp
        except ImportError:
            raise ImportError(
                "Shopify connector requires aiohttp.\n"
                "Install with: uv pip install aiohttp"
            )


class ShopifyConnector(BaseConnector):
    """Shopify REST Admin API connector for store data synchronization."""

    API_VERSION = "2026-01"
    MAX_PAGE_SIZE = 250

    # Orders fields to request (excludes PII: email, shipping_address, billing_address)
    ORDER_FIELDS = [
        "id", "order_number", "name", "financial_status", "fulfillment_status",
        "total_price", "subtotal_price", "total_tax", "total_discounts", "currency",
        "line_items", "discount_codes", "shipping_lines", "refunds",
        "note", "tags", "cancelled_at", "closed_at", "processed_at",
        "created_at", "updated_at",
    ]

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        _ensure_aiohttp()

        self.shop_domain = config.get("shop_domain") or config.get("database_id") or ""
        self.client_id = config.get("client_id") or ""
        self.client_secret = config.get("client_secret") or ""
        self.workspace = config.get("workspace", "default")

        self._base_url = f"https://{self.shop_domain}/admin/api/{self.API_VERSION}"
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._session: Optional[aiohttp.ClientSession] = None

        # Rate limiting: Shopify REST = 2 req/s, 40-request bucket
        self._last_request_time: float = 0
        self._rate_limit_delay: float = 0.5  # 2 req/s

        # Cached location ID (single-location store)
        self._location_id: Optional[str] = None

    # ── Session management ─────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _close_session(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ── Authentication ─────────────────────────────────────────────────

    async def _ensure_token(self):
        """Obtain or refresh the access token via client credentials grant."""
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return  # token still valid (with 60s buffer)

        session = await self._get_session()
        url = f"https://{self.shop_domain}/admin/oauth/access_token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        async with session.post(url, data=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                msg = self._extract_error_message(body)
                raise ConnectionError(
                    f"Shopify auth failed (HTTP {resp.status}): {msg}"
                )
            data = await resp.json()

        self._access_token = data["access_token"]
        self._token_expires_at = now + data.get("expires_in", 86399)
        self.logger.info("Shopify access token obtained/refreshed")

    # ── HTTP helpers ───────────────────────────────────────────────────

    async def _rate_limit(self):
        """Sleep if needed to stay within Shopify's rate limits."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < self._rate_limit_delay:
            await asyncio.sleep(self._rate_limit_delay - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    async def _api_get(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> aiohttp.ClientResponse:
        """Make an authenticated GET request to the Shopify REST API.

        Returns the full response so callers can inspect headers (e.g. Link).
        """
        await self._ensure_token()
        await self._rate_limit()

        session = await self._get_session()
        url = f"{self._base_url}/{endpoint}"
        headers = {"X-Shopify-Access-Token": self._access_token}

        resp = await session.get(url, headers=headers, params=params)

        # Handle rate limiting (429)
        if resp.status == 429:
            retry_after = float(resp.headers.get("Retry-After", "2.0"))
            self.logger.warning(f"Shopify rate limited, retrying after {retry_after}s")
            await resp.release()
            await asyncio.sleep(retry_after)
            resp = await session.get(url, headers=headers, params=params)

        # Handle token expiry (401) — refresh and retry once
        if resp.status == 401:
            self.logger.info("Token expired, refreshing...")
            await resp.release()
            self._access_token = None
            await self._ensure_token()
            headers = {"X-Shopify-Access-Token": self._access_token}
            resp = await session.get(url, headers=headers, params=params)

        if resp.status != 200:
            body = await resp.text()
            msg = self._extract_error_message(body)
            raise RuntimeError(f"Shopify API error (HTTP {resp.status}) on {endpoint}: {msg}")

        return resp

    async def _paginate(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        resource_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Paginate through a Shopify REST endpoint using Link-header cursors.

        Returns all items across all pages.
        """
        params = dict(params or {})
        params.setdefault("limit", self.MAX_PAGE_SIZE)

        all_items: List[Dict[str, Any]] = []
        next_url: Optional[str] = None

        while True:
            if next_url:
                # Follow the full next URL from the Link header
                await self._ensure_token()
                await self._rate_limit()
                session = await self._get_session()
                headers = {"X-Shopify-Access-Token": self._access_token}
                resp = await session.get(next_url, headers=headers)

                if resp.status == 429:
                    retry_after = float(resp.headers.get("Retry-After", "2.0"))
                    await resp.release()
                    await asyncio.sleep(retry_after)
                    resp = await session.get(next_url, headers=headers)

                if resp.status != 200:
                    body = await resp.text()
                    msg = self._extract_error_message(body)
                    raise RuntimeError(
                        f"Shopify pagination error (HTTP {resp.status}): {msg}"
                    )
            else:
                resp = await self._api_get(endpoint, params)

            data = await resp.json()

            # Extract items using resource_key (e.g. "orders", "products")
            key = resource_key or endpoint.replace(".json", "")
            items = data.get(key, [])
            all_items.extend(items)

            self.logger.debug(
                f"Fetched page: {len(items)} items (total so far: {len(all_items)})"
            )

            # Check for next page via Link header
            link_header = resp.headers.get("Link", "")
            next_url = self._parse_next_link(link_header)
            await resp.release()

            if not next_url or not items:
                break

        return all_items

    @staticmethod
    def _parse_next_link(link_header: str) -> Optional[str]:
        """Extract the 'next' URL from a Shopify Link header."""
        if not link_header:
            return None
        for part in link_header.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                return url
        return None

    @staticmethod
    def _extract_error_message(body: str, max_len: int = 300) -> str:
        """Extract a readable error message from a Shopify response body.

        Shopify returns full HTML pages on auth/API errors. This pulls out
        the <title> text (which contains the actual error) and falls back
        to a truncated snippet.
        """
        import re
        match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
        clean = re.sub(r"<[^>]+>", " ", body)
        clean = " ".join(clean.split())
        if len(clean) > max_len:
            clean = clean[:max_len] + "..."
        return clean

    # ── Table management ───────────────────────────────────────────────

    @staticmethod
    def _ensure_tables(conn):
        """Create Shopify tables if they don't exist."""
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shopify_orders (
                page_id             TEXT UNIQUE,
                workspace           TEXT,
                database_id         TEXT,
                file_path           TEXT,
                created_time        TEXT,
                last_edited_time    TEXT,
                synced_time         TEXT,
                file_size           INTEGER,
                checksum            TEXT,

                order_number        INTEGER,
                name                TEXT,
                financial_status    TEXT,
                fulfillment_status  TEXT,
                total_price         TEXT,
                subtotal_price      TEXT,
                total_tax           TEXT,
                total_discounts     TEXT,
                currency            TEXT,
                line_items          TEXT,
                discount_codes      TEXT,
                shipping_lines      TEXT,
                refunds             TEXT,
                note                TEXT,
                tags                TEXT,
                cancelled_at        TEXT,
                closed_at           TEXT,
                processed_at        TEXT,
                order_created_at    TEXT,
                order_updated_at    TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopify_orders_financial
            ON shopify_orders(financial_status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopify_orders_fulfillment
            ON shopify_orders(fulfillment_status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopify_orders_created
            ON shopify_orders(order_created_at)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shopify_products (
                page_id             TEXT UNIQUE,
                workspace           TEXT,
                database_id         TEXT,
                file_path           TEXT,
                created_time        TEXT,
                last_edited_time    TEXT,
                synced_time         TEXT,
                file_size           INTEGER,
                checksum            TEXT,

                title               TEXT,
                handle              TEXT,
                vendor              TEXT,
                product_type        TEXT,
                status              TEXT,
                tags                TEXT,
                variants            TEXT,
                options             TEXT,
                images              TEXT,
                body_html           TEXT,
                product_created_at  TEXT,
                product_updated_at  TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopify_products_status
            ON shopify_products(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopify_products_vendor
            ON shopify_products(vendor)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopify_products_type
            ON shopify_products(product_type)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shopify_inventory_snapshots (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                variant_id          TEXT,
                product_id          TEXT,
                sku                 TEXT,
                product_title       TEXT,
                variant_title       TEXT,
                inventory_item_id   TEXT,
                available           INTEGER,
                recorded_at         TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopify_inv_variant_time
            ON shopify_inventory_snapshots(variant_id, recorded_at)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopify_inv_sku
            ON shopify_inventory_snapshots(sku)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopify_inv_product
            ON shopify_inventory_snapshots(product_id)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS shopify_variant_costs (
                inventory_item_id   TEXT PRIMARY KEY,
                variant_id          TEXT,
                product_id          TEXT,
                sku                 TEXT,
                product_title       TEXT,
                variant_title       TEXT,
                cost                TEXT,
                updated_at          TEXT
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopify_variant_costs_product
            ON shopify_variant_costs(product_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_shopify_variant_costs_sku
            ON shopify_variant_costs(sku)
        """)

        conn.commit()

    # ── Order sync ─────────────────────────────────────────────────────

    async def _sync_orders(self, conn, date_filter: Optional[DateRangeFilter], result: SyncResult):
        """Sync orders from Shopify into shopify_orders."""
        params: Dict[str, Any] = {
            "status": "any",
            "fields": ",".join(self.ORDER_FIELDS),
        }

        if date_filter and date_filter.start_date:
            params["updated_at_min"] = date_filter.start_date.isoformat()
        # Don't send updated_at_max — we always want the latest data, and
        # naive datetimes can cause timezone mismatches with Shopify's API.

        self.logger.info(f"Syncing Shopify orders (params: {params})")
        orders = await self._paginate("orders.json", params, "orders")
        result.pages_fetched += len(orders)
        result.api_calls_count += 1  # approximate; pagination adds more

        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.cursor()

        for order in orders:
            page_id = str(order["id"])
            try:
                cursor.execute("""
                    INSERT INTO shopify_orders (
                        page_id, workspace, database_id, file_path,
                        created_time, last_edited_time, synced_time, file_size, checksum,
                        order_number, name, financial_status, fulfillment_status,
                        total_price, subtotal_price, total_tax, total_discounts, currency,
                        line_items, discount_codes, shipping_lines, refunds,
                        note, tags, cancelled_at, closed_at, processed_at,
                        order_created_at, order_updated_at
                    ) VALUES (
                        ?, ?, ?, NULL,
                        ?, ?, ?, NULL, NULL,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?
                    )
                    ON CONFLICT(page_id) DO UPDATE SET
                        last_edited_time = excluded.last_edited_time,
                        synced_time = excluded.synced_time,
                        financial_status = excluded.financial_status,
                        fulfillment_status = excluded.fulfillment_status,
                        total_price = excluded.total_price,
                        subtotal_price = excluded.subtotal_price,
                        total_tax = excluded.total_tax,
                        total_discounts = excluded.total_discounts,
                        line_items = excluded.line_items,
                        discount_codes = excluded.discount_codes,
                        shipping_lines = excluded.shipping_lines,
                        refunds = excluded.refunds,
                        note = excluded.note,
                        tags = excluded.tags,
                        cancelled_at = excluded.cancelled_at,
                        closed_at = excluded.closed_at,
                        order_updated_at = excluded.order_updated_at
                """, (
                    page_id, self.workspace, self.shop_domain,
                    order.get("created_at"), order.get("updated_at"), now,
                    order.get("order_number"), order.get("name"),
                    order.get("financial_status"), order.get("fulfillment_status"),
                    order.get("total_price"), order.get("subtotal_price"),
                    order.get("total_tax"), order.get("total_discounts"),
                    order.get("currency"),
                    json.dumps(order.get("line_items", [])),
                    json.dumps(order.get("discount_codes", [])),
                    json.dumps(order.get("shipping_lines", [])),
                    json.dumps(order.get("refunds", [])),
                    order.get("note"), order.get("tags"),
                    order.get("cancelled_at"), order.get("closed_at"),
                    order.get("processed_at"),
                    order.get("created_at"), order.get("updated_at"),
                ))
                result.pages_saved += 1
            except Exception as e:
                self.logger.error(f"Failed to upsert order {page_id}: {e}")
                result.add_error(f"order {page_id}: {e}")

        conn.commit()
        self.logger.info(f"Orders sync: {result.pages_saved} upserted from {len(orders)} fetched")

    # ── Product sync ───────────────────────────────────────────────────

    async def _sync_products(self, conn, result: SyncResult):
        """Sync products from Shopify into shopify_products."""
        params: Dict[str, Any] = {"status": "active"}

        self.logger.info("Syncing Shopify products")
        products = await self._paginate("products.json", params, "products")
        result.pages_fetched += len(products)

        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.cursor()

        for product in products:
            page_id = str(product["id"])
            try:
                cursor.execute("""
                    INSERT INTO shopify_products (
                        page_id, workspace, database_id, file_path,
                        created_time, last_edited_time, synced_time, file_size, checksum,
                        title, handle, vendor, product_type, status, tags,
                        variants, options, images, body_html,
                        product_created_at, product_updated_at
                    ) VALUES (
                        ?, ?, ?, NULL,
                        ?, ?, ?, NULL, NULL,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?
                    )
                    ON CONFLICT(page_id) DO UPDATE SET
                        last_edited_time = excluded.last_edited_time,
                        synced_time = excluded.synced_time,
                        title = excluded.title,
                        handle = excluded.handle,
                        vendor = excluded.vendor,
                        product_type = excluded.product_type,
                        status = excluded.status,
                        tags = excluded.tags,
                        variants = excluded.variants,
                        options = excluded.options,
                        images = excluded.images,
                        body_html = excluded.body_html,
                        product_updated_at = excluded.product_updated_at
                """, (
                    page_id, self.workspace, self.shop_domain,
                    product.get("created_at"), product.get("updated_at"), now,
                    product.get("title"), product.get("handle"),
                    product.get("vendor"), product.get("product_type"),
                    product.get("status"), product.get("tags"),
                    json.dumps(product.get("variants", [])),
                    json.dumps(product.get("options", [])),
                    json.dumps(product.get("images", [])),
                    product.get("body_html"),
                    product.get("created_at"), product.get("updated_at"),
                ))
                result.pages_saved += 1
            except Exception as e:
                self.logger.error(f"Failed to upsert product {page_id}: {e}")
                result.add_error(f"product {page_id}: {e}")

        conn.commit()
        self.logger.info(f"Products sync: {result.pages_saved} upserted from {len(products)} fetched")

    # ── Inventory sync ─────────────────────────────────────────────────

    async def _get_location_id(self) -> str:
        """Fetch the primary location ID (cached after first call)."""
        if self._location_id:
            return self._location_id

        resp = await self._api_get("locations.json")
        data = await resp.json()
        await resp.release()

        locations = data.get("locations", [])
        if not locations:
            raise RuntimeError("No Shopify locations found")

        # Use the first active location
        for loc in locations:
            if loc.get("active", False):
                self._location_id = str(loc["id"])
                self.logger.info(f"Using location: {loc.get('name')} ({self._location_id})")
                return self._location_id

        # Fallback to first location
        self._location_id = str(locations[0]["id"])
        return self._location_id

    @staticmethod
    def _build_variant_map(conn) -> Dict[str, Dict[str, Any]]:
        """Build inventory_item_id → variant info mapping from shopify_products.

        Returns a dict keyed by inventory_item_id with values containing
        variant_id, sku, product_title, variant_title, and product_id.
        """
        cursor = conn.cursor()
        cursor.execute("SELECT page_id, title, variants FROM shopify_products")
        rows = cursor.fetchall()

        inv_item_map: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            product_id = row[0] if isinstance(row, tuple) else row["page_id"]
            product_title = row[1] if isinstance(row, tuple) else row["title"]
            variants_json = row[2] if isinstance(row, tuple) else row["variants"]
            try:
                variants = json.loads(variants_json) if variants_json else []
            except (json.JSONDecodeError, TypeError):
                continue
            for v in variants:
                inv_item_id = str(v.get("inventory_item_id", ""))
                if inv_item_id:
                    inv_item_map[inv_item_id] = {
                        "variant_id": str(v.get("id", "")),
                        "sku": v.get("sku", ""),
                        "product_title": product_title,
                        "variant_title": v.get("title", "Default Title"),
                        "product_id": product_id,
                    }
        return inv_item_map

    async def _sync_inventory(self, conn, result: SyncResult):
        """Sync inventory snapshots — append-only, only when quantities change."""
        location_id = await self._get_location_id()

        inv_item_map = self._build_variant_map(conn)

        if not inv_item_map:
            self.logger.warning("No product variants found — skipping inventory sync")
            return

        # Fetch inventory levels for our location
        self.logger.info(f"Syncing inventory for location {location_id}")
        levels = await self._paginate(
            "inventory_levels.json",
            {"location_ids": location_id},
            "inventory_levels",
        )

        # Get the latest snapshot per variant for diffing
        cursor = conn.cursor()
        cursor.execute("""
            SELECT variant_id, available
            FROM shopify_inventory_snapshots s1
            WHERE recorded_at = (
                SELECT MAX(recorded_at)
                FROM shopify_inventory_snapshots s2
                WHERE s2.variant_id = s1.variant_id
            )
        """)
        last_snapshots: Dict[str, int] = {}
        for row in cursor.fetchall():
            vid = row[0] if isinstance(row, tuple) else row["variant_id"]
            avail = row[1] if isinstance(row, tuple) else row["available"]
            last_snapshots[vid] = avail

        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        skipped = 0

        for level in levels:
            inv_item_id = str(level.get("inventory_item_id", ""))
            available = level.get("available", 0)

            variant_info = inv_item_map.get(inv_item_id)
            if not variant_info:
                continue  # inventory item not linked to a known variant

            variant_id = variant_info["variant_id"]

            # Only insert if the quantity changed
            if last_snapshots.get(variant_id) == available:
                skipped += 1
                continue

            try:
                cursor.execute("""
                    INSERT INTO shopify_inventory_snapshots (
                        variant_id, product_id, sku, product_title,
                        variant_title, inventory_item_id, available, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    variant_id,
                    variant_info["product_id"],
                    variant_info["sku"],
                    variant_info["product_title"],
                    variant_info["variant_title"],
                    inv_item_id,
                    available,
                    now,
                ))
                inserted += 1
            except Exception as e:
                self.logger.error(f"Failed to insert inventory snapshot for variant {variant_id}: {e}")
                result.add_error(f"inventory {variant_id}: {e}")

        conn.commit()
        self.logger.info(
            f"Inventory sync: {inserted} snapshots inserted, {skipped} unchanged"
        )

    # ── Cost (COGS) sync ──────────────────────────────────────────────

    async def _sync_costs(self, conn, result: SyncResult):
        """Fetch cost data from Shopify InventoryItem resources and store in shopify_variant_costs."""
        inv_item_map = self._build_variant_map(conn)
        if not inv_item_map:
            self.logger.warning("No product variants found — skipping cost sync")
            return

        # Load existing costs for diff check
        cursor = conn.cursor()
        cursor.execute("SELECT inventory_item_id, cost FROM shopify_variant_costs")
        existing_costs: Dict[str, Optional[str]] = {}
        for row in cursor.fetchall():
            iid = row[0] if isinstance(row, tuple) else row["inventory_item_id"]
            cost = row[1] if isinstance(row, tuple) else row["cost"]
            existing_costs[iid] = cost

        # Batch-fetch inventory items (Shopify supports up to 100 IDs per request)
        all_inv_ids = list(inv_item_map.keys())
        now = datetime.now(timezone.utc).isoformat()
        updated = 0
        batch_size = 100

        for i in range(0, len(all_inv_ids), batch_size):
            batch_ids = all_inv_ids[i : i + batch_size]
            ids_param = ",".join(batch_ids)

            try:
                resp = await self._api_get(
                    "inventory_items.json", {"ids": ids_param, "limit": batch_size}
                )
                data = await resp.json()
                await resp.release()
            except Exception as e:
                self.logger.error(f"Failed to fetch inventory items batch {i}: {e}")
                result.add_error(f"cost batch {i}: {e}")
                continue

            for item in data.get("inventory_items", []):
                inv_item_id = str(item.get("id", ""))
                cost_value = item.get("cost")
                if cost_value is None:
                    continue

                cost_str = str(cost_value)

                # Skip if cost hasn't changed
                if existing_costs.get(inv_item_id) == cost_str:
                    continue

                variant_info = inv_item_map.get(inv_item_id, {})
                try:
                    cursor.execute("""
                        INSERT INTO shopify_variant_costs (
                            inventory_item_id, variant_id, product_id,
                            sku, product_title, variant_title,
                            cost, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(inventory_item_id) DO UPDATE SET
                            cost = excluded.cost,
                            updated_at = excluded.updated_at,
                            variant_id = excluded.variant_id,
                            product_id = excluded.product_id,
                            sku = excluded.sku,
                            product_title = excluded.product_title,
                            variant_title = excluded.variant_title
                    """, (
                        inv_item_id,
                        variant_info.get("variant_id", ""),
                        variant_info.get("product_id", ""),
                        variant_info.get("sku", ""),
                        variant_info.get("product_title", ""),
                        variant_info.get("variant_title", ""),
                        cost_str,
                        now,
                    ))
                    updated += 1
                except Exception as e:
                    self.logger.error(f"Failed to upsert cost for {inv_item_id}: {e}")
                    result.add_error(f"cost {inv_item_id}: {e}")

        conn.commit()
        self.logger.info(f"Cost sync: {updated} variant costs upserted from {len(all_inv_ids)} items")

    # ── BaseConnector interface ────────────────────────────────────────

    async def connect(self) -> bool:
        try:
            await self._ensure_token()
            self.logger.info(f"Connected to Shopify store: {self.shop_domain}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Shopify: {e}")
            return False

    async def test_connection(self) -> bool:
        try:
            resp = await self._api_get("shop.json")
            data = await resp.json()
            await resp.release()
            shop_name = data.get("shop", {}).get("name", "unknown")
            self.logger.info(f"Shopify connection OK — store: {shop_name}")
            return True
        except Exception as e:
            self.logger.error(f"Shopify connection test failed: {e}")
            return False

    async def get_database_schema(self) -> Dict[str, Any]:
        return {
            "orders": {
                "order_number": {"type": "number"},
                "financial_status": {"type": "select"},
                "fulfillment_status": {"type": "select"},
                "total_price": {"type": "text"},
                "currency": {"type": "text"},
            },
            "products": {
                "title": {"type": "text"},
                "vendor": {"type": "text"},
                "product_type": {"type": "text"},
                "status": {"type": "select"},
            },
            "inventory": {
                "sku": {"type": "text"},
                "available": {"type": "number"},
            },
        }

    async def query_pages(
        self,
        filters: Optional[List[QueryFilter]] = None,
        date_filter: Optional[DateRangeFilter] = None,
        sort_by: Optional[str] = None,
        sort_direction: str = "desc",
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Query orders from Shopify (primary resource for query_pages)."""
        if not self._access_token:
            await self.connect()

        params: Dict[str, Any] = {
            "status": "any",
            "fields": ",".join(self.ORDER_FIELDS),
        }
        if date_filter and date_filter.start_date:
            params["created_at_min"] = date_filter.start_date.isoformat()
        if date_filter and date_filter.end_date:
            params["created_at_max"] = date_filter.end_date.isoformat()
        if limit:
            params["limit"] = min(limit, self.MAX_PAGE_SIZE)

        return await self._paginate("orders.json", params, "orders")

    async def get_page_content(self, page_id: str, include_properties: bool = True) -> Dict[str, Any]:
        """Get a single order by ID."""
        resp = await self._api_get(f"orders/{page_id}.json")
        data = await resp.json()
        await resp.release()
        return data.get("order", {})

    async def get_page_properties(self, page_id: str) -> Dict[str, Any]:
        return await self.get_page_content(page_id, include_properties=True)

    async def sync_to_local(self, *args, **kwargs) -> SyncResult:
        raise NotImplementedError("Use sync_to_local_unified for Shopify connector")

    async def sync_to_local_unified(
        self,
        storage,
        db_config,
        filters: Optional[List[QueryFilter]] = None,
        date_filter: Optional[DateRangeFilter] = None,
        include_properties: bool = True,
        force_update: bool = False,
        excluded_properties: Optional[List[str]] = None,
        complex_filter: Optional[Dict[str, Any]] = None,
    ) -> SyncResult:
        """Sync orders, products, and inventory into SQLite."""
        import sqlite3 as _sqlite3
        from promaia.utils.env_writer import get_db_path

        result = SyncResult()
        result.start_time = datetime.now()
        result.database_name = getattr(db_config, "name", "shopify")

        try:
            if not self._access_token:
                await self.connect()

            db_file = str(get_db_path())
            conn = _sqlite3.connect(db_file)
            conn.row_factory = _sqlite3.Row
            try:
                self._ensure_tables(conn)

                await self._sync_orders(conn, date_filter, result)
                await self._sync_products(conn, result)
                await self._sync_inventory(conn, result)
                await self._sync_costs(conn, result)

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        except Exception as e:
            self.logger.error(f"Shopify sync failed: {e}", exc_info=True)
            result.add_error(f"Shopify sync failed: {e}")
        finally:
            await self._close_session()

        result.end_time = datetime.now()
        self.logger.info(
            f"Shopify sync complete: {result.pages_fetched} fetched, "
            f"{result.pages_saved} saved, {result.pages_failed} failed "
            f"({result.duration_seconds:.1f}s)"
        )
        return result
