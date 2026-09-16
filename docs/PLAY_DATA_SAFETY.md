# UNIFIX — Google Play Data Safety form (draft answers)

Derived from what the code actually collects and transmits. Verify against the
production configuration (Groq / Resend / object storage may or may not be
enabled) before submitting.

## Summary answers

| Question | Answer |
|---|---|
| Does your app collect or share any of the required user data types? | **Yes** |
| Is all user data encrypted in transit? | **Yes** (HTTPS) |
| Do you provide a way for users to request that their data be deleted? | **Yes** — in-app (`/account-deletion`, Profile → Delete my account) and by email |
| Data collected but processed ephemerally only? | No |

## Data types

### Personal info

| Type | Collected | Shared | Purpose | Optional? |
|---|---|---|---|---|
| Name | Yes | No | App functionality (identify who reported an issue) | Required — for employees, taken from their Google account on first sign-in |
| Email address | Yes | No | App functionality (account identifier for employees, status notifications) | **Required for employees** (it is their account identifier — see Google Sign-In below); not collected for the administrator account |
| User IDs (username) | Yes | No | Account management | Required |
| Other info (department) | Yes | No | App functionality (routing / analytics) | Optional |

> **Google Sign-In**: employees authenticate via Google Sign-In (a third-party
> identity provider). The app receives only the employee's name, email address
> and email-verification status from Google — never a password. Declare Google
> as an authentication provider / third-party SDK as applicable on the Data
> Safety form.
>
> PIN: the administrator account only (employees no longer have one) — stored
> only as a bcrypt hash, never transmitted or stored in readable form. Google's
> guidance treats credentials as not "collected" when only a hash is retained
> for authentication — declare under **not collected** or note the hash. Do
> **not** declare the PIN as a shared data type.

### Photos and videos

| Type | Collected | Shared | Purpose | Optional? |
|---|---|---|---|---|
| Photos | Yes | **Yes, if Groq AI is enabled** | App functionality (evidence of the infrastructure issue); AI classification | Optional (user chooses to attach) |

- **Collected**: yes — the issue photo is uploaded and stored with the report.
- **Shared**: "Yes" **only if `GROQ_API_KEY` is set** in production — the photo is
  sent to Groq, Inc.'s API to suggest a category/severity. If Groq is not
  enabled, answer **No** to shared.

### App activity

| Type | Collected | Shared | Purpose |
|---|---|---|---|
| Other user-generated content (issue description text, location selection, resolution notes) | Yes | Yes, if Groq enabled (description text only) | App functionality; AI classification |
| App interactions (report/workflow history, audit log) | Yes | No | App functionality; accountability |

### Data NOT collected (explicitly answer "No")

- Location (approximate or precise) — the app uses a **structured campus
  dropdown**, never device GPS.
- Financial info, health info, contacts, calendar, SMS, call logs, browsing
  history, installed apps.
- Device or other identifiers / advertising ID.
- Audio, files/docs, music.
- Crash logs / diagnostics via a third-party SDK — none integrated.

## Third-party recipients (declare under "shared" only if enabled)

| Recipient | Data | Condition |
|---|---|---|
| Google (Sign-In / OAuth) | Nothing sent to Google beyond the standard OAuth request; Google returns name, email and email-verification status to the app | Always, for employee authentication, once `GOOGLE_OAUTH_CLIENT_ID`/`SECRET` are set. This is an authentication provider, not a data-sharing recipient. |
| Groq, Inc. (AI inference) | Issue description text + attached photo | Only if `GROQ_API_KEY` set |
| Resend (transactional email) | Recipient email address + report reference | Only if `RESEND_API_KEY` set |
| Cloudflare R2 / AWS S3 (photo storage) | Issue photos | Only if `R2_*` / `S3_*` set. This is storage-processor, not "sharing". |

No data is sold. No data is shared for advertising or analytics.

## Security practices

- Encrypted in transit: **Yes** (HTTPS/TLS).
- Users can request deletion: **Yes** (in-app + email; see `/account-deletion`).
- Committed to Play Families policy: N/A (not a Families app).
- Independent security review: No.
