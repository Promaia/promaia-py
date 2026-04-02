# Mitchell Glacier Workflow — End-to-End Test Plan

## Overview

Test the full agentic re-ordering workflow for custom parts: Slack trigger → PO generation → spec download → vendor lookup → draft email with attachments.

## Prerequisites

### What's built and ready
- [x] Filesystem sandbox (`promaia/tools/sandbox.py`) — ephemeral per-session file staging
- [x] `list_workspace_files` tool — agent can see files in sandbox
- [x] Google Drive tools — `drive_search_files`, `drive_download_file`, `drive_list_folder`
- [x] Gmail attachment support — `attachment_paths` on `send_email`, `create_email_draft`, `reply_to_email`
- [x] Gmail connector attachment support — `_create_draft()` and `send_reply()` accept attachments
- [x] `gmail.compose` OAuth scope — for draft creation
- [x] MCP tool routing — external MCP servers discoverable and callable from agentic turn
- [x] Glacier PO Manager MCP server connected (`po-manager` in `mcp_servers.json`)

### Test data to create

#### 1. Parts list Google Sheet
- Create a Google Sheet called "Glacier Parts List" (or similar)
- Columns: Part Number, Part Name, Vendor Name, Description, Unit Price
- Add 3-5 fake parts, e.g.:
  - `GLC-001` | Thermal Housing | Acme Manufacturing | Aluminum housing for thermal unit | $45.00
  - `GLC-002` | Copper Heat Sink | ThermalTech Corp | Custom copper heat sink | $120.00
  - `GLC-003` | Sealing Gasket | Acme Manufacturing | High-temp silicone gasket | $8.50

#### 2. Vendor email (for Promaia to find)
- Send an email from yourself to yourself with subject like "RE: Acme Manufacturing - PO Follow Up"
- Body should mention Acme Manufacturing and include a fake vendor email like `orders@acme-mfg.example.com`
- This gives Promaia an email to find when searching for the vendor's contact

#### 3. Part specs folder in Google Drive
- Create a folder called "Part Specs" in Google Drive
- Inside, create subfolders per part (e.g., `GLC-001/`, `GLC-002/`)
- Add mock files:
  - `GLC-001-thermal-housing-v2.pdf` — a simple PDF (can be any PDF, just needs to exist)
  - `GLC-001-thermal-housing-v2.step` — create a text file, rename to .step (contents don't matter for the test)
  - `GLC-001-thermal-housing-v1.pdf` — older version (to test version selection)
- The agent should pick v2 as the latest by modified date

#### 4. PO Manager MCP server
- Already connected: `po-manager` at `https://mitchrp-PO.replit.app/mcp`
- This is Mitchell's test server — can be used for generating test POs
- Verify it's responsive before testing

## Test Flow

### Step 1: Trigger
In `maia chat`, tell the agent:
```
I need to reorder part GLC-001 (Thermal Housing), quantity 50.
Please generate a PO, find the spec, look up the vendor, and draft me an email.
```

### Step 2: Expected agent behavior

1. **Look up part** — Agent calls `sheets_read_range` on "Glacier Parts List" to find GLC-001 → gets vendor name "Acme Manufacturing"

2. **Generate PO** — Agent calls the PO manager MCP tool (e.g., `mcp__po-manager__generate_po` or similar) with part info and quantity → gets back PO number/file reference

3. **Download PO PDF** — If the PO manager uploads to Drive, agent calls `drive_search_files` to find the PO PDF, then `drive_download_file` to pull it into sandbox. If it returns the file directly, it lands in sandbox via the MCP output convention.

4. **Find part spec** — Agent calls `drive_search_files` for "GLC-001" in the Part Specs folder → finds multiple versions → selects latest (v2) by modified date → calls `drive_download_file` → spec PDF now in sandbox

5. **Find vendor email** — Agent calls `search_emails` with query `from:orders@acme-mfg.example.com OR subject:"Acme Manufacturing"` → finds the test email → extracts vendor email address

6. **Create draft email** — Agent calls `create_email_draft` with:
   - to: `orders@acme-mfg.example.com`
   - subject: "PO [number] - Thermal Housing Reorder"
   - body: Professional reorder email
   - attachment_paths: ["PO-xxx.pdf", "GLC-001-thermal-housing-v2.pdf"]

7. **Confirm** — Agent reports back with draft link or confirmation, ready for user to review and send

### Step 3: Verify

- [ ] PO generated successfully via MCP server
- [ ] Part spec downloaded from Drive (correct version selected)
- [ ] Vendor email found via Gmail search
- [ ] Draft email created with both PDFs attached
- [ ] `list_workspace_files` shows both files in sandbox
- [ ] Check Gmail drafts — draft exists with correct attachments
- [ ] No leaked MCP processes after chat exit

## Potential Issues

- **PO server return format**: Need to understand if it returns a file path, a Drive link, or base64 data. The agent may need to adapt.
- **Gmail source lookup**: `_ensure_gmail()` needs to find the gmail source by type, not by name "gmail". This was fixed but should be verified.
- **Drive folder ID**: Agent needs to know the Part Specs folder ID. Either search for it by name first, or provide it in the prompt.
- **Spec file format**: Agent needs to pick the PDF spec, not the .step/.stl. The prompt should guide this or the agent should ask.

## 1.2 Stretch: Remote MCP Server on Mitchell's Machine

Mitchell's PO Manager MCP server runs on his local machine, not a hosted service. To test the full flow:

1. **SSH into Mitchell's machine** and start the MCP server
2. **Verify connectivity** — confirm the Replit URL (`mitchrp-PO.replit.app/mcp`) is reachable from the Koii droplet
3. **If not hosted on Replit**: Set up a tunnel (e.g., ngrok, Cloudflare Tunnel) from Mitchell's machine to expose the MCP server, then update `mcp_servers.json` with the tunnel URL
4. **Alternative**: Ask Mitchell to deploy the PO Manager to a persistent host (Replit always-on, Railway, or a simple VPS)

The goal is for the Koii droplet (or local dev) to be able to call the PO Manager MCP tools during the test flow.

## After Testing

If the full flow works:
1. Save it as a workflow: "Turn what we just did into a repeatable workflow called glacier-part-reorder"
2. This becomes the first test of the workflow system (once implemented)
