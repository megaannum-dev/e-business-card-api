from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["legal-web"])

_PAGE_STYLE = """
body {
  font-family: Arial, Helvetica, sans-serif;
  max-width: 800px;
  margin: 0 auto;
  padding: 24px 20px 48px;
  line-height: 1.55;
  color: #1a1a1a;
}
h1 { font-size: 1.75rem; margin-bottom: 0.5rem; }
h2 { font-size: 1.15rem; margin-top: 1.75rem; }
ul { padding-left: 1.25rem; }
li { margin: 0.4rem 0; }
.meta { color: #555; font-size: 0.95rem; margin-bottom: 1.5rem; }
"""


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
def privacy_policy_page() -> HTMLResponse:
    """Public privacy policy for App Store / Play Console."""
    body = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Privacy Policy — Megaannum Technology Limited</title>
  <style>{_PAGE_STYLE}</style>
</head>
<body>
  <h1>Privacy Policy</h1>
  <p class="meta">E-Business Card<br />
  Megaannum Technology Limited<br />
  Last updated: 15 July 2026</p>

  <h2>1. Introduction</h2>
  <p>This Privacy Policy explains how Megaannum Technology Limited (“we”, “us”, or “our”)
  collects, uses, stores, and shares information when you use the E-Business Card
  mobile application and related backend services (the “Service”).</p>
  <p>By creating an account or using the Service, you agree to this Privacy Policy
  and our <a href="/terms">Terms of Service</a>.</p>

  <h2>2. Information we collect</h2>
  <p><strong>Account information.</strong> When you sign in, we collect your
  <strong>email address</strong> through Firebase Authentication. We do not collect
  a phone number, legal name, gender, or other registration profile fields for your account.</p>
  <p><strong>Service content you provide.</strong> To operate the Service, we store
  content that you create or capture:</p>
  <ul>
    <li>Business cards you create for yourself (for example name, company, job title,
      email, phone, website, and optional custom fields).</li>
    <li>Business cards you scan or import (contact details parsed from the card,
      optional front/back scan images, and OCR text produced on your device).</li>
    <li>Display preferences (for example card design and wallet display options).</li>
    <li>Share-link metadata (link token, whether the link is active, view count,
      and last viewed time).</li>
  </ul>
  <p><strong>Account identifier.</strong> Your data is associated with a Firebase
  user ID so we can keep your cards private to your account.</p>
  <p>We do not intentionally collect device advertising identifiers, payment card
  details, precise location, or crash analytics in this Service.</p>

  <h2>3. How we use information</h2>
  <ul>
    <li><strong>Provide the Service:</strong> authenticate you, store your cards,
      and sync them across your signed-in sessions.</li>
    <li><strong>Card scanning:</strong> process on-device OCR text with third-party
      AI models via OpenRouter to suggest structured contact fields. When image
      enhancement is enabled, scan images are also sent through OpenRouter to
      straighten the card and clean its outer edges before storage.</li>
    <li><strong>Sharing:</strong> when you create a share link, show your shared card
      (including contact details and scan images, if present) to anyone who opens
      that link, and allow download of a vCard / import into another user’s collection.</li>
    <li><strong>Abuse prevention:</strong> apply per-account usage limits to language-model
      parsing requests.</li>
    <li><strong>Account deletion:</strong> process your request to delete your account
      and associated Service data.</li>
  </ul>

  <h2>4. How we share information</h2>
  <ul>
    <li><strong>Google Firebase:</strong> authentication (email-based sign-in) and
      account management.</li>
    <li><strong>Our database (MongoDB):</strong> stores your account-linked cards,
      OCR text, scan images, share links, and usage counters.</li>
    <li><strong>OpenRouter:</strong> receives OCR text for structured parsing and,
      when image enhancement is enabled, scan images for edge cleanup and
      perspective correction.</li>
    <li><strong>People you share with:</strong> anyone with an active share link can
      view the shared card details and images.</li>
    <li><strong>Legal requirements:</strong> we may disclose information if required
      by applicable law or lawful authority.</li>
  </ul>
  <p>We do not sell your personal information. We do not use third-party payment
  processors in this Service.</p>

  <h2>5. Retention and deletion</h2>
  <p>We keep your information while your account is active and as needed to provide
  the Service, unless a longer period is required by law.</p>
  <p><strong>Account deletion.</strong> You may delete your account from the app
  (account settings). When deletion is confirmed, we delete your Firebase Auth
  account and remove associated Service data we store for you, including your
  cards, scan images, and share links. This action cannot be undone.</p>
  <p>If a shared card was imported by another user before your account was deleted,
  that copy may remain in that other user’s collection.</p>

  <h2>6. Your rights</h2>
  <p>Depending on where you live, you may have rights to access, correct, or delete
  personal information we hold about you. You can delete your account in the app.
  For other requests, contact us using the details below.</p>

  <h2>7. Children’s privacy</h2>
  <p>The Service is not directed to children under 13 (or the equivalent minimum
  age in your jurisdiction). We do not knowingly collect personal information from children.</p>

  <h2>8. Changes to this policy</h2>
  <p>We may update this Privacy Policy from time to time. The “Last updated” date
  at the top will change when we do. Continued use of the Service after an update
  means you accept the revised policy.</p>

  <h2>9. Contact us</h2>
  <p>Megaannum Technology Limited<br />
  Email: <a href="mailto:developer@megaannum.ai">developer@megaannum.ai</a><</p>
</body>
</html>
"""
    return HTMLResponse(content=body)
