import os
import uuid
import shutil
import threading
from pathlib import Path

# config.py lives at <BASE_DIR>/configs/webapp/config.py → three levels up = BASE_DIR
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AUTODARTS_DATA_DIR = os.environ.get("AUTODARTS_DATA_DIR", "/home/peter/autodarts-data")
DATA_DIR = Path(AUTODARTS_DATA_DIR).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
HELP_PDF_FILENAME = "Autodarts_install_manual.pdf"
SIEMENS_STAR_DIR = Path("/usr/local/bin/static")
SIEMENS_STAR_FILENAME = "siemens-star-a4.pdf"

LANG_JSON_DIR = Path(BASE_DIR) / "static" / "lang"
LANG_CONFIG_PATH = LANG_JSON_DIR / "config_lang.json"
CAM_CONFIG_PATH = "/var/lib/autodarts/cam-config.json"
CAMERA_MODE_SESSION = uuid.uuid4().hex

ALLOWED_JOURNAL_UNITS = {"darts-caller.service", "darts-wled.service"}

MAX_CAMERAS = 8
MAX_VIDEO_INDEX = 50
STREAM_BASE_PORT = 8081

WIFI_CONNECTION_NAME = "Autodarts-Net"
AUTODARTS_SERVICE = "autodarts.service"

WIFI_INTERFACE = "wlan0"

AP_CONNECTION_NAME = "Autodarts-AP"
AP_INTERFACE = "wlan_ap"
AP_INTERNET_STATUS_PATH = "/var/lib/autodarts/ap-internet-status.json"
MANUAL_WIFI_CONNECT_FLAG = "/run/autodarts_manual_wifi_connect.flag"

WIFI_REBOOT_COUNTDOWN_SECONDS = 10
WIFI_REBOOT_STATE_FILE = "/run/autodarts_wifi_channel_reboot.json"
WIFI_REBOOT_UNIT_PREFIX = "autodarts-wifi-channel-reboot"
WIFI_CHANNEL_APPLY_UNIT_PREFIX = "autodarts-wifi-channel-apply"

DARTS_CALLER_START_CUSTOM = "/var/lib/autodarts/extensions/darts-caller/start-custom.sh"
DARTS_CALLER_DIR = "/var/lib/autodarts/extensions/darts-caller"
DARTS_CALLER_SERVICE = "darts-caller.service"
CALLER_AUTH_STATUS_URL = "https://127.0.0.1:8079/api/auth/status"
CALLER_ENABLED_PATH = "/var/lib/autodarts/caller-enabled.json"
CALLER_TOKEN_PATHS = [
    "/root/.config/darts-caller/tokens.json",
    "/home/peter/.config/darts-caller/tokens.json",
    "/var/lib/autodarts/extensions/darts-caller/tokens.json",
]

DARTS_WLED_DIR = "/var/lib/autodarts/extensions/darts-wled"
DARTS_WLED_SERVICE = "darts-wled.service"
DARTS_WLED_WATCHDOG_TIMER = "autodarts-wled-reconnect-watchdog.timer"
DARTS_WLED_START_CUSTOM = "/var/lib/autodarts/config/darts-wled/start-custom.sh"

WLED_CONFIG_PATH = "/var/lib/autodarts/wled-targets.json"
WLED_MDNS_NAME = "Dart-Led.local"
WLED_FLAG_PATH = "/var/lib/autodarts/wled-enabled.json"
WLED_STATUS_CACHE_TTL_SEC = 3.0

ADMIN_GPIO_IMAGE = "/home/peter/autodarts-data/GPIO_Setup.jpeg"

