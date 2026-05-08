import hashlib
import hmac
import json
import re
from collections.abc import Iterable
from typing import Any

import frappe
import requests
from frappe import _
from frappe.utils import now_datetime

from interakt_integration.interakt_client import get_basic_auth_header

DEFAULT_API_URL = "https://api.interakt.ai/v1/public/message/"
PHONE_CANDIDATE_FIELDS = (
	"mobile_no",
	"mobile_number",
	"contact_mobile",
	"customer_mobile",
	"phone",
	"phone_number",
	"contact_number",
)


def get_settings():
	return frappe.get_single("WhatsApp Interakt Settings")


def is_integration_enabled() -> bool:
	try:
		return bool(get_settings().enabled)
	except Exception:
		return False


def get_webhook_secret(settings=None) -> str:
	settings = settings or get_settings()
	return settings.get_password("webhook_secret") or ""


def get_field_value(doc, field_path: str) -> Any:
	value = doc

	for part in (field_path or "").split("."):
		part = part.strip()
		if not part:
			continue

		if isinstance(value, dict):
			value = value.get(part)
		elif hasattr(value, "get"):
			value = value.get(part)
		else:
			value = getattr(value, part, None)

		if value is None:
			break

	return value


def stringify_template_value(value: Any) -> str:
	if value is None:
		return ""

	if isinstance(value, (list, tuple, set)):
		return ", ".join(stringify_template_value(item) for item in value)

	return str(value)


def get_recipient_phone(doc, mapping=None) -> str:
	if mapping and mapping.recipient_field:
		value = get_field_value(doc, mapping.recipient_field)
		if value:
			return str(value)

	for fieldname in PHONE_CANDIDATE_FIELDS:
		value = get_field_value(doc, fieldname)
		if value:
			return str(value)

	return ""


def format_phone_number(phone: str, default_country_code: str = "+91") -> tuple[str, str, str]:
	if not phone:
		frappe.throw(_("Phone number is required to send a WhatsApp message."))

	default_country_code = normalize_country_code(default_country_code)
	raw_phone = str(phone).strip()
	has_plus = raw_phone.startswith("+")
	digits = re.sub(r"\D", "", raw_phone)

	if not digits:
		frappe.throw(_("Phone number {0} does not contain any digits.").format(phone))

	default_country_digits = default_country_code.lstrip("+")

	if has_plus and digits.startswith(default_country_digits):
		country_code = default_country_code
		phone_number = digits[len(default_country_digits) :]
	elif digits.startswith(default_country_digits) and len(digits) > len(default_country_digits) + 6:
		country_code = default_country_code
		phone_number = digits[len(default_country_digits) :]
	else:
		country_code = default_country_code
		phone_number = digits

	phone_number = phone_number.lstrip("0")

	if not phone_number:
		frappe.throw(_("Phone number {0} is invalid after formatting.").format(phone))

	return country_code, phone_number, f"{country_code}{phone_number}"


def normalize_country_code(country_code: str) -> str:
	country_code = str(country_code or "+91").strip()
	country_digits = re.sub(r"\D", "", country_code)

	if not country_digits:
		frappe.throw(_("Default Country Code must contain digits."))

	return f"+{country_digits}"


def get_template_mapping(ref_doctype: str, template_name: str | None = None):
	filters = {"enabled": 1, "ref_doctype": ref_doctype}
	if template_name:
		filters["template_name"] = template_name

	mapping_name = frappe.db.get_value(
		"WhatsApp Template Mapping",
		filters,
		"name",
		order_by="modified desc",
	)

	if not mapping_name:
		return None

	return frappe.get_doc("WhatsApp Template Mapping", mapping_name)


def get_template_body_values(mapping, doc) -> list[str]:
	rows = sorted(mapping.variables or [], key=lambda row: get_variable_index(row.variable_name))
	return [stringify_template_value(get_field_value(doc, row.frappe_field)) for row in rows]


def get_variable_index(variable_name: str) -> int:
	match = re.search(r"\d+", variable_name or "")
	return int(match.group()) if match else 0


