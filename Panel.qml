import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// AnyConnect SSO: one switch for a Cisco AnyConnect gateway that signs in through
// SAML (Microsoft Entra, Okta, …). backend.py does the work; this widget runs it,
// shows its progress lines and edits the server/group settings.
Panel {
  id: root
  moduleName: "cikr-repos.anyconnect-sso"
  ipcTarget: "cikr-repos.anyconnect-sso"
  manageIpc: false

  // disconnected | connecting | connected
  property string vpnState: "disconnected"
  property string server: ""
  property string group: ""
  property string name: ""
  property var missing: []
  property string message: ""
  property bool failed: false

  property bool editing: false
  property var groupOptions: []
  property string groupsError: ""
  property string groupsNote: ""

  readonly property string backend: Qt.resolvedUrl("backend.py").toString().replace("file://", "")
  readonly property string logFile: Quickshell.env("HOME") + "/.local/state/omarchy/plugins/cikr-repos.anyconnect-sso/last.log"
  readonly property bool busy: actionProc.running
  readonly property bool configured: server !== ""
  readonly property bool connected: vpnState === "connected"
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string metaText: {
    if (busy) return "Working…"
    if (connected) return "Connected"
    if (vpnState === "connecting") return "Connecting…"
    return configured ? "Disconnected" : "Not set up"
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function refresh() {
    if (!statusProc.running) statusProc.running = true
  }

  function run(args) {
    if (actionProc.running) return
    failed = false
    message = ""
    actionProc.command = [backend].concat(args)
    actionProc.running = true
  }

  function toggleVpn() {
    if (!configured) return startEdit()
    run([connected || vpnState === "connecting" ? "disconnect" : "connect"])
  }

  function startEdit() {
    nameField.text = name
    serverField.text = server
    groupOptions = group !== "" ? [group] : []
    groupDropdown.value = group
    groupsError = ""
    groupsNote = ""
    editing = true
    if (!opened) open()
    Qt.callLater(function() { nameField.forceActiveFocus() })
  }

  function loadGroups() {
    if (serverField.text.trim() === "" || groupsProc.running) return
    groupsError = ""
    groupsNote = ""
    groupsProc.command = [backend, "groups", serverField.text.trim()]
    groupsProc.running = true
  }

  function saveSettings() {
    run(["configure", serverField.text.trim(), groupOptions.length > 0 ? groupDropdown.value : "", nameField.text.trim()])
    editing = false
  }

  function installMissing() {
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", "omarchy pkg add " + missing.join(" ")])
    close()
  }

  onOpenedChanged: {
    if (opened) {
      refresh()
      Qt.callLater(function() { keyCatcher.forceActiveFocus() })
    } else {
      editing = false
    }
  }

  IpcHandler {
    target: root.ipcTarget
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function connect(): void { if (!root.connected) root.toggleVpn() }
    function disconnect(): void { if (root.connected) root.toggleVpn() }
    function status(): string { return root.metaText }
  }

  Process {
    id: statusProc
    command: [root.backend, "status"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var s = {}
        try { s = JSON.parse(text) } catch (e) { return }
        root.vpnState = s.state || "disconnected"
        root.server = s.server || ""
        root.group = s.group || ""
        root.name = s.name || ""
        root.missing = s.missing || []
      }
    }
  }

  Process {
    id: actionProc
    stdout: SplitParser {
      onRead: function(line) {
        var space = line.indexOf(" ")
        var kind = space > 0 ? line.slice(0, space) : line
        root.message = space > 0 ? line.slice(space + 1) : ""
        root.failed = kind === "ERROR"
      }
    }
    onExited: root.refresh()
  }

  Process {
    id: groupsProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var r = {}
        try { r = JSON.parse(text) } catch (e) { root.groupsError = "Unexpected reply from the server"; return }
        if (r.error) { root.groupsError = r.error; return }
        serverField.text = r.server
        root.groupOptions = r.groups
        if (r.groups.indexOf(groupDropdown.value) === -1) groupDropdown.value = r["default"] || ""
        if (!r.sso) root.groupsError = "This server doesn't offer SSO sign-in" + (r.groups.length > 0 ? " for this group. Pick another group, or" : ";") + " it may need a username and password, which this plugin doesn't support."
        else if (r.groups.length === 0) root.groupsNote = "No group to choose: this server picks it for you. Save to continue."
      }
    }
  }

  Timer {
    interval: 10000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.connected ? "󰦝" : "󰦞"
    foreground: root.connected ? root.barForeground : Qt.darker(root.barForeground, 1.55)
    tooltipText: "VPN: " + root.metaText
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton) root.toggleVpn()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(600))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      // The settings form owns its keys while open.
      blocked: root.editing
      onActivateRequested: root.toggleVpn()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t === "s" || t === "S") root.startEdit()
      }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(12)

        PanelHero {
          id: hero
          width: parent.width
          title: root.name !== "" ? root.name : "VPN"
          meta: root.metaText
          foreground: root.foreground
          fontFamily: root.fontFamily
          iconOpacity: root.connected ? 1.0 : 0.5
          iconComponent: Component {
            Text {
              text: root.connected ? "󰦝" : "󰦞"
              color: root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.display
            }
          }
          trailingControl: Component {
            ToggleSwitch {
              visible: root.configured && root.missing.length === 0
              checked: root.connected || root.vpnState === "connecting" || (root.busy && !root.connected)
              busy: root.busy || root.vpnState === "connecting"
              foreground: hero.foreground
              onToggled: root.toggleVpn()
            }
          }
        }

        Text {
          visible: root.missing.length > 0
          width: parent.width
          text: "Missing packages: " + root.missing.join(", ")
          textFormat: Text.PlainText
          color: root.urgent
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        Text {
          visible: text !== ""
          width: parent.width
          text: root.message
          textFormat: Text.PlainText
          color: root.failed ? root.urgent : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        Row {
          visible: !root.editing
          spacing: Style.space(8)

          Button {
            visible: root.missing.length > 0
            text: "Install packages"
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.installMissing()
          }

          Button {
            text: root.configured ? "Settings" : "Set up"
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.startEdit()
          }

          Button {
            visible: root.failed
            text: "Open log"
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: {
              Quickshell.execDetached(["xdg-open", root.logFile])
              root.close()
            }
          }
        }

        // Settings form
        Column {
          visible: root.editing
          width: parent.width
          spacing: Style.space(8)

          PanelSeparator { width: parent.width }

          Text {
            text: "Name"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          TextField {
            id: nameField
            width: parent.width
            placeholderText: "Work VPN"
            foreground: root.foreground
            font.family: root.fontFamily
            Keys.onEscapePressed: root.editing = false
          }

          Text {
            text: "Server"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          TextField {
            id: serverField
            width: parent.width
            placeholderText: "vpn.example.com"
            foreground: root.foreground
            font.family: root.fontFamily
            onAccepted: root.loadGroups()
            Keys.onEscapePressed: root.editing = false
          }

          Row {
            spacing: Style.space(8)

            Button {
              text: groupsProc.running ? "Loading…" : "Load groups"
              enabled: !groupsProc.running && serverField.text.trim() !== ""
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.loadGroups()
            }
          }

          Dropdown {
            id: groupDropdown
            visible: root.groupOptions.length > 0
            width: parent.width
            label: "Group"
            options: root.groupOptions
            fontFamily: root.fontFamily
            onChanged: function(value) { groupDropdown.value = value }
          }

          Text {
            visible: text !== ""
            width: parent.width
            text: root.groupsNote
            textFormat: Text.PlainText
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Text {
            visible: text !== ""
            width: parent.width
            text: root.groupsError
            textFormat: Text.PlainText
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Row {
            spacing: Style.space(8)

            Button {
              text: "Save"
              enabled: serverField.text.trim() !== ""
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: root.saveSettings()
            }

            Button {
              text: "Cancel"
              bordered: true
              foreground: root.foreground
              fontFamily: root.fontFamily
              onClicked: {
                root.editing = false
                keyCatcher.forceActiveFocus()
              }
            }
          }
        }
      }
    }
  }
}
