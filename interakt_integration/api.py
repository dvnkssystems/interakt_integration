from html import escape

import frappe
from frappe import _
from frappe.utils import now_datetime

from interakt_integration.interakt_client import sync_interakt_user, track_interakt_event
from interakt_integration.utils import (
	as_json,
	build_interakt_payload,
	compact_callback_data,
	extract_webhook_callback_data,
	extract_webhook_provider_message_id,
	extract_webhook_status,
	format_phone_number,
	get_recipient_phone,
	get_settings,
	get_template_mapping,
	get_webhook_payload,
	get_webhook_secret,
	is_integration_enabled,
	post_interakt_message,
	update_log_timeline_fields,
	verify_webhook_signature,
)


def send_notification(doc, method=None):
	if isinstance(doc, str):
		doc = frappe.get_doc(frappe.parse_json(doc))

	if not is_integration_enabled():
		return

	if not get_template_mapping(doc.doctype):
		return

	frappe.enqueue(
		"interakt_integration.api.send_interakt_template",
		queue="short",
		enqueue_after_commit=True,
		reference_doctype=doc.doctype,
		reference_name=doc.name,
	)


@frappe.whitelist()
def send_manual_message(doctype: str, name: str, template_name: str | None = None, mobile_no: str | None = None):
	frappe.only_for(["System Manager", "Sales Manager", "Sales User"])

	frappe.enqueue(
		"interakt_integration.api.send_interakt_template",
		queue="short",
		enqueue_after_commit=True,
		reference_doctype=doctype,
		reference_name=name,
		template_name=template_name,
		mobile_no=mobile_no,
	)

	return {"queued": True}


def send_interakt_template(
	reference_doctype: str,
	reference_name: str,
	template_name: str | None = None,
	mobile_no: str | None = None,
):
	settings = get_settings()
	if not settings.enabled:
		return

	doc = frappe.get_doc(reference_doctype, reference_name)
	mapping = get_template_mapping(reference_doctype, template_name)
	if not mapping:
		frappe.throw(_("No enabled WhatsApp Template Mapping found for {0}.").format(reference_doctype))

	recipient = mobile_no or get_recipient_phone(doc, mapping)
	country_code, phone_number, full_phone_number = format_phone_number(recipient, settings.country_code)

	log = frappe.new_doc("WhatsApp Message Log")
	log.status = "Queued"
	log.template_name = mapping.template_name
	log.reference_doctype = reference_doctype
	log.reference_name = reference_name
	log.country_code = country_code
	log.phone_number = full_phone_number
	log.queued_on = now_datetime()
	log.insert(ignore_permissions=True)

	callback_data = compact_callback_data(
		{
			"message_log": log.name,
			"doctype": reference_doctype,
			"name": reference_name,
		}
	)
	payload = build_interakt_payload(
		mapping=mapping,
		doc=doc,
		country_code=country_code,
		phone_number=phone_number,
		callback_data=callback_data,
	)

	log.callback_data = callback_data
	log.request_payload = as_json(payload)
	log.communication = create_whatsapp_communication(log, mapping, payload)
	log.save(ignore_permissions=True)

	try:
		if settings.sync_contact_before_send:
			user_sync_response = sync_interakt_user(
				doc=doc,
				country_code=country_code,
				phone_number=phone_number,
				settings=settings,
			)
			log.user_sync_response = as_json(user_sync_response)
			log.save(ignore_permissions=True)

		_, response_payload = post_interakt_message(payload, settings)
		log.response_payload = as_json(response_payload)
		if isinstance(response_payload, dict) and response_payload.get("result") is False:
			frappe.throw(_("Interakt API returned an unsuccessful response."))
		log.provider_message_id = get_provider_message_id(response_payload)
		log.status = "Sent"
		log.sent_on = now_datetime()

		if settings.track_event_after_send:
			event_response = track_interakt_event(
				doc=doc,
				country_code=country_code,
				phone_number=phone_number,
				event="WhatsApp Template Sent",
				traits=get_template_event_traits(doc, mapping, log, payload, response_payload),
				settings=settings,
			)
			log.event_track_response = as_json(event_response)
	except Exception:
		log.status = "Failed"
		log.error_message = frappe.get_traceback()
		log.save(ignore_permissions=True)
		update_communication_delivery(log, "Failed", error_message=log.error_message)
		raise

	log.save(ignore_permissions=True)
	update_communication_delivery(log, "Sent")
	return log.name