SETTINGS_PATH = "/var/lib/autodarts/webpanel-settings.json"
AUTODARTS_THEME_DIR = Path(os.environ.get("AUTODARTS_THEME_DIR", "/usr/local/bin/theme"))
AUTODARTS_THEME_STATE_PATH = Path(os.environ.get("AUTODARTS_THEME_STATE_PATH", "/var/lib/autodarts/autodarts-theme-state.json"))
AUTODARTS_THEME_PANEL_URL = os.environ.get("AUTODARTS_THEME_PANEL_URL", "http://10.77.0.1")
AUTODARTS_THEME_STORE_URL = "https://chromewebstore.google.com/detail/autodarts-themes/chjcoefebpojnaihcjegalpfmlblgkcf?hl=de&utm_source=ext_sidebar"

AUTODARTS_CALLER_ZIP_URL = os.environ.get("AUTODARTS_CALLER_ZIP_URL", "https://github.com/Jumbo125/Autodarts-Webinterface-Installation/releases/download/V1/Caller_Webpanel.zip").strip()
AUTODARTS_CALLER_DIR_NAME = os.environ.get("AUTODARTS_CALLER_DIR_NAME", "Caller").strip() or "Caller"
AUTODARTS_CALLER_WINDOWS_INSTALLER_FILENAME = "Windows_Caller_install.bat"
AUTODARTS_CALLER_INSTALL_LOCK = threading.Lock()

AUTODARTS_DISPLAY_SECTION_PI5_ONLY = str(os.environ.get("AUTODARTS_DISPLAY_SECTION_PI5_ONLY", "1")).strip().lower() not in {"0", "false", "no", "off"}
AUTODARTS_DISPLAY_XRANDR = os.environ.get("AUTODARTS_DISPLAY_XRANDR", shutil.which("xrandr") or "xrandr")
AUTODARTS_DISPLAY_WLR_RANDR = os.environ.get("AUTODARTS_DISPLAY_WLR_RANDR", shutil.which("wlr-randr") or "wlr-randr")
AUTODARTS_DISPLAY_USER = os.environ.get("AUTODARTS_DISPLAY_USER", "").strip()
AUTODARTS_DISPLAY_PREFERRED_USERS = [x for x in [AUTODARTS_DISPLAY_USER, os.environ.get("SUDO_USER", "").strip(), "peter", "pi"] if x]

PI5_HEADLESS_STATE_PATH = Path(os.environ.get("AUTODARTS_PI5_HEADLESS_STATE_PATH", "/var/lib/autodarts/pi5-headless-state.json"))
PI5_HEADLESS_RESOLUTION = os.environ.get("AUTODARTS_PI5_HEADLESS_RESOLUTION", "1920x1080").strip() or "1920x1080"
PI5_HEADLESS_RATE = os.environ.get("AUTODARTS_PI5_HEADLESS_RATE", "60").strip() or "60"
PI5_HEADLESS_OUTPUTS = ["HDMI-A-1", "HDMI-1"]

PINGTEST_STATE_DIR = "/var/lib/autodarts/pingtests"

WEBPANEL_SERVICE = "autodarts-web.service"
WEBPANEL_UPDATE_SCRIPT = "/usr/local/bin/autodarts-webpanel-update.sh"
WEBPANEL_UPDATE_LOG = "/var/log/autodarts_webpanel_update.log"
WEBPANEL_UPDATE_STATE = "/var/lib/autodarts/webpanel-update-state.json"
WEBPANEL_UPDATE_CHECK = "/var/lib/autodarts/webpanel-update-check.json"
STATE_DIR = os.path.dirname(WEBPANEL_UPDATE_STATE)
UVC_BACKUP_ROOT = os.path.join(STATE_DIR, "uvc-backup")

OS_UPDATE_LOG = "/var/log/autodarts_os_update.log"
OS_UPDATE_STATE = "/var/lib/autodarts/os-update-state.json"

UFW_LOG = "/var/log/autodarts_ufw.log"
UFW_STATE = "/var/lib/autodarts/ufw-state.json"
UFW_PORT_RULES = [
    "22/tcp",
    "3180/tcp",
    "8079/tcp",
    "3181/tcp",
    "80/tcp",
    "21324/udp",
    "5568/udp",
    "6454/udp",
    "4048/udp",
]

