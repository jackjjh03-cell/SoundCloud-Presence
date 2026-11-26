SoundCloud Presence
A lightweight Windows desktop application that displays your SoundCloud listening activity on Discord using Rich Presence. It works with tracks played across devices, including mobile playback, without requiring a phone app, browser extension or automated browser during normal use.

Version: 0.32
Features
- Automatic song title, artist, album and artwork updates on Discord.
- Cross-device listening history detection, including iPhone playback.
- Estimated playback progress with an adjustable start offset and configurable inactivity handling.
- Customizable music layouts with formatting variables, conditional text, duplicate metadata filtering, artwork, tooltips and up to two link buttons.
- Live Discord-style preview, character counters, saved layouts and template import/export.
- Adjustable polling intervals, adaptive polling, manual refresh, request counters, a configurable request budget and rate-limit handling.
- Automatic publishing when playback information changes, with redundant updates suppressed.
- Independent custom presence mode with configurable activity types, text, images, a link button, an optional timer and saved presets.
- Resizable dark interface, optional system tray support and automatic Discord reconnection.
- Encrypted local token storage, token expiration monitoring, filterable logs, settings export and configurable performance options.
Requirements
- Windows with Python 3.8 or newer and Tkinter installed.
- Discord desktop client with activity sharing enabled.
- A SoundCloud account and valid access token for music presence.
- A Discord developer application ID. The same ID can be used for music and custom presence.
The application was developed using Python 3.8 on Windows 7. Compatibility with legacy operating systems and Discord clients is not guaranteed.
Installation
1. Download and extract the project files into a folder of your choice.
2. Install Python if necessary, including Tcl/Tk support, and make sure Python is available on your PATH.
3. Run Install.bat to install the required dependency.
4. Launch SoundCloudPresence.pyw, or the version-numbered .pyw file included in your release.
Alternatively, open Command Prompt in the project folder and run:
python -m pip install -r requirements.txt
For optional system tray support:
python -m pip install -r requirements-tray.txt
The required dependency is pypresence==4.3.0. System tray support also uses pystray==0.19.5 and Pillow==9.5.0. The installer only installs the required dependency and does not register the application for Windows startup.
First-time setup
Discord
1. Open the Discord Developer Portal.
2. Create an application and copy its Application ID from General Information. A bot token or client secret is not required.
3. Open Accounts in SoundCloud Presence and enter the ID under Discord application.
4. Open the Discord desktop client and enable activity sharing in its privacy settings.
5. Select Connect / Switch. Use Save preferences to save your connection preferences.
SoundCloud
1. Open Accounts and select Help: Get token for authentication instructions.
2. Sign into your own SoundCloud account in your browser and follow the in-app instructions to obtain your access token.
3. Enter the token, optionally enable Remember verified token securely on this PC, and select Connect / Save.
4. Open Now playing and select Start / Resume.
Access tokens must be renewed manually when they expire. The application monitors the token's expiration timestamp when available and does not automate browser sign-in or token renewal. The browser can be closed while a valid token is in use. Never share your access token or refresh token.
Application overview
Tab	Purpose
Now playing	Current track, estimated playback, polling, manual refresh, publishing and mode switching
Accounts	SoundCloud authentication, Discord application ID and connection preferences
Appearance	Music layouts, templates, live preview, artwork, buttons and saved presets
Custom presence	Custom activity editor and saved presets
Settings	Playback, inactivity, polling, request protection, desktop behavior and performance
Activity log	Request results, connection events, errors, timings and logging preferences


Appearance and templates
Appearance > Basic layout contains simple display options for the activity name, album information, artwork and SoundCloud link button.

For more control, use Appearance > Template editor. The editor supports a built-in layout and custom templates with configurable activity names, body text, status text, large and small images, hover text, fallback artwork and up to two link buttons.

The built-in music layout uses the song title in both the activity heading and the main text, the artist on the secondary line and the album as cover artwork text. Duplicate album information is omitted. Discord may display the cover text as an additional line or as an artwork tooltip, depending on the client.

Select the premade layout and click Load layout, or create your own. Enable Use custom music template and select Apply template to publish your changes. The live preview updates while editing without sending changes to Discord. It uses artwork placeholders and supports music, inactive music and custom presence previews.
Template variables
Variable	Value
%title%	Song title
%artist%	Artist name
%album%	Album name
%distinct_album%	Album name when different from the title and artist
%duration%	Track duration in minutes and seconds
%artwork%	Track artwork URL
%url%	Track link
%app%	Configured activity name


Variables can be combined with normal text. Optional groups in square brackets are included only when all variables in the group have values. For example:
%artist%[ - %distinct_album%]
This displays the artist and, when available, a distinct album name with a separator. Duplicate filtering avoids redundant text while allowing the song title to appear in both the heading and the body of the activity card.

Layouts can be saved, loaded, renamed, duplicated, deleted, imported or exported as JSON files. Layout exports do not include account credentials. Revert discards unapplied changes, and Restore defaults restores the default template fields.
Playback and inactivity
Open Settings > Playback & inactivity to configure the progress bar, start offset, end-of-track clearing and inactivity behavior.

