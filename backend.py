#!/usr/bin/env python3
"""Backend for the AnyConnect SSO widget.

Cisco ASA / FTD gateways that use SAML with the *embedded* browser can't be
signed in to by the OpenConnect CLI ("No SSO handler"). This does what the
Cisco client does: asks the gateway for the SAML login page, shows it in a
WebKit window for the identity provider's sign-in and MFA, trades the
resulting token for a VPN session, then hands the session to NetworkManager
(networkmanager-openconnect) so no root prompt is needed.

  backend.py status                      JSON: state, server, group, missing packages
  backend.py groups <server>             JSON: the gateway's group list
  backend.py configure <server> <group>  save settings
  backend.py connect                     sign in and bring the tunnel up
  backend.py disconnect                  take the tunnel down

connect/configure/disconnect print progress lines for the panel:
"STEP <text>", "OK <text>" or "ERROR <text>". Details go to the log file;
tokens and session cookies are never written to it.
"""
import http.cookiejar
import importlib.util
import json
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from xml.sax.saxutils import escape

PLUGIN_ID = "cikr-repos.anyconnect-sso"
PROFILE = "AnyConnect SSO"
CLIENT_VERSION = "4.10.07061"
USER_AGENT = "AnyConnect Linux_64 " + CLIENT_VERSION

STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
                         "omarchy", "plugins", PLUGIN_ID)
CONFIG = os.path.join(STATE_DIR, "config.json")
LOG = os.path.join(STATE_DIR, "last.log")
BROWSER_DIR = os.path.join(STATE_DIR, "browser")


def say(kind, text):
    print(kind, text, flush=True)


def log(text):
    with open(LOG, "a") as f:
        f.write("%s %s\n" % (datetime.now().strftime("%H:%M:%S"), text))


def load_config():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def normalize_server(server):
    """Host, or host/path for gateways that pick the group by URL (vpn.example.com/Employees)."""
    server = server.strip()
    for prefix in ("https://", "http://"):
        if server.lower().startswith(prefix):
            server = server[len(prefix):]
    return server.strip("/")


def server_host(server):
    return server.split("/", 1)[0]


# ---- dependencies --------------------------------------------------------------

def missing_packages():
    missing = []
    if not shutil.which("nmcli"):
        missing.append("networkmanager")
    if not os.path.exists("/usr/lib/NetworkManager/VPN/nm-openconnect-service.name"):
        missing.append("networkmanager-openconnect")
    if not shutil.which("openssl"):
        missing.append("openssl")
    if importlib.util.find_spec("gi") is None:
        missing += ["python-gobject", "webkit2gtk-4.1"]
    else:
        try:
            import gi
            gi.require_version("WebKit2", "4.1")
        except ValueError:
            missing.append("webkit2gtk-4.1")
    return missing


# ---- gateway protocol (AnyConnect aggregate auth) -----------------------------

def gateway_post(opener, server, body):
    req = urllib.request.Request("https://%s/" % server, data=body.encode(), method="POST", headers={
        "User-Agent": USER_AGENT,
        "X-Transcend-Version": "1",
        "X-Aggregate-Auth": "1",
        "X-Support-HTTP-Auth": "true",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
    })
    with opener.open(req, timeout=30) as resp:
        return ET.fromstring(resp.read())


def init_request(server, group):
    select = "<group-select>%s</group-select>" % escape(group) if group else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<config-auth client="vpn" type="init" aggregate-auth-version="2">'
            '<version who="vpn">%s</version><device-id>linux-64</device-id>%s'
            '<capabilities><auth-method>single-sign-on-v2</auth-method></capabilities>'
            '<group-access>https://%s</group-access></config-auth>') % (CLIENT_VERSION, select, escape(server))


def reply_request(opaque, token):
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<config-auth client="vpn" type="auth-reply" aggregate-auth-version="2">'
            '<version who="vpn">%s</version><device-id>linux-64</device-id>'
            '<session-token/><session-id/>%s<auth><sso-token>%s</sso-token></auth>'
            '</config-auth>') % (CLIENT_VERSION, opaque, escape(token))


