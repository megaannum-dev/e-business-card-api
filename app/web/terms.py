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


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
def terms_of_service_page() -> HTMLResponse:
    """Public terms of service for App Store / Play Console."""
    body = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Terms of Service — Megaannum Technology Limited</title>
  <style>{_PAGE_STYLE}</style>
</head>
<body>
  <h1>Terms of Service</h1>
  <p class="meta">E-Business Card<br />
  Megaannum Technology Limited<br />
  Last updated: 15 July 2026</p>

  <h2>1. Agreement to these terms</h2>
  <p>These Terms of Service (“Terms”) govern your access to and use of the
  E-Business Card mobile application and related backend services (the “Service”)
  provided by Megaannum Technology Limited (“we”, “us”, or “our”).</p>
  <p>By creating an account or using the Service, you agree to these Terms and to
  our <a href="/privacy">Privacy Policy</a>. If you do not agree, do not use the Service.</p>

  <h2>2. The Service</h2>
  <p>E-Business Card lets you create and manage digital business cards, scan and
  store contact information from physical cards, optionally enhance scanned text
  with automated parsing, and share your cards via public links (including HTML
  pages, images, and vCard downloads).</p>
  <p>We may change, suspend, or discontinue features of the Service at any time,
  including usage limits for automated parsing.</p>

  <h2>3. Eligibility and accounts</h2>
  <ul>
    <li>You must be at least 13 years old (or the minimum age required in your
      jurisdiction) to use the Service.</li>
    <li>You sign in with an email-based account (via Firebase Authentication).
      You are responsible for keeping your sign-in credentials secure and for
      activity under your account.</li>
    <li>You must provide accurate account information and update it as needed.</li>
  </ul>

  <h2>4. Your content</h2>
  <p>You retain ownership of content you submit to the Service, including business
  card details, scan images, OCR text, and custom fields (“Your Content”).</p>
  <p>You grant us a limited licence to host, process, display, and transmit Your
  Content solely to operate and improve the Service (for example storing cards,
  generating share pages, and sending OCR text or scan images to our AI provider
  for parsing or image enhancement when you use those features).</p>
  <p>You are solely responsible for Your Content and for having the rights to
  submit it. Do not upload or share content that you are not authorised to use,
  or that is unlawful, misleading, or infringes others’ rights.</p>

  <h2>5. Sharing</h2>
  <p>If you create a share link, anyone with that link may view the shared card
  details and images, download a vCard, and (where available) import the card
  into their own collection. You control whether a link is active; you are
  responsible for who you share links with and for revoking links you no longer
  want to be public.</p>
  <p>If another user imported your shared card before you revoke the link or
  delete your account, their stored copy may remain in their account.</p>

  <h2>6. Automated parsing (OCR / AI)</h2>
  <p>When you use scan or enhance features, OCR text from your device may be sent
  to a third-party language-model service (via OpenRouter) to suggest structured
  contact fields. When image enhancement is enabled, scan images are also sent
  through OpenRouter for perspective correction and outer-edge cleanup before
  the resulting image is stored.</p>
  <p>Suggestions may be incomplete or incorrect. You should review and edit
  results before relying on them. We do not guarantee the accuracy of automated
  parsing.</p>

  <h2>7. Acceptable use</h2>
  <p>You agree not to:</p>
  <ul>
    <li>use the Service for unlawful, fraudulent, or harmful purposes;</li>
    <li>attempt to access other users’ accounts or non-public data;</li>
    <li>interfere with or disrupt the Service, including by overloading APIs
      or circumventing rate limits;</li>
    <li>reverse engineer or scrape the Service except as allowed by law;</li>
    <li>use the Service to spam, harass, or distribute malware;</li>
    <li>misrepresent your identity or affiliation when creating or sharing cards.</li>
  </ul>

  <h2>8. Intellectual property</h2>
  <p>The Service, including software, design, branding, and documentation (other
  than Your Content), is owned by Megaannum Technology Limited or its licensors.
  These Terms do not grant you any ownership rights in the Service.</p>

  <h2>9. Third-party services</h2>
  <p>The Service relies on third parties such as Google Firebase (authentication),
  our database hosting, and OpenRouter (language-model requests). Their terms and
  availability may affect the Service. We are not responsible for third-party
  outages or changes outside our reasonable control.</p>

  <h2>10. Disclaimer</h2>
  <p>The Service is provided “as is” and “as available”. To the fullest extent
  permitted by law, we disclaim warranties of merchantability, fitness for a
  particular purpose, and non-infringement. We do not warrant that the Service
  will be uninterrupted, error-free, or that stored data will never be lost.</p>

  <h2>11. Limitation of liability</h2>
  <p>To the fullest extent permitted by law, Megaannum Technology Limited and its
  directors, employees, and agents will not be liable for any indirect,
  incidental, special, consequential, or punitive damages, or for loss of
  profits, data, or goodwill, arising from your use of the Service.</p>
  <p>Our total liability for any claim relating to the Service will not exceed
  the greater of (a) the amount you paid us for the Service in the 12 months
  before the claim, or (b) USD 50. Some jurisdictions do not allow certain
  limitations; in those cases, our liability is limited to the maximum extent
  permitted by law.</p>

  <h2>12. Termination</h2>
  <p>You may stop using the Service at any time and may delete your account in
  the app. When you delete your account, we delete your authentication account
  and associated Service data we store for you, as described in our Privacy Policy,
  subject to copies that other users may already have imported.</p>
  <p>We may suspend or terminate your access if you breach these Terms, misuse
  the Service, or if we need to do so for legal, security, or operational reasons.</p>

  <h2>13. Changes to these Terms</h2>
  <p>We may update these Terms from time to time. The “Last updated” date at the
  top will change when we do. Continued use of the Service after an update means
  you accept the revised Terms. If you do not agree, stop using the Service and
  delete your account.</p>

  <h2>14. Governing law</h2>
  <p>These Terms are governed by the laws of the Hong Kong Special Administrative
  Region, without regard to conflict-of-law rules. Courts in Hong Kong will have
  exclusive jurisdiction, except where mandatory consumer protections in your
  place of residence provide otherwise.</p>

  <h2>15. Contact</h2>
  <p>Megaannum Technology Limited<br />
  Email: <a href="mailto:developer@megaannum.ai">developer@megaannum.ai</a></p>
</body>
</html>
"""
    return HTMLResponse(content=body)
