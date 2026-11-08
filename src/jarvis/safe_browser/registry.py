from __future__ import annotations

from types import MappingProxyType

from jarvis.safe_browser.models import BrowserDestination


_DESTINATIONS = {
    "browser_home": BrowserDestination("browser_home", "navegador", "https://www.google.com/"),
    "google": BrowserDestination("google", "Google", "https://www.google.com/"),
    "youtube": BrowserDestination("youtube", "YouTube", "https://www.youtube.com/"),
    "github": BrowserDestination("github", "GitHub", "https://github.com/"),
    "google_calendar": BrowserDestination("google_calendar", "Google Agenda", "https://calendar.google.com/"),
    "google_drive": BrowserDestination("google_drive", "Google Drive", "https://drive.google.com/"),
    "google_docs": BrowserDestination("google_docs", "Google Docs", "https://docs.google.com/"),
    "whatsapp_web": BrowserDestination("whatsapp_web", "WhatsApp Web", "https://web.whatsapp.com/"),
    "spotify_web": BrowserDestination("spotify_web", "Spotify Web", "https://open.spotify.com/"),
}

DESTINATIONS = MappingProxyType(_DESTINATIONS)
SEARCH_PROVIDERS = MappingProxyType({
    "google": "https://www.google.com/search?q={query}",
})
