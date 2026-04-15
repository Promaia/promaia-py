# Google OAuth Setup for Pilot Deployments

## Problem

The Promaia OAuth proxy's Google Cloud project is under `promaia.com`. Pilot users on external domains (e.g., `@endwaste.io`) are treated as external users, which means:

- In **testing mode**: refresh tokens expire after 7 days, requiring re-auth weekly
- In **production mode**: requires Google's full verification process (privacy policy, security review for Gmail restricted scope, days-to-weeks timeline)

Google has no "trusted partner domain" mechanism for OAuth consent screens.

## Solution: Pilot creates their own Internal Google Cloud project

The pilot org creates a Google Cloud project under their own Workspace domain and sets it to **Internal**. This gives:

- No token expiry (refresh tokens are permanent)
- No verification needed
- No scope review (internal apps skip it entirely)
- Only works for users within their org (which is the desired behavior)

The pilot uses the existing **"Use your own Google Cloud project"** auth mode in `maia setup`, which:

- Prompts for their Client ID and Client Secret
- Uses the Promaia proxy as a passthrough (handles redirect only)
- Token refresh goes directly to Google via `refresh_google_token_direct()` — no proxy dependency

## Steps for the Pilot (e.g., Mitchell @ endwaste.io)

### 1. Create Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Sign in with org account (e.g., `mitchell@endwaste.io`)
3. Create new project (e.g., "Promaia - Glacier")

### 2. Enable APIs

In the project, go to **APIs & Services > Library** and enable:

- **Gmail API**
- **Google Calendar API**
- **Google Sheets API**
- **Google Drive API**

### 3. Configure OAuth Consent Screen

Go to **APIs & Services > OAuth consent screen**:

1. User type: **Internal**
2. App name: "Promaia" (or any name)
3. Support email: their email
4. Contact email: their email
5. Click through — no scopes need to be listed (internal apps skip review)

### 4. Create OAuth Credentials

Go to **APIs & Services > Credentials**:

1. Click **Create Credentials > OAuth client ID**
2. Application type: **Web application**
3. Add authorized redirect URI: `https://oauth.promaia.workers.dev/auth/google/callback`
4. Copy the **Client ID** and **Client Secret**

### 5. Configure in Promaia

Run `maia auth configure google --account their@email.com` and select **"Use your own Google Cloud project"**. Enter the Client ID and Client Secret when prompted.

## Alternative: Use proxy credentials directly

If we want to avoid per-pilot Google projects, we could:

- **Replace** the proxy's Google credentials with the pilot's (breaks other orgs)
- **Support per-deployment credentials** in the proxy (route by pilot/org)
- **Publish the app to production** (requires Google verification — days to weeks, needs privacy policy and security review for Gmail restricted scope)

The per-pilot Internal project approach is recommended for now as it requires zero proxy changes and zero verification.
