import frappe
from frappe.model.document import Document


class WhatsAppInteraktSettings(Document):
	def validate(self):
		if self.country_code:
			self.country_code = self.country_code.strip()
		if self.api_url:
			self.api_url = self.api_url.strip()

		if self.enabled and not self.get_password("api_key"):
			frappe.throw("API Key is required when Interakt integration is enabled.")