EXTENSIONS_UPDATE_SCRIPT = "/usr/local/bin/autodarts-extensions-update.sh"
EXTENSIONS_UPDATE_LOG = "/var/log/autodarts_extensions_update.log"
EXTENSIONS_UPDATE_STATE = "/var/lib/autodarts/extensions-update-state.json"
EXTENSIONS_UPDATE_LAST = "/var/lib/autodarts/extensions-update-last.json"

AUTODARTS_UPDATE_LOG    = "/var/log/autodarts_ad_update.log"
AUTODARTS_UPDATE_STATE  = "/var/lib/autodarts/autodarts-update-state.json"
AUTODARTS_UPDATE_LOCK   = "/var/lib/autodarts/autodarts-update-lock.json"
AUTODARTS_UPDATE_RESULT = "/var/lib/autodarts/autodarts-update-result.txt"
AUTODARTS_AUTO_UPDATE_STAMP = "/tmp/autodarts_auto_update.stamp"
V2_MIGRATION_STATE_FILE = "/var/lib/autodarts/autodarts-v2-migration-state.json"
V2_WELCOME_FLAG_FILE    = "/var/lib/autodarts/v2-welcome-shown.flag"
EXTENSIONS_V2_INSTALL_SCRIPT = "/usr/local/bin/autodarts-extensions-v2-install.sh"
EXTENSIONS_V2_FLAG = "/var/lib/autodarts/config/extensions-v2-installed.json"
EXTENSIONS_V2_INSTALL_LOG = "/var/log/autodarts_extensions_v2_install.log"
EXTENSIONS_V2_INSTALL_STATE = "/var/lib/autodarts/extensions-v2-install-state.json"
CALLER_AUTH_RESET_SCRIPT = "/usr/local/bin/autodarts-caller-auth-reset.sh"
CALLER_AUTH_RESET_STATE = "/var/lib/autodarts/caller-auth-reset-last.json"
CALLER_V2_BINARY = "/var/lib/autodarts/extensions/darts-caller/darts-caller"
WLED_V2_BINARY = "/var/lib/autodarts/extensions/darts-wled/darts-wled"

WEBPANEL_VERSION_FILE = os.environ.get("WEBPANEL_VERSION_FILE", "/var/lib/autodarts/webpanel-version.txt")
WEBPANEL_HARDCODED_VERSION = ""

WEBPANEL_RAW_BASE = os.environ.get(
    "WEBPANEL_RAW_BASE",
    "https://raw.githubusercontent.com/jumbo1250/Autodarts-Webinterface-Installation/main/latest",
)
WEBPANEL_VERSION_URL = os.environ.get("WEBPANEL_VERSION_URL", WEBPANEL_RAW_BASE + "/version.txt")
WEBPANEL_UI_FALLBACK_VERSION = "1.20"

DEFAULT_SETTINGS = {
    "admin_password": "1234",
    "autodarts_auto_update": True,
    "ap_ssid_choices": [f"Autodartsinstall{i}" for i in range(1, 11)],
    "autodarts_update_cmd": "",
    "autodarts_versions": [],
    "autodarts_stable_version": "",
    "webpanel_channel": "stable",
}

PI_MONITOR_SCRIPT = "/usr/local/bin/pi_monitor_test.sh"
PI_MONITOR_CSV = "/var/log/pi_monitor_test.csv"
PI_MONITOR_README = "/var/log/pi_monitor_test_README.txt"
PI_MONITOR_STATE = "/tmp/autodarts_pi_monitor_state.json"
PI_MONITOR_PIDFILE = "/tmp/autodarts_pi_monitor.pid"
PI_MONITOR_OUTLOG = "/var/log/pi_monitor_test.out"

EXTENSIONS_DIR = "/var/lib/autodarts/extensions"
USR_LOCAL_BIN_DIR = "/usr/local/bin"
