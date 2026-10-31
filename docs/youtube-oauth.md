# Connect YouTube: create `client_secret.json`

social2shorts uploads to **your own channel** through the YouTube Data API. Google only allows
that after you create an **OAuth client** in your own Google Cloud project and give the program
the client file. This page walks you through it, one click at a time, with direct links.

!!! abstract "What you will have at the end"
    - `secrets/client_secret.json`: the client file you download from Google (steps 1 to 7).
    - `secrets/token.json`: the permission you grant once in your browser (step 8). It is
      renewed automatically afterwards, so unattended runs keep working.

**Time:** about 10 minutes. **Cost:** nothing. **You need:** a web browser and the Google account
that owns (or manages) the YouTube channel you want to upload to.

## At a glance

| # | What | Where (direct link) |
|---|---|---|
| 1 | Create a Google Cloud project | [console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate){ target=_blank } |
| 2 | Enable *YouTube Data API v3* | [API library page](https://console.cloud.google.com/apis/library/youtube.googleapis.com){ target=_blank } |
| 3 | Set up the Google Auth Platform (app name, audience) | [Overview](https://console.cloud.google.com/auth/overview){ target=_blank } |
| 4 | Add the two YouTube scopes | [Data Access](https://console.cloud.google.com/auth/scopes){ target=_blank } |
| 5 | Add yourself as a test user, then **Publish app** | [Audience](https://console.cloud.google.com/auth/audience){ target=_blank } |
| 6 | Create the **Desktop app** client and download its JSON | [Clients](https://console.cloud.google.com/auth/clients){ target=_blank } |
| 7 | Save the file as `secrets/client_secret.json` | your computer |
| 8 | Authorize once | `uv run social2shorts auth` |
| 9 | Check with one unlisted upload | `uv run social2shorts run --limit 1 --privacy unlisted` |

!!! tip "Use the same Google account everywhere"
    Sign in to the console with the account that owns the channel. Then the account you pick in
    step 8 is already the right one. If your channel is a **Brand Account**, you will choose it
    in step 8.

!!! info "If a link opens a page you do not expect"
    Google moves console pages around from time to time. Every step below also names the menu
    path. In the console, open the left menu (☰) and look for **Google Auth Platform**; its
    sub-pages are *Overview, Branding, Audience, Clients, Data Access, Verification Center*.
    The older path *APIs & Services → OAuth consent screen / Credentials* still redirects there.

---

## Step 1: Create a project

1. Open [console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate){ target=_blank }.
   Accept the terms if Google asks.
2. **Project name:** `social2shorts` (any name works).
3. **Location:** keep *No organization*, unless your Google account belongs to a company
   organization. Then pick the folder you are allowed to use.
4. Click **Create**. A notification appears at the top right: *Creating project…*, then
   *Create Project: social2shorts*.
5. **Check that the new project is selected.** The project name sits next to the Google Cloud logo
   at the top left. If it shows another project, click it and choose `social2shorts`.

!!! warning "Every later page works on the selected project"
    If you see an old project name there, you will enable the API and create the client in the
    wrong place. Check the selector at each step.

## Step 2: Enable the YouTube Data API v3

1. Open the [YouTube Data API v3 page](https://console.cloud.google.com/apis/library/youtube.googleapis.com){ target=_blank }.
   (Menu path: ☰ → *APIs & Services* → *Library* → search `YouTube Data API v3`.)
2. Make sure the project selector still says `social2shorts`.
3. Click **Enable**.
4. You land on the API overview with charts for *Traffic*, *Errors* and *Latency*. That means it
   worked. If the button says **Manage** instead of **Enable**, the API was already on.

## Step 3: Set up the Google Auth Platform

This is the "consent screen" that Google shows when you authorize the program. It only ever
shows to you.

1. Open [Google Auth Platform → Overview](https://console.cloud.google.com/auth/overview){ target=_blank }.
   (Menu path: ☰ → *Google Auth Platform*.)
2. If you see *Google Auth Platform not configured yet*, click **Get started**. A short wizard
   asks, in this order:

    | Wizard page | What to enter |
    |---|---|
    | **App Information** | *App name*: `social2shorts`. *User support email*: your own address. Click **Next**. |
    | **Audience** | Choose **External**. (*Internal* only exists for Google Workspace organizations.) Click **Next**. |
    | **Contact Information** | Your email address. Click **Next**. |
    | **Finish** | Tick *I agree to the Google API Services: User Data Policy*, click **Continue**, then **Create**. |

3. Leave the **logo** empty: Google asks for a verification as soon as an app in production has a
   logo. Leave the *App domain* and *Authorized domains* fields empty for now. You need them only
   to publish the app, and step 5 says exactly what to enter.

!!! note "Where to change these later"
    [Branding](https://console.cloud.google.com/auth/branding){ target=_blank } holds the app name
    and emails. [Audience](https://console.cloud.google.com/auth/audience){ target=_blank } holds the
    user type, test users and publishing status.

## Step 4: Add the two YouTube scopes

A scope is a permission. social2shorts needs exactly two.

1. Open [Google Auth Platform → Data Access](https://console.cloud.google.com/auth/scopes){ target=_blank }.
2. Click **Add or remove scopes**.
3. In the panel that opens, scroll to **Manually add scopes** and paste these two lines:

    ```text
    https://www.googleapis.com/auth/youtube.upload
    https://www.googleapis.com/auth/youtube.force-ssl
    ```

4. Click **Add to table**, then **Update** at the bottom of the panel, then **Save** on the page.

| Scope | What social2shorts uses it for | What Google shows you at consent |
|---|---|---|
| `youtube.upload` | Upload videos. | *Manage your YouTube videos* |
| `youtube.force-ssl` | Change a video's visibility (for example unlisted → public) and run the post-upload claim check. | *See, edit, and permanently delete your YouTube videos, ratings, comments and captions* |

!!! note "Sensitive scopes"
    Google may list these under *Your sensitive scopes*. That is expected for YouTube scopes. It is
    the reason for the warning screen in step 8, and nothing to worry about for your own account.

## Step 5: Add yourself as a test user, then publish the app

New apps start in **Testing**. In that state Google **expires your permission every 7 days**, which
silently breaks scheduled runs. So you add yourself as a test user (so the first login works), and
then you move the app to **In production**.

1. Open [Google Auth Platform → Audience](https://console.cloud.google.com/auth/audience){ target=_blank }.
2. Under **Test users**, click **+ Add users**, type the Google address that owns the channel, and
   click **Save**.
3. At the top, find **Publishing status: Testing** and click **Publish app**.
4. A dialog explains that the app will be available to any user with a Google Account. Click
   **Confirm**.
5. The status now reads **In production**.

!!! warning "If **Publish app** is greyed out"
    The button is disabled with *To publish your app, you must finish its configuration on the
    Branding page*. Google's help states that the **App domain** links are *required for all
    external production apps*
    ([Manage OAuth App Branding](https://support.google.com/cloud/answer/10311615){ target=_blank }).
    On [Branding](https://console.cloud.google.com/auth/branding){ target=_blank }, fill:

    | Field | Value for this project's site |
    |---|---|
    | **App home page** | `https://m-elkhou.github.io/social2shorts/` |
    | **Privacy policy link** | `https://m-elkhou.github.io/social2shorts/privacy/` |
    | **Terms of service link** | `https://m-elkhou.github.io/social2shorts/terms/` |
    | **Authorized domains** → **+ Add domain** | `m-elkhou.github.io` |

    Then click **Save** at the bottom of the page and go back to *Audience*. If you run your own
    fork, use the same pages on your own GitHub Pages site
    ([Privacy policy](privacy.md), [Terms of service](terms.md)).

    Google's help requires these links for *verification*; it does not say it checks them when you
    publish. If the console rejects a link or the domain, use pages on a domain you own. Do **not**
    upload a logo (see step 3).

!!! tip "You can try the tool before publishing"
    Steps 6 to 9 work in **Testing** mode for the test user you added in this step. The only
    difference is that Google expires the permission after 7 days. Publish the app before you rely
    on scheduled, unattended runs.

!!! info "Do I need to submit the app for verification?"
    No. An unverified app in production works for your own account. The only consequences are the
    "Google hasn't verified this app" warning that you click through once (step 8), and a cap of 100
    new users for the whole app. Google may mention verification after you publish. You can ignore it.

## Step 6: Create the OAuth client and download the JSON

1. Open [Google Auth Platform → Clients](https://console.cloud.google.com/auth/clients){ target=_blank }.
   (Older path: [APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials){ target=_blank }
   → **+ Create credentials** → **OAuth client ID**.)
2. Click **+ Create client**.
3. **Application type:** choose **Desktop app**.

    !!! danger "It must be *Desktop app*"
        Not *Web application*. A web client has a different JSON layout and fails with
        `redirect_uri_mismatch`.

4. **Name:** `social2shorts desktop` (only you see it). Click **Create**.
5. A dialog titled **OAuth client created** shows the *Client ID* and *Client secret*. Click
   **Download JSON** now, then **OK**.

!!! warning "Download it right away"
    Google may not show the client secret again after you close this dialog. If you lose the file,
    open the client in the list, click **Add secret**, and download the new one.

## Step 7: Save the file as `secrets/client_secret.json`

Google names the download something like `client_secret_123456789012-abc….apps.googleusercontent.com.json`
and puts it in your *Downloads* folder. social2shorts expects this exact path (set by
`[youtube] client_secrets` in `config.toml`):

```text
<your clone of social2shorts>/secrets/client_secret.json
```

The `secrets/` folder is in `.gitignore`, so the file is never committed.

=== "Windows (PowerShell)"

    ```powershell
    cd C:\path\to\social2shorts
    New-Item -ItemType Directory -Force secrets | Out-Null
    Move-Item "$HOME\Downloads\client_secret_*.json" secrets\client_secret.json
    ```

=== "macOS / Linux"

    ```bash
    cd /path/to/social2shorts
    mkdir -p secrets
    mv ~/Downloads/client_secret_*.json secrets/client_secret.json
    ```

If you downloaded the file more than once, the wildcard matches several files and the command
fails. Move the newest one by its full name.

**Check the file without printing any secret:**

```bash
uv run python -c "import json; print(list(json.load(open('secrets/client_secret.json'))))"
```

| You see | Meaning |
|---|---|
| `['installed']` | Correct: it is a Desktop app client. Continue. |
| `['web']` | Wrong client type. Delete that client and redo step 6 with **Desktop app**. |
| An error | The file is not the downloaded JSON. Download it again. |

!!! danger "Treat the file like a password"
    Do not commit it, email it, or paste it into a chat or an issue. If it leaks, open
    [Clients](https://console.cloud.google.com/auth/clients){ target=_blank }, delete that client, and
    redo step 6.

## Step 8: Authorize once

```bash
uv run social2shorts auth
```

If the file is missing or misnamed, the command stops with
`OAuth client file secrets/client_secret.json is missing…`. Fix the path and run it again.

Otherwise your browser opens. If it does not, the terminal prints a long link: copy it into a
browser on the same computer. Then:

1. **Choose your account**, the one that owns the channel. If the channel is a Brand Account, a
   second page asks you to **choose the channel**.
2. You see **Google hasn't verified this app**. This is expected for a personal app. Click
   **Advanced**, then **Go to social2shorts (unsafe)**. ("unsafe" only means "not reviewed by
   Google". It is your own app.)
3. The permission page lists two items, one per scope from step 4. **Tick every box** (or *Select
   all*), then click **Continue**.
4. The browser shows **The authentication flow has completed. You may close this window.**
5. The terminal prints `YouTube authorization OK; token saved to secrets/token.json.`

`secrets/token.json` now holds a long-lived permission. social2shorts renews it by itself, so
you do not repeat this unless you revoke access or the app was left in *Testing* (see below).

## Step 9: Check it with one unlisted upload

```bash
uv run social2shorts status
uv run social2shorts run --source catalog --limit 1 --privacy unlisted
```

Open [YouTube Studio → Content](https://studio.youtube.com){ target=_blank } and look at the
*Visibility* column of the new video. See the next section if it says **Private**.

---

## Quota and limits (checked on 2026-10-31)

| Fact | Value | Source |
|---|---|---|
| Default daily quota (most calls) | 10,000 units, reset at midnight Pacific time | [Quota cost table](https://developers.google.com/youtube/data/determine_quota_cost){ target=_blank } |
| `videos.insert` (an upload) | Its own bucket: **100 calls per day**, 1 unit per call | same page, updated 2026-10-15 |
| History | Uploads cost about 1,600 units until 2025-12-04. On 2026-06-01 `videos.insert` and `search.list` moved to their own buckets. | [Revision history](https://developers.google.com/youtube/data/revision_history){ target=_blank } |

!!! warning "social2shorts defaults still use the old cost"
    `config.toml` has `upload_cost_units = 1600`, which makes the program plan for about 6 uploads
    a day. That was the real limit before December 2025. Check your own numbers on the
    [Quotas page](https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas){ target=_blank }
    and adjust `[youtube] upload_cost_units` and `daily_quota_units` if they differ.

### Will my uploads be forced to private?

Google's official revision history (2026-10-15) still says that videos uploaded through an
**unaudited** API project created after 28 July 2020 are locked to **private**. A Google comment on
the public [issue tracker](https://issuetracker.google.com/issues/572863050){ target=_blank } dated
13 October 2026 says the restriction no longer applies. That comment is not in the official
documentation, so treat the question as **open until you test it**:

1. Do the unlisted upload of step 9.
2. In YouTube Studio, check the *Visibility* column. social2shorts also logs a warning when YouTube
   applied a different privacy than requested, and it stores the privacy that was really applied.
3. If the video is **Private** and you need it public, submit the
   [YouTube API Services audit and quota extension form](https://support.google.com/youtube/contact/yt_api_form){ target=_blank }.
   Google gives no review time.

## Troubleshooting

| What you see | Likely cause | Fix |
|---|---|---|
| `Error 403: access_denied` and "has not completed the Google verification process" | The app is in *Testing* and your address is not a test user. | [Audience](https://console.cloud.google.com/auth/audience){ target=_blank } → **+ Add users**, or publish the app (step 5). |
| "Google hasn't verified this app" | Normal for a personal app. | **Advanced** → **Go to social2shorts (unsafe)** (step 8). |
| **Publish app** is greyed out | The Branding page lacks the home page, privacy policy and terms links, or was not saved. | Fill them, add their domain under *Authorized domains*, click **Save** (step 5). |
| `redirect_uri_mismatch` | The client is a *Web application*. | Delete it and recreate it as **Desktop app** (step 6). |
| `invalid_client` or "The OAuth client was not found" | Wrong or old JSON, wrong project, or the secret was rotated. | Download the JSON again from [Clients](https://console.cloud.google.com/auth/clients){ target=_blank }. |
| `invalid_grant: Token has been expired or revoked` | The app is still in *Testing* (7-day limit), you revoked access, or the token was unused for 6 months. | Publish the app (step 5), delete `secrets/token.json`, run `auth` again. |
| `Access blocked` or `invalid_scope` | The API is not enabled, or the scopes are missing. | Redo steps 2 and 4. |
| The video went to the wrong channel | You picked the wrong Brand Account channel in step 8. | Delete `secrets/token.json`, run `auth`, and pick the right channel. |
| `quotaExceeded` or `uploadLimitExceeded` | A daily cap was reached. | Wait for midnight Pacific time. See the quota section above. |
| `auth` says the client file is missing | Wrong folder or file name. | The path must be exactly `secrets/client_secret.json`, relative to the folder where you run the command. |

Google's exact error wording changes now and then. The causes above stay the same.

## Review, revoke or rotate access

| I want to… | Do this |
|---|---|
| See or remove what the program may do | [myaccount.google.com/permissions](https://myaccount.google.com/permissions){ target=_blank } → *social2shorts* → **Delete all connections**. Then delete `secrets/token.json`. |
| Replace a leaked or lost client secret | [Clients](https://console.cloud.google.com/auth/clients){ target=_blank } → open the client → **Add secret**, download the new JSON, then disable the old secret. |
| Start again from scratch | Delete the client in [Clients](https://console.cloud.google.com/auth/clients){ target=_blank }, delete `secrets/client_secret.json` and `secrets/token.json`, and redo steps 6 to 8. |

## Sources

- [Using OAuth 2.0 for installed applications (YouTube)](https://developers.google.com/youtube/data/guides/auth/installed-apps){ target=_blank }, updated 2026-10-30
- [OAuth 2.0 overview and refresh-token rules](https://developers.google.com/identity/protocols/oauth2){ target=_blank }, updated 2026-10-29
- [Quota cost of each API call](https://developers.google.com/youtube/data/determine_quota_cost){ target=_blank } and [revision history](https://developers.google.com/youtube/data/revision_history){ target=_blank }, updated 2026-10-15
- [Manage OAuth app data (Google Auth Platform)](https://support.google.com/cloud/answer/10311615){ target=_blank }
- [Unverified apps](https://support.google.com/cloud/answer/7454865){ target=_blank } and [Audience and publishing status](https://support.google.com/cloud/answer/15549945){ target=_blank }

Next: [Getting started](getting-started.md), then [Publishing and quota](use-cases/publishing.md) and
[OAuth expiry and alerts](use-cases/alerts.md).
