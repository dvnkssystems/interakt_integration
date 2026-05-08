import re

import frappe
from frappe.model.document import Document


class WhatsAppTemplateMapping(Document):
	def validate(self):
		seen = set()

		for row in self.variables:
			row.variable_name = (row.variable_name or "").strip()
			row.frappe_field = (row.frappe_field or "").strip()

			if row.variable_name in seen:
				frappe.throw(f"Duplicate template variable {row.variable_name}.")

			if not re.fullmatch(r"\{\{\d+\}\}", row.variable_name):
				frappe.throw("Variable Name must use Interakt placeholder format like {{1}}.")

			seen.add(row.variable_name)