def new_opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def server_pin(server):
    """pin-sha256 of the gateway's public key, a form OpenConnect's --servercert accepts."""
    pem = ssl.get_server_certificate((server_host(server), 443)).encode()
    pubkey = subprocess.run(["openssl", "x509", "-pubkey", "-noout"], input=pem,
                            capture_output=True, check=True).stdout
    der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "der"], input=pubkey,
                         capture_output=True, check=True).stdout
    digest = subprocess.run(["openssl", "dgst", "-sha256", "-binary"], input=der,
                            capture_output=True, check=True).stdout
    import base64
    return "pin-sha256:" + base64.b64encode(digest).decode()


# ---- embedded browser ---------------------------------------------------------

def browser_login(login_url, final_url, token_cookie):
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, WebKit2

    os.makedirs(BROWSER_DIR, exist_ok=True)
    result = {"token": None}
    manager = WebKit2.WebsiteDataManager(base_data_directory=BROWSER_DIR, base_cache_directory=BROWSER_DIR)
    context = WebKit2.WebContext.new_with_website_data_manager(manager)
    cookies = context.get_cookie_manager()
    # Keeps the identity provider's "stay signed in" cookies between connects.
    cookies.set_persistent_storage(os.path.join(BROWSER_DIR, "cookies.sqlite"),
                                   WebKit2.CookiePersistentStorage.SQLITE)

    view = WebKit2.WebView.new_with_context(context)
    window = Gtk.Window(title="VPN sign-in")
    window.set_default_size(520, 720)
    window.add(view)
    window.connect("destroy", lambda *_: Gtk.main_quit())

    def got_cookies(manager, task):
        for cookie in manager.get_cookies_finish(task):
            if cookie.get_name() == token_cookie:
                result["token"] = cookie.get_value()
                window.destroy()
                return

    def on_load(view, event):
        if event != WebKit2.LoadEvent.FINISHED:
            return
        uri = view.get_uri() or ""
        # No query string: it can carry SAML data.
        log("page: %s [%s]" % (uri.split("?")[0], view.get_title() or ""))
        if uri.startswith(final_url):
            cookies.get_cookies(final_url, None, got_cookies)

    def on_failed(view, event, uri, error):
        log("page failed: %s: %s" % (uri.split("?")[0], error.message))
        return False

    view.connect("load-changed", on_load)
    view.connect("load-failed", on_failed)
    view.load_uri(login_url)
    window.show_all()
    Gtk.main()
    return result["token"]


# ---- NetworkManager -----------------------------------------------------------

def nmcli(*args, **kwargs):
    return subprocess.run(["nmcli", *args], capture_output=True, text=True, **kwargs)


def ensure_profile(server):
    data = "gateway=%s,protocol=anyconnect,enable_csd_trojan=no,cookie-flags=2,gateway-flags=2,gwcert-flags=2" % server
    if nmcli("-t", "-f", "NAME", "connection", "show").stdout.splitlines().count(PROFILE):
        res = nmcli("connection", "modify", PROFILE, "vpn.data", data)
    else:
        res = nmcli("connection", "add", "type", "vpn", "con-name", PROFILE, "autoconnect", "no",
                    "vpn-type", "openconnect", "vpn.data", data)
    if res.returncode != 0:
        log("nmcli: " + (res.stderr or res.stdout).strip())
    return res.returncode == 0


def vpn_state():
    state = nmcli("-t", "-g", "GENERAL.STATE", "connection", "show", PROFILE).stdout.strip()
    return {"activated": "connected", "activating": "connecting"}.get(state, "disconnected")


def bring_up(server, cookie, fingerprint):
    secrets = "vpn.secrets.cookie:%s\nvpn.secrets.gateway:%s\nvpn.secrets.gwcert:%s\n" % (cookie, server_host(server), fingerprint)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, secrets.encode())
    os.close(write_fd)
    try:
        res = nmcli("connection", "up", PROFILE, "passwd-file", "/dev/fd/%d" % read_fd, pass_fds=(read_fd,))
    finally:
        os.close(read_fd)
    log("nmcli: " + (res.stdout or res.stderr).strip())
    return res.returncode == 0


# ---- commands -----------------------------------------------------------------

def cmd_status():
    config = load_config()
    print(json.dumps({
        "state": vpn_state() if shutil.which("nmcli") else "disconnected",
        "server": config.get("server", ""),
        "group": config.get("group", ""),
        "missing": missing_packages(),
    }))


