SoundCloud Presence
A lightweight Windows desktop application that displays your SoundCloud listening history as Discord Rich Presence, including tracks played on your phone. No phone app, browser extension or automated browser is required during normal operation.

Version: 0.27
Features
- Automatically display song title, artist, album and artwork on Discord, with duplicate text removed.
- Detect playback history changes across devices, including iPhone playback.
- Estimated playback progress with an adjustable offset and optional end-of-track clearing.
- Configurable inactivity handling: keep the last song, hide the timer, display custom inactive text or clear the presence after a selected delay.
- Customizable music presence templates with text variables, conditional formatting, artwork, fallback images, tooltips and up to two link buttons.
- Live Discord-style preview, character counters, saved music layouts and template import/export.
- Adjustable polling intervals, manual refresh and pause/resume, with optional adaptive polling, rolling request counters, configurable request budget and rate-limit cooldowns.
- Automatic publishing when new playback information is detected, with duplicate payload suppression, manual publishing and reconnection support.
- Separate custom presence mode with configurable text, activity type, images, link button, elapsed timer and saved presets.
- Resizable dark desktop interface, optional system tray support and automatic Discord reconnection.
- Encrypted local access-token storage using Windows DPAPI, expiration monitoring and warnings.
- Filterable diagnostic logs, configurable log retention in memory, optional persistent logging, settings export and optional performance optimizations. No automatic Windows startup registration.
Requirements
- Windows, with Python 3.8 or newer and Tkinter installed. The project was developed for Python 3.8 on Windows 7; legacy OS and Discord client compatibility is not guaranteed.
- A running Discord desktop client with activity sharing enabled.
- A SoundCloud account and your own valid access token for music.
- Your own Discord developer application ID. The same ID can be used for music and custom presence.
Installation
1. Download and extract the project files into a folder of your choice.
2. Install Python if necessary, including Tcl/Tk support, and ensure python is available on your PATH.
3. Double-click Install.bat to install the minimum required dependency, PyPresence.
4. Double-click SoundCloudPresence.pyw to launch the application. If using a version-numbered .pyw file, launch that file instead.
Alternatively, open Command Prompt in the project folder and run:
python -m pip install -r requirements.txt
For optional system tray support, install the additional dependencies:
python -m pip install -r requirements-tray.txt
The minimum dependency is pypresence==4.3.0. Tray support additionally uses pystray==0.19.5 and Pillow==9.5.0. The installer does not install optional tray packages or register the application for Windows startup.
First-time setup
Discord
1. Open the Discord Developer Portal.
2. Create a new application and copy its Application ID under General Information. A bot token or client secret is not needed.
3. Open the Accounts tab in SoundCloud Presence and enter the ID under Discord application.
4. Open the Discord desktop client and enable activity sharing in its privacy settings.
5. In Accounts, click Connect / Switch. Use Save preferences to save your connection preferences.
SoundCloud
1. Open the Accounts tab and select Help: Get token for the current manual authentication instructions.
2. Sign into your own SoundCloud account using your normal browser and follow the in-app instructions to obtain your current access token. Never share credentials or copy the refresh token.
3. Enter the access token in Accounts, optionally enable Remember verified token securely on this PC, and click Connect / Save.
4. In Now playing, click Start / Resume.
The application checks the token's embedded expiration timestamp when available. Access tokens must be renewed manually; the program does not automatically sign in or refresh credentials. Firefox can be closed while a valid token is in use.
Configuration
Tab	Purpose
Now playing	Current song, estimated playback, start/pause polling, refresh, publish or clear activity, switch modes
Accounts	SoundCloud authentication, token expiration, Discord application ID, connection and reconnection preferences
Appearance	Basic music layout, custom templates, live preview, artwork, buttons and saved layouts
Custom presence	Independent custom activity editor, images, link, timer and saved presets
Settings	Playback, inactivity, polling, request protection, desktop behavior, diagnostics and performance
Activity log	Filterable request results, errors, Discord acknowledgment timings and logging preferences


Appearance and templates
Use Appearance > Basic layout for simple display options, including the activity name, artwork, album information and SoundCloud link button.

For more control, open Appearance > Template editor. You can customize the activity name, first and second lines, Discord status text, large and small images, hover text, fallback artwork and two optional link buttons. Enable Use custom music template and select Apply template to publish the layout. The preview updates as you edit without publishing those edits. It can show music, basic music, inactive music or custom presence, using the current track or sample metadata. Artwork is represented by placeholders, and Discord may display the final activity differently on desktop and mobile.

The following variables can be used in template fields:
Variable	Value
%title%	Song title
%artist%	Artist name
%album%	Album name
%distinct_album%	Album name, only when different from the song title and artist
%duration%	Song duration, formatted as minutes and seconds
%artwork%	Track artwork URL
%url%	Track link
%app%	Configured activity name


Variables can be combined with normal text. Optional groups in square brackets are omitted when any variable inside the group is empty. For example, %artist%[ - %distinct_album%] only includes the separator and album when there is a distinct album name. Identical music activity text is displayed once, with the higher-priority field retained.

