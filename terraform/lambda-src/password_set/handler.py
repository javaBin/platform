"""Self-service password-set flow for new hero Google Workspace accounts.

Routes (via Lambda Function URL):
- GET  /set-password?token=...  → Serve password-set HTML form
- POST /set-password            → Set password via team-provisioner Lambda invoke

Password-set tokens are HMAC-SHA256 signed with single-use enforcement via DynamoDB.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
import uuid

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")
dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")

SIGNING_KEY_PARAM = os.environ.get(
    "SIGNING_KEY_PARAM", "/javabin/platform/password-token-signing-key"
)
DEDUP_TABLE_NAME = os.environ.get("DEDUP_TABLE_NAME", "javabin-alert-dedup")
TEAM_PROVISIONER_FUNCTION = os.environ.get(
    "TEAM_PROVISIONER_FUNCTION", "javabin-team-provisioner"
)
PASSWORD_TOKEN_TTL = 48 * 3600  # 48 hours

_secret_cache = {}


def _get_secret(param_name):
    if param_name not in _secret_cache:
        resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
        _secret_cache[param_name] = resp["Parameter"]["Value"]
    return _secret_cache[param_name]


def _dedup_table():
    return dynamodb.Table(DEDUP_TABLE_NAME)


# ---------------------------------------------------------------------------
# Password-set token helpers
# ---------------------------------------------------------------------------
def _b64url_encode(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s):
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def generate_password_token(email, signing_key):
    """Generate an HMAC-signed password-set token.

    Token format: base64url(json_payload).hmac_hex
    Payload: {"email": "...", "exp": unix_ts, "jti": "uuid"}
    """
    payload = json.dumps({
        "email": email,
        "exp": int(time.time()) + PASSWORD_TOKEN_TTL,
        "jti": str(uuid.uuid4()),
    })
    payload_b64 = _b64url_encode(payload)
    sig = hmac.new(
        signing_key.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload_b64}.{sig}"


def _validate_token(token):
    """Validate a password-set token. Returns (payload_dict, error_string)."""
    signing_key = _get_secret(SIGNING_KEY_PARAM)

    parts = token.split(".")
    if len(parts) != 2:
        return None, "Invalid token format"

    payload_b64, sig = parts

    # Verify signature
    expected_sig = hmac.new(
        signing_key.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        return None, "Invalid token signature"

    # Decode payload
    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        return None, "Invalid token payload"

    # Check expiry
    if time.time() > payload.get("exp", 0):
        return None, "Token has expired"

    # Check single-use via DynamoDB
    jti = payload.get("jti", "")
    dedup_key = f"pwset:{jti}"
    try:
        resp = _dedup_table().get_item(Key={"finding_key": dedup_key})
        if "Item" in resp:
            return None, "This link has already been used"
    except Exception as e:
        logger.warning("DynamoDB single-use check failed: %s", e)

    return payload, None


def _mark_token_used(jti):
    """Mark a password-set token as used in DynamoDB."""
    try:
        _dedup_table().put_item(Item={
            "finding_key": f"pwset:{jti}",
            "used_at": int(time.time()),
            "expires_at": int(time.time()) + (30 * 86400),
        })
    except Exception as e:
        logger.warning("Failed to mark token used: %s", e)


# ---------------------------------------------------------------------------
# Password-set form HTML
# ---------------------------------------------------------------------------
_PASSWORD_FORM_HTML = """\
<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sett passord &mdash; javaBin</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:#f5f5f5;min-height:100vh;display:flex;align-items:center;justify-content:center}
  .card{background:#fff;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);
        max-width:440px;width:100%;overflow:hidden}
  .hdr{background:#f05350;padding:24px 32px;text-align:center}
  .hdr img{height:40px}
  .bd{padding:32px}
  h1{color:#f05350;font-size:20px;margin-bottom:8px}
  .email{color:#666;font-size:14px;margin-bottom:24px}
  label{display:block;font-size:14px;font-weight:600;color:#333;margin-bottom:6px}
  input[type=password]{width:100%;padding:10px 12px;border:1px solid #d1d5db;border-radius:6px;
                       font-size:15px;margin-bottom:16px}
  input[type=password]:focus{outline:none;border-color:#f05350;box-shadow:0 0 0 2px rgba(240,83,80,.2)}
  .btn{width:100%;padding:12px;background:#f05350;color:#fff;border:none;border-radius:6px;
       font-size:15px;font-weight:600;cursor:pointer}
  .btn:hover{background:#d94442}
  .btn:disabled{opacity:.5;cursor:not-allowed}
  .rules{font-size:13px;color:#888;margin-bottom:20px}
  .rules span{display:block;margin:2px 0}
  .pass{color:#22c55e}
  .fail{color:#ef4444}
  .error{color:#ef4444;font-size:14px;margin-bottom:16px;display:none}
  .ft{background:#f8f9fa;padding:16px 32px;border-top:1px solid #e9ecef;
      text-align:center;font-size:12px;color:#999}
  .ft a{color:#666;text-decoration:none}
</style>
</head>
<body>
<div class="card">
  <div class="hdr">
    <img src="https://java.no/_next/static/media/javaBin-logo-horizontal-WHITE.434dba55.png" alt="javaBin"/>
  </div>
  <div class="bd">
    <h1>Sett passord</h1>
    <p class="email">{{EMAIL}}</p>
    <form id="f" method="POST" action="/set-password">
      <input type="hidden" name="token" value="{{TOKEN}}"/>
      <label for="pw1">Nytt passord</label>
      <input type="password" id="pw1" name="password" required minlength="12" autocomplete="new-password"/>
      <label for="pw2">Bekreft passord</label>
      <input type="password" id="pw2" name="password_confirm" required minlength="12" autocomplete="new-password"/>
      <div class="rules" id="rules">
        <span id="r-len" class="fail">&#x2717; Minst 12 tegn</span>
        <span id="r-upper" class="fail">&#x2717; Stor bokstav</span>
        <span id="r-lower" class="fail">&#x2717; Liten bokstav</span>
        <span id="r-num" class="fail">&#x2717; Tall</span>
      </div>
      <div class="error" id="err"></div>
      <button class="btn" type="submit" id="sub" disabled>Sett passord</button>
    </form>
  </div>
  <div class="ft">JavaBin &mdash; Javabrukerforeningen i Norge &middot; <a href="https://java.no">java.no</a> &middot; <a href="https://javazone.no">javazone.no</a></div>
</div>
<script>
const p1=document.getElementById('pw1'),p2=document.getElementById('pw2'),
      btn=document.getElementById('sub'),err=document.getElementById('err');
const ids=['r-len','r-upper','r-lower','r-num'];
const tests=[v=>v.length>=12,v=>/[A-Z]/.test(v),v=>/[a-z]/.test(v),v=>/[0-9]/.test(v)];
function ck(){
  const v=p1.value,ok=tests.map(t=>t(v));
  ids.forEach((id,i)=>{const e=document.getElementById(id);
    e.className=ok[i]?'pass':'fail';
    e.innerHTML=(ok[i]?'&#x2713;':'&#x2717;')+e.textContent.slice(1);});
  const match=!p2.value||p1.value===p2.value;
  err.style.display=match?'none':'block';
  err.textContent=match?'':'Passordene er ikke like';
  btn.disabled=!(ok.every(Boolean)&&p1.value===p2.value&&p2.value);
}
p1.addEventListener('input',ck);p2.addEventListener('input',ck);
document.getElementById('f').addEventListener('submit',function(e){
  if(p1.value!==p2.value){e.preventDefault();err.textContent='Passordene er ikke like';err.style.display='block';}
});
</script>
</body>
</html>"""

_PASSWORD_SUCCESS_HTML = """\
<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Passord satt &mdash; javaBin</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:#f5f5f5;min-height:100vh;display:flex;align-items:center;justify-content:center}
  .card{background:#fff;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);
        max-width:440px;width:100%;overflow:hidden;text-align:center}
  .hdr{background:#f05350;padding:24px 32px}
  .hdr img{height:40px}
  .bd{padding:40px 32px}
  h1{color:#22c55e;font-size:22px;margin-bottom:12px}
  p{color:#333;font-size:15px;line-height:1.6;margin-bottom:24px}
  .btn{display:inline-block;padding:12px 32px;background:#f05350;color:#fff;border-radius:6px;
       font-size:15px;font-weight:600;text-decoration:none}
  .btn:hover{background:#d94442}
  .ft{background:#f8f9fa;padding:16px 32px;border-top:1px solid #e9ecef;
      font-size:12px;color:#999}
  .ft a{color:#666;text-decoration:none}
</style>
</head>
<body>
<div class="card">
  <div class="hdr">
    <img src="https://java.no/_next/static/media/javaBin-logo-horizontal-WHITE.434dba55.png" alt="javaBin"/>
  </div>
  <div class="bd">
    <h1>&#x2713; Passord satt!</h1>
    <p>Passordet ditt er oppdatert. Du kan n&aring; logge inn med din javaBin-konto.</p>
    <a class="btn" href="https://accounts.google.com">Logg inn</a>
  </div>
  <div class="ft">JavaBin &mdash; Javabrukerforeningen i Norge &middot; <a href="https://java.no">java.no</a> &middot; <a href="https://javazone.no">javazone.no</a></div>
</div>
</body>
</html>"""

_PASSWORD_ERROR_HTML = """\
<!DOCTYPE html>
<html lang="no">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Feil &mdash; javaBin</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:#f5f5f5;min-height:100vh;display:flex;align-items:center;justify-content:center}
  .card{background:#fff;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);
        max-width:440px;width:100%;overflow:hidden;text-align:center}
  .hdr{background:#f05350;padding:24px 32px}
  .hdr img{height:40px}
  .bd{padding:40px 32px}
  h1{color:#ef4444;font-size:22px;margin-bottom:12px}
  p{color:#333;font-size:15px;line-height:1.6}
  .ft{background:#f8f9fa;padding:16px 32px;border-top:1px solid #e9ecef;
      font-size:12px;color:#999;margin-top:24px}
  .ft a{color:#666;text-decoration:none}
</style>
</head>
<body>
<div class="card">
  <div class="hdr">
    <img src="https://java.no/_next/static/media/javaBin-logo-horizontal-WHITE.434dba55.png" alt="javaBin"/>
  </div>
  <div class="bd">
    <h1>&#x2717; {{TITLE}}</h1>
    <p>{{MESSAGE}}</p>
  </div>
  <div class="ft">JavaBin &mdash; Javabrukerforeningen i Norge &middot; <a href="https://java.no">java.no</a> &middot; <a href="https://javazone.no">javazone.no</a></div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Password-set handlers
# ---------------------------------------------------------------------------
def _handle_get_password_form(query_params):
    """Serve the password-set HTML form after validating the token."""
    token = query_params.get("token", "")
    if not token:
        return _html_error(400, "Ugyldig lenke", "Ingen token funnet i lenken.")

    payload, error = _validate_token(token)
    if error:
        return _html_error(400, "Lenken er ugyldig", error)

    email = payload.get("email", "")
    html = _PASSWORD_FORM_HTML.replace("{{EMAIL}}", email).replace("{{TOKEN}}", token)
    return _html_response(200, html)


def _handle_post_password(body):
    """Process password-set form submission."""
    parsed = urllib.parse.parse_qs(body)
    token = parsed.get("token", [""])[0]
    password = parsed.get("password", [""])[0]
    password_confirm = parsed.get("password_confirm", [""])[0]

    if not token or not password:
        return _html_error(400, "Mangler data", "Token og passord er p\u00e5krevd.")

    if password != password_confirm:
        return _html_error(
            400, "Passordene er ikke like", "Vennligst g\u00e5 tilbake og pr\u00f8v igjen."
        )

    if (
        len(password) < 12
        or not any(c.isupper() for c in password)
        or not any(c.islower() for c in password)
        or not any(c.isdigit() for c in password)
    ):
        return _html_error(
            400,
            "Passordet er for svakt",
            "Minst 12 tegn, stor bokstav, liten bokstav og tall.",
        )

    payload, error = _validate_token(token)
    if error:
        return _html_error(400, "Lenken er ugyldig", error)

    email = payload.get("email", "")
    jti = payload.get("jti", "")

    # Set password via team-provisioner Lambda
    try:
        resp = lambda_client.invoke(
            FunctionName=TEAM_PROVISIONER_FUNCTION,
            InvocationType="RequestResponse",
            Payload=json.dumps({
                "action": "set_password",
                "email": email,
                "password": password,
            }),
        )
        result = json.loads(resp["Payload"].read())
        if result.get("statusCode") != 200:
            raise RuntimeError(result.get("body", "Unknown error"))
    except Exception as e:
        logger.error("Password set failed for %s: %s", email, e)
        return _html_error(
            500,
            "Noe gikk galt",
            "Kunne ikke sette passord. Kontakt en administrator.",
        )

    _mark_token_used(jti)
    logger.info("Password set for %s via self-service link", email)
    return _html_response(200, _PASSWORD_SUCCESS_HTML)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------
def _json_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _html_response(status_code, html):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": html,
    }


def _html_error(status_code, title, message):
    html = _PASSWORD_ERROR_HTML.replace("{{TITLE}}", title).replace(
        "{{MESSAGE}}", message
    )
    return _html_response(status_code, html)


# ---------------------------------------------------------------------------
# Lambda entry point (Function URL + ALB)
# ---------------------------------------------------------------------------
def _is_alb_event(event):
    """Detect ALB invocation by checking for elb in requestContext."""
    return "elb" in event.get("requestContext", {})


def _alb_response(response):
    """Wrap a Function URL-style response for ALB format."""
    response.setdefault("isBase64Encoded", False)
    status = response.get("statusCode", 200)
    response["statusDescription"] = f"{status} OK" if status < 400 else f"{status} Error"
    return response


def handler(event, context):
    """Route by HTTP method + path. Supports both Function URL and ALB events."""
    is_alb = _is_alb_event(event)

    if is_alb:
        method = event.get("httpMethod", "GET")
        path = event.get("path", "/")
    else:
        http = event.get("requestContext", {}).get("http", {})
        method = http.get("method", "GET")
        path = event.get("rawPath", "/")

    query_params = event.get("queryStringParameters") or {}

    # Decode body
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")

    # GET /set-password → password form
    if method == "GET" and path == "/set-password":
        result = _handle_get_password_form(query_params)
    # POST /set-password → set password
    elif method == "POST" and path == "/set-password":
        result = _handle_post_password(body)
    else:
        result = _json_response(404, {"error": "not found"})

    return _alb_response(result) if is_alb else result
