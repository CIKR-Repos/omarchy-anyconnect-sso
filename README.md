# AnyConnect SSO for Omarchy

A bar widget for Cisco AnyConnect / Secure Client VPNs that sign in through
**SAML single sign-on** (Microsoft Entra ID, Okta, Duo, …) with MFA, including
gateways that offer a **group** dropdown.

## Why this exists

Many Cisco ASA / FTD gateways only allow SAML sign-in through the Cisco
client's *embedded* browser. The OpenConnect CLI, and the plugins built on
it, then fail with `No SSO handler` or `Unknown response from server`,
even with `--external-browser`.

This plugin does what the Cisco client does:

1. Asks the gateway for the sign-in page of the group you chose.
2. Opens it in a small WebKit window where you sign in and approve MFA.
3. Trades the resulting SSO token for a VPN session.
4. Hands the session to NetworkManager (`networkmanager-openconnect`), which
   brings the tunnel up. No `sudo` and no terminal.

## Install

```sh
omarchy plugin add https://github.com/CIKR-Repos/omarchy-anyconnect-sso.git --enable
```

If something is missing, the panel offers an **Install packages** button,
which installs these packages:

```sh
omarchy pkg add networkmanager-openconnect webkit2gtk-4.1 python-gobject
```

## Use

1. Click the shield icon in the bar, then **Set up**.
2. Give the connection a name (shown in the panel), enter your VPN server (for example `vpn.example.com`) and click
   **Load groups**. The widget asks the server for its group list.
3. Pick your group and click **Save**. If the server has no group list, there's
   nothing to pick and the server chooses for you. If your company gave you a
   group address like `vpn.example.com/Employees`, enter that as the server.
4. Flip the switch. Sign in and approve MFA in the window that opens. The
   window closes on its own and the VPN connects.

Middle-click the bar icon to connect or disconnect without opening the panel.
Your identity provider's "stay signed in" cookie is kept, so later connects
may only need the MFA approval.

From a terminal or keybinding:

```sh
omarchy-shell cikr-repos.anyconnect-sso connect
omarchy-shell cikr-repos.anyconnect-sso disconnect
omarchy-shell cikr-repos.anyconnect-sso status
```

## Where things are stored

| What | Where |
|---|---|
| Server and group | `~/.local/state/omarchy/plugins/cikr-repos.anyconnect-sso/config.json` |
| Sign-in window cookies | `~/.local/state/omarchy/plugins/cikr-repos.anyconnect-sso/browser/` |
| Log of the last connect | `~/.local/state/omarchy/plugins/cikr-repos.anyconnect-sso/last.log` |
| Tunnel | NetworkManager profile **AnyConnect SSO** |

The VPN session cookie is passed to NetworkManager through a pipe and is never
saved to disk or the profile. The log records page addresses (without query
strings) and results, never tokens or cookies.

## Limitations

- Supports SAML sign-in only. For gateways that take a username and
  password, use `networkmanager-openconnect` directly.
- No client certificates and no HostScan / CSD posture checks.
- IPv4/IPv6 routes and DNS are whatever the gateway pushes, as handled by
  NetworkManager.

## Troubleshooting

When a connect fails, the panel shows the reason and an **Open log** button.
The log lists every page the sign-in window visited, so you can see whether
the identity provider or the gateway rejected the sign-in.

## Remove

```sh
omarchy plugin remove cikr-repos.anyconnect-sso
nmcli connection delete "AnyConnect SSO"
rm -rf ~/.local/state/omarchy/plugins/cikr-repos.anyconnect-sso
```

## License

MIT