def cmd_groups(server):
    server = normalize_server(server)
    try:
        root = gateway_post(new_opener(), server, init_request(server, ""))
    except (OSError, ET.ParseError) as e:
        print(json.dumps({"error": "Could not reach %s: %s" % (server, getattr(e, "reason", e))}))
        return 1
    select = root.find(".//select[@name='group_list']")
    groups = [opt.text.strip() for opt in select.findall("option") if opt.text] if select is not None else []
    # No dropdown: the server (or its URL path) decides the group, so none is saved.
    current = (root.findtext(".//tunnel-group") or groups[0]) if groups else ""
    sso = root.find(".//sso-v2-login") is not None
    print(json.dumps({"server": server, "groups": groups, "default": current, "sso": sso}))
    return 0


def cmd_configure(server, group):
    server = normalize_server(server)
    if not server:
        say("ERROR", "Enter the VPN server address")
        return 1
    with open(CONFIG, "w") as f:
        json.dump({"server": server, "group": group.strip()}, f, indent=2)
    if shutil.which("nmcli") and not ensure_profile(server):
        say("ERROR", "Saved, but the NetworkManager profile could not be updated")
        return 1
    say("OK", "Saved")
    return 0


def cmd_connect():
    open(LOG, "w").close()
    config = load_config()
    server, group = config.get("server", ""), config.get("group", "")
    if not server:
        say("ERROR", "Set the VPN server in Settings first")
        return 1
    missing = missing_packages()
    if missing:
        say("ERROR", "Missing packages: " + ", ".join(missing))
        return 1
    if not ensure_profile(server):
        say("ERROR", "Could not create the NetworkManager profile")
        return 1

    opener = new_opener()
    try:
        log("init: server %s, group %s" % (server, group or "(default)"))
        root = gateway_post(opener, server, init_request(server, group))
        login = root.findtext(".//sso-v2-login")
        final = root.findtext(".//sso-v2-login-final")
        token_cookie = root.findtext(".//sso-v2-token-cookie-name") or "acSamlv2Token"
        opaque = root.find("opaque")
        if not login or not final or opaque is None:
            reason = root.findtext(".//error") or root.findtext(".//message") or "the gateway did not offer SSO sign-in"
            log("init failed: " + reason)
            say("ERROR", "Sign-in not available: " + reason)
            return 1
        log("init: tunnel group %s" % root.findtext(".//tunnel-group"))

        say("STEP", "Sign in in the window that opened…")
        token = browser_login(login, final, token_cookie)
        if not token:
            log("sign-in window closed before sign-in finished")
            say("ERROR", "Sign-in was cancelled")
            return 1

        reply = gateway_post(opener, server, reply_request(ET.tostring(opaque, encoding="unicode"), token))
        session = reply.findtext(".//session-token")
        if reply.get("type") != "complete" or not session:
            reason = reply.findtext(".//error") or reply.findtext(".//message") or reply.get("type")
            log("auth-reply failed: %s" % reason)
            say("ERROR", "The gateway refused the sign-in: %s" % reason)
            return 1
        log("auth-reply: session granted")
        fingerprint = server_pin(server)
    except (OSError, ET.ParseError, subprocess.CalledProcessError) as e:
        log("error: %s" % getattr(e, "reason", e))
        say("ERROR", "Could not talk to %s: %s" % (server, getattr(e, "reason", e)))
        return 1

    say("STEP", "Signed in, starting the tunnel…")
    if not bring_up(server, session, fingerprint):
        say("ERROR", "Signed in, but the tunnel did not start (see the log)")
        return 1
    say("OK", "Connected")
    return 0


def cmd_disconnect():
    res = nmcli("connection", "down", PROFILE)
    log("nmcli: " + (res.stdout or res.stderr).strip())
    say("OK", "Disconnected")
    return 0


def main(argv):
    os.makedirs(STATE_DIR, exist_ok=True)
    cmd, args = (argv[0] if argv else ""), argv[1:]
    if cmd == "status":
        return cmd_status()
    if cmd == "groups" and len(args) == 1:
        return cmd_groups(args[0])
    if cmd == "configure" and len(args) == 2:
        return cmd_configure(*args)
    if cmd == "connect":
        return cmd_connect()
    if cmd == "disconnect":
        return cmd_disconnect()
    print(__doc__.split("\n\n")[2], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
