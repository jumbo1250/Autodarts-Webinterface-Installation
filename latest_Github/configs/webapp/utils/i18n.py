import re
import json
from flask import session, request

from webapp.config import LANG_JSON_DIR, LANG_CONFIG_PATH


def _normalize_lang_code(code: str) -> str:
    code = str(code or "").strip().lower().replace(" ", "")
    code = code.replace("-", "_")
    code = re.sub(r"[^a-z0-9_]", "", code)
    return code


def _iter_all_lang_paths():
    try:
        if not LANG_JSON_DIR.exists() or not LANG_JSON_DIR.is_dir():
            return
    except Exception:
        return

    seen: set[str] = set()
    try:
        for candidate in sorted(LANG_JSON_DIR.glob("lang_*.json")):
            try:
                if not candidate.is_file():
                    continue
            except Exception:
                continue

            candidate_key = str(candidate.resolve())
            if candidate_key in seen:
                continue
            seen.add(candidate_key)
            yield candidate
    except Exception:
        return


def _find_lang_path_by_code(code: str):
    normalized = _normalize_lang_code(code)
    if not normalized:
        return None, normalized

    for path in _iter_all_lang_paths() or []:
        stem = path.stem
        suffix = stem[5:] if stem.startswith("lang_") else ""
        suffix = _normalize_lang_code(suffix)
        if suffix == normalized:
            return path, normalized

    return None, normalized


def _default_flag_for_lang(code: str) -> str:
    normalized = _normalize_lang_code(code)
    if not normalized:
        return "xx"

    parts = [p for p in normalized.replace("-", "_").split("_") if p]
    lang = parts[0] if parts else normalized
    region = parts[1] if len(parts) > 1 else ""

    if region:
        return region.lower()

    lang_to_flag = {
        "ar": "sa", "bg": "bg", "cs": "cz", "da": "dk", "de": "de",
        "el": "gr", "en": "gb", "es": "es", "et": "ee", "fi": "fi",
        "fr": "fr", "ga": "ie", "he": "il", "hi": "in", "hr": "hr",
        "hu": "hu", "id": "id", "it": "it", "ja": "jp", "ko": "kr",
        "lt": "lt", "lv": "lv", "ms": "my", "mt": "mt", "nl": "nl",
        "no": "no", "pl": "pl", "pt": "pt", "ro": "ro", "ru": "ru",
        "sk": "sk", "sl": "si", "sr": "rs", "sv": "se", "th": "th",
        "tr": "tr", "uk": "ua", "vi": "vn", "zh": "cn",
    }
    return lang_to_flag.get(lang, lang[:2] or "xx")


def _normalize_lang_payload(data, fallback_code: str):
    if isinstance(data, dict):
        payload = dict(data)
    else:
        payload = {"placeholder": {}, "items": data}

    config = payload.get("config")
    if not isinstance(config, dict):
        config = {}
    else:
        config = dict(config)

    placeholder = payload.get("placeholder")
    if not isinstance(placeholder, dict):
        placeholder = {}

    abk = _normalize_lang_code(config.get("abk") or fallback_code)
    if not abk:
        abk = fallback_code

    flag = str(config.get("flag") or "").strip().lower()
    flag = re.sub(r"[^a-z0-9_-]", "", flag)
    if not flag:
        flag = _default_flag_for_lang(abk)

    config["abk"] = abk
    config["flag"] = flag
    config.pop("default", None)

    payload["config"] = config
    payload["placeholder"] = placeholder
    return payload


def _read_lang_default_config() -> tuple[str | None, str | None]:
    try:
        if not LANG_CONFIG_PATH.exists():
            return None, None

        raw = LANG_CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None, "config_lang.json muss ein JSON-Objekt enthalten."

        code = _normalize_lang_code(data.get("default"))
        if not code:
            return None, None
        return code, None
    except json.JSONDecodeError as e:
        return None, f"config_lang.json ist ungültig: {e}"
    except Exception as e:
        return None, f"config_lang.json konnte nicht gelesen werden: {e}"


