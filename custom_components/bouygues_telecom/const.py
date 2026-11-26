"""Constants for the Bouygues Telecom integration."""

from datetime import timedelta

DOMAIN = "bouygues_telecom"

CONF_COOKIES = "cookies"
CONF_PERSON_ID = "person_id"

CLIENT_ID = "a360.bouyguestelecom.fr"
REDIRECT_URI = "https://www.bouyguestelecom.fr/mon-compte/all//callback.html"
AUTHORIZE_URL = "https://oauth2.bouyguestelecom.fr/authorize"
TOKEN_URL = "https://oauth2.bouyguestelecom.fr/token"
API_URL = "https://api.bouyguestelecom.fr"
CAS_ORIGIN = "https://www.mon-compte.bouyguestelecom.fr"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Safari/605.1.15"
)

UPDATE_INTERVAL = timedelta(minutes=30)