Music layouts can be saved, loaded, renamed, duplicated, deleted, imported or exported as JSON files. Layout exports contain appearance settings, not account credentials. Review custom text and URLs before sharing them. Use Revert to discard unapplied changes or Restore defaults to restore the default template fields.
Playback and inactivity
Open Settings > Playback & inactivity to configure the estimated playback progress bar, start offset and end-of-track behavior.

The inactivity delay is measured from the estimated end of the song, not the moment playback is paused. You can enter a custom delay in seconds, minutes or hours.
Inactivity action	Behavior
Keep last song	Retain the last displayed activity
Hide timer	Remove the playback timestamps while retaining the song information
Mark inactive	Remove the timer and display customizable inactive text
Clear presence	Remove the music activity from Discord


If immediate end-of-track clearing is enabled, its grace period takes priority for clearing. Newly detected playback restores the normal music presence. Inactivity is an estimate; it does not indicate whether the SoundCloud player is actually paused.
Custom presence
The Custom presence tab can be edited without interrupting music presence. Select Enable custom mode, or select Switch to custom presence from Now playing, and accept the confirmation. SoundCloud polling is suspended in that mode. Configure your activity and select Publish custom. You can save presets and reuse them later. Select Return to music to switch back.

Images must be public HTTPS URLs or asset keys from your Discord application; local file paths are not supported by Discord RPC. Custom presence has its own optional elapsed timer, separate from the estimated music progress bar.
Logging and performance
The Activity log can be filtered by new tracks, unchanged polls, Discord, account messages, errors and other messages. These filters affect the on-screen log only. Disable Hide unchanged polls if you want to record every successful poll. Persistent file logging and the number of entries retained in memory can also be configured.

Optional performance settings are available under Settings > Performance. Optimizations for skipping unchanged GUI values and repeated inactive Discord checks are disabled by default. Preview rendering can be restricted to when Appearance is visible. The normal polling and publishing behavior does not require experimental optimizations.
Polling and limitations
The program uses the undocumented api-v2.soundcloud.com/me/play-history/tracks?limit=1 endpoint. It is not a live playback API. Track changes may appear after a delay, and pause, stop, seeking and actual playback position cannot be detected reliably. The progress bar is estimated using the history timestamp and duration. Discord controls the final activity layout and may render it differently on desktop and mobile.

The default polling interval is 20 seconds, adaptive polling is enabled, and the default local rolling 24-hour budget is 8,640 requests. Polling intervals can be customized using presets or manually entered durations. Five-second polling is experimental. The actual server-side quota for this endpoint is unknown; the local budget does not represent a verified safe SoundCloud limit. Respect HTTP 429 cooldowns and stop on access restrictions. Do not attempt to bypass service restrictions. The endpoint and Discord RPC behavior may change without notice.
Privacy and local storage
The program stores runtime files in %APPDATA%\SoundCloudPresence\ on Windows:
File	Contents
account.dpapi	Access token encrypted for the current Windows user
settings.json	Application ID, preferences, appearance templates and saved presets
activity.log / activity.old.log	Local activity and diagnostic logs
request_usage.json	Request timestamps and cooldown information


The access token is sent to SoundCloud for authenticated history requests. Track metadata, artwork URLs and optional links are shared with Discord through its local RPC connection. Public image hosts may receive image requests. Logs, exported settings and layouts may contain personal listening information, custom text or URLs; inspect before sharing. Do not upload these runtime files or credentials. Forget account removes the locally saved credential, but does not revoke the browser session.
Troubleshooting
- Application does not open: Run Install.bat and verify that Python, Tkinter and PyPresence are installed in the Python environment used to launch the application.
- HTTP 401: The token may have expired or been revoked. Obtain a fresh access token using the in-app help.
- HTTP 403: Stop polling and verify normal access to SoundCloud before attempting to reconnect.
- HTTP 429: Wait for the cooldown. Increase the polling interval if necessary.
- HTTP 200 with unchanged track: The server has not returned a different history entry. Disable Hide unchanged polls and enable the unchanged-polls log filter to see every successful poll.
- Discord not updating: Check the Discord connection in Accounts, activity privacy settings and Activity log. Publish latest resends the detected track without requesting SoundCloud again.
- Playback progress is inaccurate: Check the estimated timeline and start offset. SoundCloud history does not provide the current player position.
- Template changes not appearing: Enable Use custom music template and select Apply template. The live preview is not a published activity.
- Tray unavailable: Install the optional tray dependencies.
Contributing
Bug reports and suggestions are welcome. Include the application version, Python version, Windows version, reproduction steps and redacted logs. Never include tokens, cookies, passwords or private configuration files.
Disclaimer
This is an independent, unofficial project, not affiliated with or endorsed by SoundCloud or Discord. Use only with accounts you are authorized to access and in accordance with the services' applicable terms. The undocumented endpoint can stop working at any time. No guarantee is made about account access or rate limits