def build_interakt_payload(*, mapping, doc, country_code: str, phone_number: str, callback_data: str) -> dict:
	return {
		"countryCode": country_code,
		"phoneNumber": phone_number,
		"callbackData": callback_data,
		"type": "Template",
		"template": {
			"name": mapping.template_name,
			"languageCode": mapping.language_code or "en",
			"bodyValues": get_template_body_values(mapping, doc),
		},
	}


def post_interakt_message(payload: dict, settings=None) -> tuple[int, dict | str]:
	settings = settings or get_settings()
	api_url = (settings.api_url or DEFAULT_API_URL).strip()
	headers = {
		"Authorization": get_basic_auth_header(settings),
		"Content-Type": "application/json",
	}

	response = requests.post(api_url, headers=headers, json=payload, timeout=30)

	try:
		response_payload = response.json()
	except ValueError:
		response_payload = response.text

	if response.status_code >= 400:
		frappe.throw(_("Interakt API request failed with HTTP {0}: {1}").format(response.status_code, response.text))

	return response.status_code, response_payload


def as_json(value: Any) -> str:
	return frappe.as_json(value, indent=2)


def compact_callback_data(value: dict) -> str:
	callback_data = json.dumps(value, separators=(",", ":"), default=str)
	return callback_data[:512]


def verify_webhook_signature(payload_text: str, secret: str) -> bool:
	if not secret:
		return True

	secret_headers = (
		frappe.get_request_header("X-Interakt-Webhook-Secret"),
		frappe.get_request_header("X-Webhook-Secret"),
		frappe.form_dict.get("secret"),
	)
	if any(hmac.compare_digest(str(header), secret) for header in secret_headers if header):
		return True

	signature = (
		frappe.get_request_header("X-Interakt-Signature")
		or frappe.get_request_header("X-Hub-Signature-256")
		or frappe.get_request_header("X-Webhook-Signature")
	)
	if not signature:
		return False

	signature = signature.replace("sha256=", "").strip()
	expected = hmac.new(secret.encode(), payload_text.encode(), hashlib.sha256).hexdigest()
	return hmac.compare_digest(signature, expected)


def get_webhook_payload() -> dict:
	if not frappe.request:
		return {}

	payload = frappe.request.get_json(silent=True)
	if isinstance(payload, dict):
		return payload

	return dict(frappe.form_dict)


def extract_webhook_status(payload: dict) -> str | None:
	event_type = (payload.get("type") or payload.get("event") or "").lower()
	status = str(payload.get("status") or get_nested(payload, ("data", "message", "status")) or "").lower()
	combined = f"{event_type} {status}"

	if "read" in combined:
		return "Read"
	if "delivered" in combined:
		return "Delivered"
	if "failed" in combined:
		return "Failed"
	if "sent" in combined:
		return "Sent"

	return None


def extract_webhook_provider_message_id(payload: dict) -> str:
	candidates = (
		payload.get("id"),
		payload.get("message_id"),
		payload.get("messageId"),
		get_nested(payload, ("data", "message", "id")),
		get_nested(payload, ("data", "message", "message_id")),
		get_nested(payload, ("data", "message", "messageId")),
	)
	return next((str(value) for value in candidates if value), "")


def extract_webhook_callback_data(payload: dict) -> dict:
	candidates = (
		payload.get("callbackData"),
		payload.get("callback_data"),
		get_nested(payload, ("data", "message", "callbackData")),
		get_nested(payload, ("data", "message", "callback_data")),
	)

	for value in candidates:
		if not value:
			continue
		if isinstance(value, dict):
			return value
		if isinstance(value, str):
			try:
				parsed = json.loads(value)
			except ValueError:
				continue
			if isinstance(parsed, dict):
				return parsed

	return {}


def get_nested(value: dict, path: Iterable[str]) -> Any:
	current = value
	for key in path:
		if not isinstance(current, dict):
			return None
		current = current.get(key)
	return current


def update_log_timeline_fields(log, status: str):
	timestamp = now_datetime()
	if status == "Sent" and not log.sent_on:
		log.sent_on = timestamp
	elif status == "Delivered" and not log.delivered_on:
		log.delivered_on = timestamp
	elif status == "Read" and not log.read_on:
		log.read_on = timestamp
