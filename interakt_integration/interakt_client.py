import base64
import json
from typing import Any

import frappe
from frappe import _

DEFAULT_TRACK_API_HOST = "https://api.interakt.ai"


def get_raw_api_key(settings=None) -> str:
	settings = settings or frappe.get_single("WhatsApp Interakt Settings")
	api_key = settings.get_password("api_key")
	if not api_key:
		frappe.throw(_("Interakt API Key is missing in WhatsApp Interakt Settings."))

	return api_key.strip()


def get_basic_auth_header(settings=None) -> str:
	api_key = get_raw_api_key(settings)
	if api_key.lower().startswith("basic "):
		return api_key

	if decodes_to_basic_username(api_key):
		return f"Basic {api_key}"

	return "Basic " + base64.b64encode(f"{api_key.rstrip(':')}:".encode()).decode()


def get_track_api_key(settings=None) -> str:
	api_key = get_raw_api_key(settings)
	if api_key.lower().startswith("basic "):
		api_key = api_key.split(None, 1)[1].strip()

	decoded_key = decode_basic_token(api_key)
	if decoded_key:
		return decoded_key

	return api_key.rstrip(":")


def decode_basic_token(api_key: str) -> str:
	try:
		decoded = base64.b64decode(api_key).decode()
	except Exception:
		return ""

	if not decoded:
		return ""

	return decoded[:-1] if decoded.endswith(":") else decoded


def decodes_to_basic_username(api_key: str) -> bool:
	decoded = decode_basic_token(api_key)
	return bool(decoded and decoded != api_key)


def get_track_client(settings=None):
	settings = settings or frappe.get_single("WhatsApp Interakt Settings")

	try:
		import track
	except ModuleNotFoundError:
		frappe.throw(
			_("Official Interakt SDK is not installed. Run `bench setup requirements` or install `interakt-track-python==1.0.7`.")
		)

	return track.Client(
		api_key=get_track_api_key(settings),
		host=(settings.track_api_host or DEFAULT_TRACK_API_HOST).strip(),
		sync_mode=True,
		timeout=30,
		debug=False,
	)


def sync_interakt_user(*, doc, country_code: str, phone_number: str, settings=None) -> dict:
	client = get_track_client(settings)
	response = client.user(
		user_id=get_user_id(doc),
		country_code=country_code,
		phone_number=phone_number,
		traits=get_doc_traits(doc),
	)
	return serialize_sdk_response(response)


def track_interakt_event(
	*,
	doc,
	country_code: str,
	phone_number: str,
	event: str,
	traits: dict,
	settings=None,
) -> dict:
	client = get_track_client(settings)
	response = client.event(
		user_id=get_user_id(doc),
		event=event,
		traits=traits,
		country_code=country_code,
		phone_number=phone_number,
	)
	return serialize_sdk_response(response)


def get_user_id(doc) -> str:
	return f"{doc.doctype}:{doc.name}"


def get_doc_traits(doc) -> dict:
	traits = {
		"frappe_doctype": doc.doctype,
		"frappe_name": doc.name,
	}

	for fieldname in ("customer_name", "lead_name", "first_name", "full_name", "company_name", "email_id"):
		value = doc.get(fieldname)
		if value:
			traits[fieldname] = str(value)

	return traits


def serialize_sdk_response(response: Any) -> dict:
	if hasattr(response, "status_code"):
		body: Any
		try:
			body = response.json()
		except ValueError:
			body = response.text

		return {
			"status_code": response.status_code,
			"body": body,
		}

	if isinstance(response, tuple):
		return {
			"queued": response[0],
			"message": response[1] if len(response) > 1 else None,
		}

	try:
		json.dumps(response)
		return {"response": response}
	except TypeError:
		return {"response": str(response)}