def _write_lang_default_config(code: str) -> tuple[bool, str | None]:
    normalized = _normalize_lang_code(code)
    if not normalized:
        return False, "Ungültiger Sprachcode."

    try:
        LANG_JSON_DIR.mkdir(parents=True, exist_ok=True)
        LANG_CONFIG_PATH.write_text(
            json.dumps({"default": normalized}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return True, None
    except Exception as e:
        return False, f"config_lang.json konnte nicht geschrieben werden: {e}"


def _resolve_default_lang_code(languages: dict[str, dict]) -> tuple[str | None, str | None]:
    configured, err = _read_lang_default_config()
    if configured and configured in languages:
        return configured, err

    if configured and configured not in languages:
        err = f"Default-Sprache '{configured}' aus config_lang.json wurde nicht gefunden."

    fallback = next(iter(languages.keys()), None)
    return fallback, err


def _load_lang_json(code: str) -> tuple[dict | None, str | None, str | None, str]:
    path, normalized = _find_lang_path_by_code(code)
    if not normalized:
        return None, None, "Ungültiger Sprachcode.", normalized
    if path is None:
        return None, None, f"Sprachdatei lang_{normalized}.json nicht gefunden in {LANG_JSON_DIR}.", normalized

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, (dict, list)):
            return None, str(path), "Sprachdatei muss ein JSON-Objekt oder Array enthalten.", normalized
        payload = _normalize_lang_payload(data, normalized)
        return payload, str(path), None, normalized
    except json.JSONDecodeError as e:
        return None, str(path), f"JSON ungültig: {e}", normalized
    except Exception as e:
        return None, str(path), f"Datei konnte nicht gelesen werden: {e}", normalized


def _load_all_lang_jsons() -> tuple[dict, list[dict], str | None]:
    languages: dict[str, dict] = {}
    sources: list[dict] = []

    try:
        if not LANG_JSON_DIR.exists() or not LANG_JSON_DIR.is_dir():
            return {}, [], f"Sprachordner nicht gefunden: {LANG_JSON_DIR}"
    except Exception as e:
        return {}, [], f"Sprachordner konnte nicht gelesen werden: {e}"

    paths = list(_iter_all_lang_paths() or [])
    if not paths:
        return {}, [], f"Keine Sprachdateien in {LANG_JSON_DIR} gefunden."

    for path in paths:
        stem = path.stem
        suffix = stem[5:] if stem.startswith("lang_") else ""
        suffix = _normalize_lang_code(suffix)
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, (dict, list)):
                sources.append({
                    "file": str(path),
                    "code": suffix,
                    "ok": False,
                    "msg": "Sprachdatei muss ein JSON-Objekt oder Array enthalten.",
                })
                continue

            payload = _normalize_lang_payload(data, suffix)
            code = _normalize_lang_code(payload.get("config", {}).get("abk") or suffix)
            if not code:
                code = suffix
            payload["config"]["abk"] = code
            languages[code] = payload
            sources.append({"file": str(path), "code": code, "ok": True})
        except json.JSONDecodeError as e:
            sources.append({"file": str(path), "code": suffix, "ok": False, "msg": f"JSON ungültig: {e}"})
        except Exception as e:
            sources.append({"file": str(path), "code": suffix, "ok": False, "msg": f"Datei konnte nicht gelesen werden: {e}"})

    if not languages:
        return {}, sources, "Keine gültigen Sprachdateien gefunden."

    return languages, sources, None


def _get_current_lang_code() -> str:
    code = session.get("lang") or request.args.get("lang") or ""
    code = _normalize_lang_code(code)

    languages, _, _ = _load_all_lang_jsons()
    default_code, _ = _resolve_default_lang_code(languages)

    if code and code in languages:
        return code
    if default_code and default_code in languages:
        return default_code
    return "de"


def t(key: str, fallback: str = "", **kwargs) -> str:
    languages, _, _ = _load_all_lang_jsons()
    code = _get_current_lang_code()
    lang_data = languages.get(code, {}) or {}

    placeholder = lang_data.get("placeholder", {})
    if not isinstance(placeholder, dict):
        placeholder = {}

    value = placeholder.get(key, fallback)

    if isinstance(value, (dict, list)):
        value = fallback

    text = str(value)

    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass

    return text
