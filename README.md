# SoundCloud Presence

A lightweight Windows desktop application that displays your SoundCloud listening history as Discord Rich Presence, including tracks played on your phone. No phone app, browser extension or automated browser is required during normal operation.

**Version:** 0.21 · **Status:** Experimental / unofficial

## Features

- Automatically display song title, artist, album and artwork on Discord.
- Detect playback history changes across devices, including iPhone playback.
- Estimated playback progress with adjustable offset and optional automatic clearing.
- Adjustable polling intervals (5–120 seconds), manual refresh and pause/resume.
- Optional adaptive polling, rolling request counters, configurable request budget and rate-limit cooldowns.
- Publish automatically only when track information or the playback timestamp changes; manual publishing and reconnection are also supported.
- Separate custom presence mode with configurable text, activity type, images, link button, elapsed timer and saved presets.
- Dark desktop interface, optional system tray support and automatic Discord reconnection.
- Encrypted local access-token storage using Windows DPAPI, expiration monitoring and warnings.
- Diagnostic logs and settings export. No automatic Windows startup registration.

## Requirements

- Windows, with Python 3.8 or newer and Tkinter installed. The project was developed for Python 3.8 on Windows 7; legacy OS and Discord client compatibility is not guaranteed.
- A running Discord desktop client with activity sharing enabled.
- A SoundCloud account and your own valid access token for music.
- Your own Discord developer application ID.

## First-time setup

### Discord

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a new application and copy its **Application ID** under General Information. A bot token or client secret is not needed.
3. In SoundCloud Presence, open **Settings**, enter the ID, and select **Save & apply**.
4. Open Discord and enable activity sharing in its privacy settings.
5. In **Now playing**, click **Connect / Switch**.

### SoundCloud

1. Open the **Account** tab and select **Help: Get token** for the current manual authentication instructions.
2. Sign into your own SoundCloud account using your normal browser and follow the in-app instructions to obtain your current access token. Never share credentials or copy the refresh token.
3. Enter the access token in the Account tab, optionally enable **Remember verified token securely on this PC**, and click **Connect / Save**.
4. In **Now playing**, click **Start / Resume**.

The application checks the token's embedded expiration timestamp. Access tokens must be renewed manually; the program does not automatically sign in or refresh credentials. Firefox can be closed while a valid token is in use.

## Configuration

| Tab | Purpose |
|---|---|
| Now playing | Start, pause, refresh, connect Discord, publish or clear activity, switch modes |
| Account | Verify, remember or forget your access token; expiration information and help |
| Settings | Polling interval, adaptive polling, application ID and presence layout |
| Playback | Estimated timeline, start offset and optional end-of-track clearing |
| Custom presence | Custom text, image URLs or asset keys, link, timer and presets |
| Utilities | Request budget, diagnostics, tray support, reconnection and settings export |
| Activity log | Request results, errors and Discord acknowledgment timings |

To use custom presence, select **Switch to custom presence** from Now playing and accept the confirmation. SoundCloud polling is suspended in that mode. Select **Return to music** to switch back. Images must be public HTTPS URLs or asset keys from your Discord application; local file paths are not supported by Discord RPC.

## Polling and limitations

The program uses the undocumented `api-v2.soundcloud.com/me/play-history/tracks?limit=1` endpoint. It is not a live playback API. Track changes may appear after a delay, and pause, stop, seeking and actual playback position cannot be detected reliably. The progress bar is estimated using the history timestamp and duration.

The default polling interval is 20 seconds, adaptive polling is enabled, and the default local rolling 24-hour budget is 8,640 requests. Five-second polling is experimental. The actual server-side quota for this endpoint is unknown; the local budget does not represent a verified safe SoundCloud limit. Respect HTTP 429 cooldowns and stop on access restrictions. Do not attempt to bypass service restrictions. The endpoint and Discord RPC behavior may change without notice.

## Privacy and local storage

The program stores runtime files in `%APPDATA%\SoundCloudPresence\` on Windows:

| File | Contents |
|---|---|
| `account.dpapi` | Access token encrypted for the current Windows user |
| `settings.json` | Application ID, preferences and custom presets |
| `activity.log` / `activity.old.log` | Local activity and diagnostic logs |
| `request_usage.json` | Request timestamps and cooldown information |

The access token is sent to SoundCloud for authenticated history requests. Track metadata, artwork URLs and optional links are shared with Discord through its local RPC connection. Public image hosts may receive image requests. Logs and exported settings may contain personal listening information or custom text; inspect before sharing. Do not upload these runtime files or credentials. Forget account removes the locally saved credential, but does not revoke the browser session.

## Troubleshooting

- **HTTP 401:** The token may have expired or been revoked. Obtain a fresh access token using the in-app help.
- **HTTP 403:** Stop polling and verify normal access to SoundCloud before attempting to reconnect.
- **HTTP 429:** Wait for the cooldown. Increase the polling interval if necessary.
- **HTTP 200 with unchanged track:** The server has not returned a different history entry. Disable Hide unchanged polling messages to see every successful poll.
- **Discord not updating:** Check the Discord connection, activity privacy settings and Activity log. Publish latest resends the detected track without requesting SoundCloud again.
- **Tray unavailable:** Install the optional tray dependencies.

## Contributing

Bug reports and suggestions are welcome. Include the application version, Python version, Windows version, reproduction steps and redacted logs. Never include tokens, cookies, passwords or private configuration files.

## Disclaimer

This is an independent, unofficial project, not affiliated with or endorsed by SoundCloud or Discord. Use only with accounts you are authorized to access and in accordance with the services' applicable terms. The undocumented endpoint can stop working at any time. No guarantee is made about account access or rate limits.