The progress bar is estimated from the listening history timestamp and song duration. Completed tracks retain their original progress bar until a configured clearing action occurs. When progress is enabled and usable timing information is unavailable, the activity is cleared instead of publishing a standalone elapsed timer.

Inactivity is measured from the estimated end of the song. The delay can be entered in seconds, minutes or hours.
Inactivity action	Behavior
Keep last song	Retain the last displayed activity
Hide timer	Remove timestamps when progress is disabled; clear the activity when progress is enabled
Mark inactive	Display custom inactive text while retaining the progress bar if enabled
Clear presence	Remove the activity from Discord


Enable inactivity actions controls both inactivity handling and end-of-track clearing. Disabling it retains the configured preferences. When enabled, end-of-track clearing takes priority once its grace period expires. Newly detected playback restores normal music presence.

SoundCloud history does not reliably identify when a song is paused, stopped or skipped within the player, so inactivity is an estimate rather than a live playback status.
Polling and request management
The default automatic polling interval is 20 seconds, with adaptive polling enabled and a local rolling 24-hour request budget of 8,640 requests. Polling intervals and adaptive behavior can be customized in Settings.

Refresh now requests the latest history entry independently of the automatic polling schedule, including when automatic polling is paused. Manual requests count toward usage but are not limited by the automatic polling budget or interval. If a request is already running, one manual refresh is queued. Server cooldowns and access restrictions still apply.

Publish latest republishes the detected track without making an additional SoundCloud request.

The application uses the undocumented api-v2.soundcloud.com/me/play-history/tracks?limit=1 endpoint. History entries may arrive after a delay, and faster polling does not necessarily provide faster playback detection. The endpoint's server-side rate limit is unknown; the local request budget is not a verified SoundCloud quota. Reduce request frequency if rate limits occur and respect all cooldowns and access restrictions.
Custom presence
The Custom presence tab can be edited without interrupting music presence. Select Enable custom mode or switch modes from Now playing, configure the activity and select Publish custom.

SoundCloud polling is suspended while custom mode is active. Custom activities support configurable text, activity types, large and small images, tooltips, a link button, an optional elapsed timer and saved presets. Select Return to music to restore music mode.

Images must be public HTTPS URLs or asset keys from your Discord developer application. Local image file paths are not supported by Discord RPC.
Logging and performance
The Activity log includes SoundCloud request results, track changes, Discord connection events, acknowledgment timings and errors. Messages can be filtered by category. Disable Hide unchanged polls to include unchanged successful requests.

Persistent logging and the number of log entries retained in memory are configurable. Optional performance settings include skipping unchanged GUI values, reducing repeated inactive Discord checks and limiting background preview rendering. The settings that skip GUI updates or inactive checks are disabled by default.
Privacy and local storage
Runtime files are stored in %APPDATA%\SoundCloudPresence\:
File	Contents
account.dpapi	Access token encrypted using Windows DPAPI for the current Windows user
settings.json	Discord application ID, preferences, templates and saved presets
activity.log / activity.old.log	Local activity and diagnostic logs
request_usage.json	Request timestamps and cooldown information


The access token is sent to SoundCloud for authenticated history requests. Track metadata, artwork URLs and optional links are sent to Discord through its local RPC connection. Public image hosts may receive image requests.

Logs, exported settings and layouts may contain listening information or custom text and URLs. Review them before sharing. Do not upload credentials or runtime files. Forget account removes the locally stored token but does not revoke the browser session.
Troubleshooting
Issue	Recommended action
Application does not open	Run Install.bat and verify Python, Tkinter and PyPresence are installed in the same Python environment.
HTTP 401	Obtain a fresh access token using the in-app authentication instructions.
HTTP 403	Stop polling and verify normal account access before reconnecting.
HTTP 429	Wait for the cooldown and increase the polling interval if necessary.
HTTP 200 with unchanged track	SoundCloud has not returned a different history entry. Enable unchanged-poll logging to view successful requests.
Discord not updating	Check the connection in Accounts, Discord activity sharing and the Activity log. Try Publish latest.
Inaccurate playback progress	Check the estimated playback time and start offset. Live player position is not available.
Template changes not appearing	Enable the custom music template and select Apply template. Preview changes are not published automatically.
System tray unavailable	Install the optional tray dependencies.


Contributing
Bug reports and suggestions are welcome. Include the application version, Python version, Windows version, reproduction steps and relevant redacted logs. Never include tokens, cookies, passwords or private configuration files.
Disclaimer
SoundCloud Presence is an independent, unofficial project and is not affiliated with or endorsed by SoundCloud or Discord. Use it only with accounts you are authorized to access and in accordance with the applicable service terms. The undocumented SoundCloud endpoint and Discord RPC behavior may change without notice. No guarantee is made regarding account access, rate limits or identical activity rendering across Discord clients.