@frappe.whitelist(allow_guest=True)
def interakt_webhook():
	payload_text = frappe.request.get_data(as_text=True) if frappe.request else ""
	settings = get_settings()

	if not verify_webhook_signature(payload_text, get_webhook_secret(settings)):
		frappe.throw(_("Invalid Interakt webhook signature."), frappe.PermissionError)

	payload = get_webhook_payload()
	status = extract_webhook_status(payload)
	provider_message_id = extract_webhook_provider_message_id(payload)
	callback_data = extract_webhook_callback_data(payload)
	log_name = callback_data.get("message_log")

	if not log_name and provider_message_id:
		log_name = frappe.db.get_value("WhatsApp Message Log", {"provider_message_id": provider_message_id})

	if not log_name:
		return {"ok": True, "updated": False}

	log = frappe.get_doc("WhatsApp Message Log", log_name)
	log.webhook_payload = as_json(payload)

	if provider_message_id and not log.provider_message_id:
		log.provider_message_id = provider_message_id

	if status:
		log.status = status
		update_log_timeline_fields(log, status)

	log.save(ignore_permissions=True)
	if status:
		update_communication_delivery(log, status)

	return {"ok": True, "updated": True, "status": log.status}


def get_provider_message_id(response_payload) -> str:
	if isinstance(response_payload, dict):
		return str(response_payload.get("id") or response_payload.get("message_id") or "")

	return ""


def get_template_event_traits(doc, mapping, log, request_payload: dict, response_payload) -> dict:
	return {
		"frappe_doctype": doc.doctype,
		"frappe_name": doc.name,
		"message_log": log.name,
		"template_name": mapping.template_name,
		"language_code": mapping.language_code or "en",
		"provider_message_id": get_provider_message_id(response_payload),
		"body_values": request_payload.get("template", {}).get("bodyValues", []),
	}


def create_whatsapp_communication(log, mapping, payload: dict) -> str:
	subject = _("WhatsApp: {0}").format(mapping.template_name)
	content = get_communication_content(log, mapping, payload, "Queued")

	communication = frappe.get_doc(
		{
			"doctype": "Communication",
			"communication_type": "Communication",
			"communication_medium": "SMS",
			"sent_or_received": "Sent",
			"status": "Linked",
			"delivery_status": "Sending",
			"subject": subject,
			"content": content,
			"text_content": get_plain_communication_text(log, mapping, payload, "Queued"),
			"phone_no": log.phone_number,
			"reference_doctype": log.reference_doctype,
			"reference_name": log.reference_name,
			"communication_date": log.queued_on or now_datetime(),
		}
	)
	communication.insert(ignore_permissions=True)
	return communication.name


def update_communication_delivery(log, status: str, error_message: str | None = None):
	if not log.communication:
		return

	values = get_communication_delivery_values(status, error_message)
	if not values:
		return

	frappe.db.set_value("Communication", log.communication, values, update_modified=True)


def get_communication_delivery_values(status: str, error_message: str | None = None) -> dict:
	if status in {"Sent", "Delivered"}:
		return {"delivery_status": "Sent"}

	if status == "Read":
		return {
			"delivery_status": "Read",
			"read_by_recipient": 1,
			"read_by_recipient_on": now_datetime(),
		}

	if status == "Failed":
		values = {
			"delivery_status": "Error",
			"content": get_failed_communication_content(log),
			"text_content": get_failed_communication_text(log),
		}
		return values

	return {}


def get_communication_content(log, mapping, payload: dict, status: str) -> str:
	body_values = payload.get("template", {}).get("bodyValues", [])
	value_items = "".join(f"<li>{escape(str(value))}</li>" for value in body_values)
	message_log_link = f"/app/whatsapp-message-log/{escape(log.name)}"

	return f"""
<p><strong>WhatsApp template message via Interakt.</strong></p>
<p>
	<strong>Status:</strong> {escape(status)}<br>
	<strong>Template:</strong> {escape(mapping.template_name)}<br>
	<strong>Recipient:</strong> {escape(log.phone_number or "")}
</p>
<p><strong>Template Values</strong></p>
<ol>{value_items}</ol>
<p><a href="{message_log_link}">Open WhatsApp Message Log {escape(log.name)}</a></p>
""".strip()


def get_plain_communication_text(log, mapping, payload: dict, status: str) -> str:
	body_values = payload.get("template", {}).get("bodyValues", [])
	values = "\n".join(f"- {value}" for value in body_values)

	return "\n".join(
		[
			"WhatsApp template message via Interakt.",
			f"Status: {status}",
			f"Template: {mapping.template_name}",
			f"Recipient: {log.phone_number or ''}",
			"Template Values:",
			values,
			f"WhatsApp Message Log: {log.name}",
		]
	)


def get_failed_communication_content(log) -> str:
	message_log_link = f"/app/whatsapp-message-log/{escape(log.name)}"

	return f"""
<p><strong>WhatsApp template send failed.</strong></p>
<p>
	<strong>Recipient:</strong> {escape(log.phone_number or "")}<br>
	<strong>Template:</strong> {escape(log.template_name or "")}
</p>
<p><a href="{message_log_link}">Open WhatsApp Message Log {escape(log.name)}</a></p>
""".strip()


def get_failed_communication_text(log) -> str:
	return "\n".join(
		[
			"WhatsApp template send failed.",
			f"Recipient: {log.phone_number or ''}",
			f"Template: {log.template_name or ''}",
			f"WhatsApp Message Log: {log.name}",
		]
	)
