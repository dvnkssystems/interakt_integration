### Interakt Integration

Interakt Integration with Frappe

### WhatsApp API Scaffold

This app includes a standard Webhook + API scaffold for sending Interakt WhatsApp templates from Frappe and storing delivery feedback.

Included DocTypes:

- **WhatsApp Interakt Settings**: Single DocType for enable flag, API key, API URL, default country code, and webhook secret.
- **WhatsApp Interakt Settings** also includes SDK controls for the official `interakt-track-python` package: contact sync before send and event tracking after send.
- **WhatsApp Template Mapping**: maps a Frappe DocType to an Interakt template name, language code, recipient field, and body variables.
- **WhatsApp Template Var**: child table rows for `{{1}}`, `{{2}}`, etc. mapped to Frappe fields.
- **WhatsApp Message Log**: tracks queued, sent, delivered, read, and failed messages with request, response, and webhook payloads.

Runtime paths:

- Automatic trigger: `Sales Order.on_submit` calls `interakt_integration.api.send_notification`.
- Manual button: Lead, Customer, and Sales Order forms load `public/js/send_whatsapp.js`.
- Webhook endpoint: `/api/method/interakt_integration.api.interakt_webhook`.
- Official SDK adapter: `interakt_integration.interakt_client` wraps `interakt-track-python` for `track.user` and `track.event`.
- Template sending remains on Interakt's public message endpoint because the official SDK does not expose `/v1/public/message/`.

Basic setup:

1. Run `bench --site [site-name] migrate` after installing the app.
2. Open **WhatsApp Interakt Settings**, add the Interakt API key, default country code, webhook secret, and enable the integration.
   - The API key may be the Basic header token copied from Interakt or the decoded SDK key. The app normalizes it for both the SDK and message endpoint.
3. Create a **WhatsApp Template Mapping** for the source DocType, such as Sales Order.
4. Add variable rows like `{{1}} -> customer_name` and `{{2}} -> grand_total`.
5. Configure the Interakt webhook URL as `/api/method/interakt_integration.api.interakt_webhook`.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app interakt_integration
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/interakt_integration
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### License

mit
